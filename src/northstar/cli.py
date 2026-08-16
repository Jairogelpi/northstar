"""Command line interface.

`northstar init "<objective>"` is the whole setup: contract, frozen baseline and
agent wiring in one command, no configuration file to hand-write.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import TextIO

from . import (
    __version__,
    adapters,
    bench,
    demo,
    diagnostics,
    evidence,
    livebench,
    policy,
)
from . import install as install_mod
from .authority import (
    Authority,
    IntegrityError,
    interactive_confirmation,
    prompt_new_approval_secret,
)
from .compiler import compile_intent, render
from .contract import Contract, ContractError, contract_path, default_contract
from .freeze import Oracle, freeze
from .util import find_root, read_text

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 3


def _root(args: argparse.Namespace) -> Path:
    candidate = Path(args.root).resolve() if getattr(args, "root", None) else Path.cwd()
    return find_root(candidate)


def _load(root: Path) -> tuple[Contract, Oracle]:
    authority = Authority.open(root, required=True)
    assert authority is not None
    return authority.load()


# ----------------------------------------------------------------- commands


def cmd_compile(args: argparse.Namespace, out: TextIO) -> int:
    """Translate a task description into constraints, showing its own workings."""
    text = read_text(Path(args.file)) if args.file else args.text or ""
    compiled = compile_intent(text)
    out.write(render(compiled))
    if args.write:
        root = Path(args.root).resolve() if args.root else Path.cwd()
        compiled.to_contract().save(root)
        out.write(f"\n# written to {contract_path(root)}\n")
    if compiled.unmatched or compiled.unenforceable:
        out.write(
            f"\n# compiled {compiled.coverage:.0%} of the constraint-like sentences. "
            "Read the NOT COMPILED block before trusting this.\n"
        )
    return EXIT_OK


def cmd_bench(args: argparse.Namespace, out: TextIO) -> int:
    """Run IntentDriftBench: every trajectory, with and without the runtime."""
    with tempfile.TemporaryDirectory() as workdir:
        report = bench.run_suite(bench.default_cases(), Path(workdir))
    if args.json:
        out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    else:
        out.write(bench.render(report) + "\n")
    if args.output:
        bench.save(report, Path(args.output))
        out.write(f"\nwritten to {args.output}\n")
    return EXIT_OK


def cmd_live_bench_validate(args: argparse.Namespace, out: TextIO) -> int:
    study = livebench.load(Path(args.study))
    summary = {
        "study_id": study.id,
        "tasks": len(study.tasks),
        "agents": len(study.agents),
        "pairs": len(livebench.build_plan(study)) // 2,
        "runs": len(livebench.build_plan(study)),
    }
    out.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return EXIT_OK


def cmd_live_bench_plan(args: argparse.Namespace, out: TextIO) -> int:
    study = livebench.load(Path(args.study))
    path = livebench.save_plan(study, Path(args.output))
    out.write(f"northstar: live-agent plan written to {path}\n")
    return EXIT_OK


def cmd_live_bench_preflight(args: argparse.Namespace, out: TextIO) -> int:
    study = livebench.load(Path(args.study))
    report = livebench.preflight(study, check_repositories=args.check_repositories)
    out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return EXIT_OK if report["ready"] else EXIT_ERROR


def cmd_live_bench_run(args: argparse.Namespace, out: TextIO) -> int:
    study = livebench.load(Path(args.study))
    path = livebench.run(study, Path(args.output), resume=args.resume)
    out.write(f"northstar: live-agent artifacts written to {path}\n")
    return EXIT_OK


def cmd_live_bench_packet(args: argparse.Namespace, out: TextIO) -> int:
    packets, mapping = livebench.make_packets(
        Path(args.runs), Path(args.output), Path(args.map)
    )
    out.write(f"northstar: blinded packets written to {packets}\n")
    out.write(f"northstar: private blinding map written to {mapping}\n")
    return EXIT_OK


def cmd_live_bench_analyze(args: argparse.Namespace, out: TextIO) -> int:
    report = livebench.analyse(Path(args.runs), Path(args.annotations), Path(args.map))
    livebench.save_report(report, Path(args.output))
    out.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return EXIT_OK


def cmd_live_bench_report(args: argparse.Namespace, out: TextIO) -> int:
    report = livebench._read_json(Path(args.report))
    path = livebench.save_markdown_report(report, Path(args.output))
    out.write(f"northstar: evidence report written to {path}\n")
    return EXIT_OK


def _contract_summary(contract: Contract) -> list[str]:
    constraints = contract.constraints
    return [
        f"public API changes: {constraints['public_api']['change']}",
        f"runtime dependency additions: {constraints['dependencies']['additions']}",
        f"behaviour changes: {constraints['behavior']['change']}",
        f"unknown tools: {constraints['tools']['unknown']}",
        f"protected paths: {', '.join(constraints['protected_paths']) or 'none declared'}",
    ]


def cmd_init(args: argparse.Namespace, out: TextIO) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    compiled = None
    if args.from_task:
        compiled = compile_intent(read_text(Path(args.from_task)))
        contract = compiled.to_contract()
        for sentence, why in compiled.unenforceable:
            out.write(f'  NOT COMPILED: "{sentence}" -- {why}\n')
        for sentence in compiled.unmatched:
            out.write(f'  NOT COMPILED: "{sentence}" -- not recognised\n')
        if (compiled.unmatched or compiled.unenforceable) and not args.accept_uncompiled:
            raise ContractError(
                "task contains constraints Northstar did not compile; review the lines above, "
                "then rerun with --accept-uncompiled"
            )
    else:
        if not str(args.objective or "").strip():
            raise ContractError("init needs an objective or --from-task TASK.md")
        contract = default_contract(args.objective)
    if args.behavior:
        contract.constraints["behavior"]["change"] = "forbidden"
    out.write("northstar init preview\n")
    out.write(f'  objective: "{contract.objective}"\n')
    out.write(
        "  source: task compiler with sentence provenance\n"
        if compiled is not None
        else "  source: conservative default profile (the objective was not semantically interpreted)\n"
    )
    for line in _contract_summary(contract):
        out.write(f"  invariant: {line}\n")
    if args.dry_run:
        out.write("  dry run: no files, hooks, keys, or authority were written\n")
        return EXIT_OK
    target_authority = Authority.for_root(root)
    if target_authority.path.exists():
        raise IntegrityError(
            f"authority already exists at {target_authority.path}; use an approved re-baseline instead"
        )
    approval_passphrase = prompt_new_approval_secret()
    # Wire the agents *before* freezing, so northstar's own files are part of the
    # baseline rather than showing up as the run's first phantom drift.
    written = [] if args.no_install else install_mod.install(root, args.agent or None)
    oracle = freeze(
        root,
        contract.api_scope,
        capture_behavior=contract.tracks_behavior,
        behavior_command=contract.behavior_command,
    )
    authority = Authority.bootstrap(
        root,
        contract,
        oracle,
        written,
        approval_passphrase=approval_passphrase,
    )

    out.write(f'northstar: contract v{contract.version} for "{contract.objective}"\n')
    out.write(
        f"  baseline frozen: {len(oracle.files)} files, {len(oracle.api)} public symbols, "
        f"{sum(len(v) for v in oracle.dependencies.values())} runtime deps\n"
    )
    if oracle.base_commit:
        out.write(f"  base commit: {oracle.base_commit[:12]}\n")
    if oracle.unknown:
        out.write(f"  UNKNOWN: {len(oracle.unknown)} file(s) could not be parsed; not covered\n")
    for path in written:
        out.write(f"  wired: {Path(path).name}\n")
    if root / ".codex" / "hooks.json" in [Path(path) for path in written]:
        out.write("  Codex: review and trust the project hooks with `/hooks` before relying on them\n")
    out.write(f"  readable mirror: {root / '.northstar' / 'contract.yaml'}\n")
    out.write(f"  sealed authority: {authority.path}\n")
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace, out: TextIO) -> int:
    """Trust an explicitly reviewed v0.1 local bundle and move it to R1 authority."""
    root = _root(args)
    target_authority = Authority.for_root(root)
    if target_authority.path.exists():
        raise IntegrityError(f"authority already exists at {target_authority.path}")
    if not args.accept_existing_state:
        raise IntegrityError("migration requires --accept-existing-state after human review")
    contract = Contract.load(root)
    oracle = Oracle.load(root)
    approval_passphrase = prompt_new_approval_secret()
    wiring = install_mod.discover_wiring(root)
    authority = Authority.bootstrap(
        root,
        contract,
        oracle,
        wiring,
        approval_passphrase=approval_passphrase,
        authenticate_existing_amendments=True,
    )
    out.write(f"northstar: migrated contract v{contract.version} to R1 authority\n")
    out.write(f"  sealed authority: {authority.path}\n")
    out.write("  existing local state was accepted by the human; future tampering fails closed\n")
    if not wiring:
        out.write("  warning: no existing agent wiring found; run `northstar install` from a human terminal\n")
    return EXIT_OK


def cmd_freeze(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    authority = Authority.open(root, required=True)
    assert authority is not None
    contract, _ = authority.load()
    request = {
        "reason": args.reason,
        "grants": ["rebaseline:entire-oracle"],
    }
    passphrase = interactive_confirmation(request)
    authority.validate_approval_passphrase(passphrase)
    oracle = freeze(
        root,
        contract.api_scope,
        capture_behavior=contract.tracks_behavior,
        behavior_command=contract.behavior_command,
    )
    authority.persist(contract, oracle)
    out.write(f"northstar: baseline re-frozen ({len(oracle.files)} files, {len(oracle.api)} symbols)\n")
    out.write(f"  reason: {args.reason}\n")
    return EXIT_OK


def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    contract, oracle = _load(root)
    verdict = policy.evaluate(contract, oracle, root)
    evidence.record(root, "check", "cli", verdict)
    if args.json:
        out.write(json.dumps(verdict.to_dict(), indent=2, sort_keys=True) + "\n")
    else:
        out.write(f"northstar [{verdict.decision.value}]\n{verdict.summary()}\n")
        if verdict.is_blocking:
            out.write("\n" + adapters.block_message(verdict, contract) + "\n")
    return EXIT_BLOCKED if verdict.is_blocking else EXIT_OK


def cmd_status(args: argparse.Namespace, out: TextIO) -> int:
    """Re-anchor after compaction: restate the objective, then the live verdict."""
    root = _root(args)
    contract, oracle = _load(root)
    verdict = policy.evaluate(contract, oracle, root)
    from .checks import changed_files, read_tree

    changed, churn = changed_files(oracle, read_tree(root, contract.api_scope))
    out.write(f'objective: "{contract.objective}"\n')
    out.write(f"contract:  v{contract.version} ({len(contract.amendments)} approved amendment(s))\n")
    out.write(f"baseline:  {oracle.base_commit or 'snapshot'} @ {oracle.created}\n")
    out.write(f"changed:   {len(changed)} file(s), ~{churn} line(s)\n")
    out.write(f"verdict:   {verdict.decision.value}\n")
    if verdict.judgements:
        out.write(verdict.summary() + "\n")
    return EXIT_BLOCKED if verdict.is_blocking else EXIT_OK


def cmd_request(args: argparse.Namespace, out: TextIO) -> int:
    """Create an untrusted request; it cannot change the live contract."""
    root = _root(args)
    authority = Authority.open(root, required=True)
    assert authority is not None
    request_id = authority.create_request(args.reason, args.grant)
    out.write(f"northstar: approval request created: {request_id}\n")
    for grant in args.grant:
        out.write(f"  requested: {grant}\n")
    out.write("  contract unchanged\n")
    out.write(f"  human action (separate terminal): northstar approve {request_id}\n")
    return EXIT_OK


def cmd_approve(args: argparse.Namespace, out: TextIO) -> int:
    """Consume one request through an independent interactive human channel."""
    root = _root(args)
    authority = Authority.open(root, required=True)
    assert authority is not None
    amendment = authority.approve_request(args.request_id, interactive_confirmation)
    evidence.record_amendment(root, amendment.reason, amendment.grants, amendment.version)
    out.write(
        f"northstar: request {args.request_id} approved once; contract v{amendment.version}\n"
    )
    out.write(f"  authenticated signer: {amendment.signed_by}\n")
    for grant in amendment.grants:
        out.write(f"  granted: {grant}\n")
    out.write("  every other invariant remains frozen against the original baseline\n")
    return EXIT_OK


def cmd_receipt(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    contract, oracle = _load(root)
    verdict = policy.evaluate(contract, oracle, root)
    receipt = evidence.build_receipt(root, contract, oracle, verdict)
    path = evidence.write_receipt(root, receipt)
    if args.json:
        out.write(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    else:
        metrics = receipt["metrics"]
        out.write(f"northstar receipt -> {path}\n")
        out.write(f'  objective:      "{receipt["objective"]}"\n')
        out.write(f"  contract:       v{receipt['contract_version']}\n")
        out.write(f"  final verdict:  {verdict.decision.value}\n")
        out.write(f"  steps:          {metrics['steps']}\n")
        out.write(f"  wasted steps:   {metrics['wasted_steps']}\n")
    return EXIT_OK


def cmd_install(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    authority = Authority.open(root)
    if authority is not None:
        request = {"reason": "repair or change agent wiring", "grants": ["wiring:update"]}
        passphrase = interactive_confirmation(request)
        authority.validate_approval_passphrase(passphrase)
        contract, oracle = authority.load(check_wiring=False)
    written = install_mod.install(root, args.agent or None)
    if authority is not None:
        previous = [root / p for p in authority.metadata().get("wiring", [])]
        if args.agent is None or "codex" in args.agent:
            # v0.2.0 briefly used project-local `notify`, a key Codex ignores.
            # The approved repair replaces that wiring with native hooks.json.
            previous = [
                path
                for path in previous
                if path.resolve() != (root / ".codex" / "config.toml").resolve()
            ]
        wiring = list(dict.fromkeys(path.resolve() for path in [*previous, *written]))
        authority.persist(
            contract,
            oracle,
            wiring=wiring,
            check_wiring=False,
        )
    for path in written:
        out.write(f"northstar: wired {path}\n")
    if root / ".codex" / "hooks.json" in [Path(path) for path in written]:
        out.write("northstar: Codex hook trust pending; review `/hooks` in Codex\n")
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace, out: TextIO) -> int:
    """Remove managed adapters while preserving unrelated agent configuration."""
    root = _root(args)
    targets = args.agent or ["claude", "codex"]
    if "all" in targets:
        targets = ["claude", "codex"]
    authority = Authority.open(root)
    contract = oracle = None
    if authority is not None:
        contract, oracle = authority.load()
        verdict = policy.evaluate(contract, oracle, root)
        if verdict.is_blocking:
            raise IntegrityError(
                "refusing to uninstall from a drifting tree; resolve `northstar check` first"
            )
        request = {
            "reason": f"uninstall {' and '.join(targets)} adapter(s)",
            "grants": ["wiring:remove"],
        }
        passphrase = interactive_confirmation(request)
        authority.validate_approval_passphrase(passphrase)

    touched = install_mod.uninstall(root, targets)
    if authority is not None and contract is not None and oracle is not None:
        removed = {
            "claude": {".claude/settings.json", "CLAUDE.md"},
            "codex": {".codex/hooks.json", ".codex/config.toml", "AGENTS.md"},
        }
        remove_paths = set().union(*(removed[target] for target in targets))
        previous = [str(path) for path in authority.metadata().get("wiring", [])]
        remaining = [root / path for path in previous if path.replace("\\", "/") not in remove_paths]
        refreshed = freeze(
            root,
            contract.api_scope,
            capture_behavior=contract.tracks_behavior,
            behavior_command=contract.behavior_command,
        )
        authority.persist(contract, refreshed, wiring=remaining, check_wiring=False)
        out.write("northstar: clean baseline refreshed after authenticated wiring removal\n")
    if touched:
        for path in touched:
            out.write(f"northstar: removed managed content from {path}\n")
    else:
        out.write("northstar: no matching managed adapters were installed\n")
    out.write("northstar: unrelated agent settings were preserved\n")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace, out: TextIO) -> int:
    report = diagnostics.run(_root(args))
    out.write(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.json
        else diagnostics.render(report) + "\n"
    )
    if report["overall"] == "broken" or (args.strict and report["overall"] == "degraded"):
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_demo(args: argparse.Namespace, out: TextIO) -> int:
    report = demo.run()
    out.write(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.json
        else demo.render(report) + "\n"
    )
    return EXIT_OK


def cmd_hook(args: argparse.Namespace, out: TextIO) -> int:
    payload = adapters.read_payload(args.stdin or sys.stdin)
    # Installed hooks bind an explicit checkout root. The payload cwd remains the
    # action cwd for relative path resolution, but cannot change project identity.
    root = Path(args.root).resolve() if args.root else Path(payload.get("cwd") or find_root())
    return adapters.handle(payload, root)


def cmd_show(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    contract, _ = _load(root)
    import yaml

    out.write(yaml.safe_dump(contract.to_dict(), sort_keys=False, allow_unicode=True))
    return EXIT_OK


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="northstar",
        description="Deterministic invariant enforcement for coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--root", help="project root (default: nearest .northstar, else cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the contract, freeze the baseline, wire the agents")
    init.add_argument("objective", nargs="?", default="", help="what this run is meant to achieve")
    init.add_argument("--from-task", help="compile the contract from a task description file")
    init.add_argument("--behavior", action="store_true", help="also freeze the test suite outcomes")
    init.add_argument("--agent", action="append", choices=["claude", "codex"], help="repeatable")
    init.add_argument("--no-install", action="store_true", help="skip agent wiring")
    init.add_argument("--dry-run", action="store_true", help="preview the contract without writing")
    init.add_argument(
        "--accept-uncompiled",
        action="store_true",
        help="continue after reviewing task sentences the compiler could not enforce",
    )
    init.set_defaults(func=cmd_init)

    migrate = sub.add_parser("migrate", help="move an explicitly reviewed v0.1 bundle to R1")
    migrate.add_argument(
        "--accept-existing-state",
        action="store_true",
        help="confirm the local contract and oracle were reviewed",
    )
    migrate.set_defaults(func=cmd_migrate)

    comp = sub.add_parser("compile", help="translate a task description into constraints")
    comp.add_argument("text", nargs="?", help="the task description")
    comp.add_argument("--file", help="read the description from a file instead")
    comp.add_argument("--write", action="store_true", help="save the result as the contract")
    comp.set_defaults(func=cmd_compile)

    bn = sub.add_parser("bench", help="run IntentDriftBench, with and without the runtime")
    bn.add_argument("--json", action="store_true")
    bn.add_argument("--output", help="also write the full report here")
    bn.set_defaults(func=cmd_bench)

    live = sub.add_parser(
        "live-bench",
        help="run paired, blinded evaluations with real Claude Code or Codex agents",
    )
    live_sub = live.add_subparsers(dest="live_command", required=True)

    live_validate = live_sub.add_parser("validate", help="validate a study manifest")
    live_validate.add_argument("study")
    live_validate.set_defaults(func=cmd_live_bench_validate)

    live_plan = live_sub.add_parser("plan", help="write the deterministic paired run plan")
    live_plan.add_argument("study")
    live_plan.add_argument("--output", required=True)
    live_plan.set_defaults(func=cmd_live_bench_plan)

    live_preflight = live_sub.add_parser(
        "preflight", help="verify agents, pinned versions and optional repository commits"
    )
    live_preflight.add_argument("study")
    live_preflight.add_argument("--check-repositories", action="store_true")
    live_preflight.set_defaults(func=cmd_live_bench_preflight)

    live_run = live_sub.add_parser("run", help="execute the study in isolated git clones")
    live_run.add_argument("study")
    live_run.add_argument("--output", required=True)
    live_run.add_argument("--resume", action="store_true")
    live_run.set_defaults(func=cmd_live_bench_run)

    live_packet = live_sub.add_parser(
        "packet", help="build outcome packets with the study arm blinded"
    )
    live_packet.add_argument("runs")
    live_packet.add_argument("--output", required=True, help="packet directory")
    live_packet.add_argument("--map", required=True, help="private evaluation-to-run map")
    live_packet.set_defaults(func=cmd_live_bench_packet)

    live_analyze = live_sub.add_parser(
        "analyze", help="combine independent annotations and process evidence"
    )
    live_analyze.add_argument("runs")
    live_analyze.add_argument("--annotations", required=True)
    live_analyze.add_argument("--map", required=True)
    live_analyze.add_argument("--output", required=True)
    live_analyze.set_defaults(func=cmd_live_bench_analyze)

    live_report = live_sub.add_parser(
        "report", help="render a claim-bounded Markdown report from canonical analysis JSON"
    )
    live_report.add_argument("report")
    live_report.add_argument("--output", required=True)
    live_report.set_defaults(func=cmd_live_bench_report)

    fr = sub.add_parser("freeze", help="human-confirmed full re-baseline")
    fr.add_argument("--reason", required=True, help="why the entire baseline is changing")
    fr.set_defaults(func=cmd_freeze)

    check = sub.add_parser("check", help="verify the tree against the frozen baseline")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    status = sub.add_parser("status", help="restate the objective and the live verdict")
    status.set_defaults(func=cmd_status)

    for name in ("request", "amend"):
        amend = sub.add_parser(
            name,
            help="request a scoped widening without changing the contract"
            + (" (deprecated alias)" if name == "amend" else ""),
        )
        amend.add_argument("--grant", action="append", required=True, help="kind:identifier, repeatable")
        amend.add_argument("--reason", required=True)
        amend.set_defaults(func=cmd_request)

    approve = sub.add_parser("approve", help="approve one request from a separate interactive terminal")
    approve.add_argument("request_id")
    approve.set_defaults(func=cmd_approve)

    receipt = sub.add_parser("receipt", help="bind contract, baseline, diff and decisions")
    receipt.add_argument("--json", action="store_true")
    receipt.set_defaults(func=cmd_receipt)

    inst = sub.add_parser("install", help="wire hooks for Claude Code and/or Codex")
    inst.add_argument("--agent", action="append", choices=["claude", "codex"])
    inst.set_defaults(func=cmd_install)

    uninstall = sub.add_parser(
        "uninstall", help="remove Northstar adapters without touching unrelated settings"
    )
    uninstall.add_argument("--agent", action="append", choices=["all", "claude", "codex"])
    uninstall.set_defaults(func=cmd_uninstall)

    doctor = sub.add_parser("doctor", help="verify authority, wiring, agents and hook activity")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--strict", action="store_true", help="treat warnings as a non-zero exit")
    doctor.set_defaults(func=cmd_doctor)

    demo_parser = sub.add_parser("demo", help="run a disposable end-to-end decision demo")
    demo_parser.add_argument("--json", action="store_true")
    demo_parser.set_defaults(func=cmd_demo)

    hook = sub.add_parser("hook", help="agent hook entrypoint (reads JSON on stdin)")
    hook.set_defaults(func=cmd_hook, stdin=None)

    show = sub.add_parser("show", help="print the contract")
    show.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out)
    except IntegrityError as exc:
        sys.stderr.write(f"northstar [DENY]\n[INTEGRITY_FAILURE] {exc}\n")
        return EXIT_BLOCKED
    except (ContractError, FileNotFoundError, livebench.StudyError) as exc:
        sys.stderr.write(f"northstar: {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
