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

from . import adapters, bench, evidence, install as install_mod, policy
from .compiler import compile_intent, render
from .contract import Contract, ContractError, contract_path, default_contract
from .freeze import Oracle, freeze
from .util import find_root, read_text

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 3


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve() if getattr(args, "root", None) else find_root()


def _load(root: Path) -> tuple[Contract, Oracle]:
    return Contract.load(root), Oracle.load(root)


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


def cmd_init(args: argparse.Namespace, out: TextIO) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    if args.from_task:
        compiled = compile_intent(read_text(Path(args.from_task)))
        contract = compiled.to_contract()
        for sentence, why in compiled.unenforceable:
            out.write(f'  NOT COMPILED: "{sentence}" -- {why}\n')
        for sentence in compiled.unmatched:
            out.write(f'  NOT COMPILED: "{sentence}" -- not recognised\n')
    else:
        contract = default_contract(args.objective)
    if args.behavior:
        contract.constraints["behavior"]["change"] = "forbidden"
    contract.save(root)
    # Wire the agents *before* freezing, so northstar's own files are part of the
    # baseline rather than showing up as the run's first phantom drift.
    written = [] if args.no_install else install_mod.install(root, args.agent or None)
    oracle = freeze(
        root,
        contract.api_scope,
        capture_behavior=contract.tracks_behavior,
        behavior_command=contract.behavior_command,
    )
    oracle.save(root)

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
    out.write(f"  edit the contract: {root / '.northstar' / 'contract.yaml'}\n")
    return EXIT_OK


def cmd_freeze(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    contract = Contract.load(root)
    oracle = freeze(root, contract.api_scope)
    oracle.save(root)
    out.write(f"northstar: baseline re-frozen ({len(oracle.files)} files, {len(oracle.api)} symbols)\n")
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
    out.write(f"contract:  v{contract.version} ({len(contract.amendments)} signed amendment(s))\n")
    out.write(f"baseline:  {oracle.base_commit or 'snapshot'} @ {oracle.created}\n")
    out.write(f"changed:   {len(changed)} file(s), ~{churn} line(s)\n")
    out.write(f"verdict:   {verdict.decision.value}\n")
    if verdict.judgements:
        out.write(verdict.summary() + "\n")
    return EXIT_BLOCKED if verdict.is_blocking else EXIT_OK


def cmd_amend(args: argparse.Namespace, out: TextIO) -> int:
    """Human-signed, scoped widening. Re-baselines only what is granted."""
    root = _root(args)
    contract = Contract.load(root)
    amendment = contract.amend(args.reason, args.grant, signed_by=args.signed_by)
    contract.save(root)
    evidence.record_amendment(root, amendment.reason, amendment.grants, amendment.version)
    out.write(f"northstar: contract amended to v{contract.version}, signed by {amendment.signed_by}\n")
    for grant in amendment.grants:
        out.write(f"  granted: {grant}\n")
    out.write("  every other invariant stays frozen against the original baseline\n")
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
    for path in install_mod.install(root, args.agent or None):
        out.write(f"northstar: wired {path}\n")
    return EXIT_OK


def cmd_hook(args: argparse.Namespace, out: TextIO) -> int:
    payload = adapters.read_payload(args.stdin or sys.stdin)
    root = Path(payload.get("cwd") or (Path(args.root) if args.root else find_root()))
    return adapters.handle(payload, root)


def cmd_show(args: argparse.Namespace, out: TextIO) -> int:
    root = _root(args)
    out.write(read_text(Path(root) / ".northstar" / "contract.yaml"))
    return EXIT_OK


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="northstar",
        description="Executable intent contracts for coding agents.",
    )
    parser.add_argument("--root", help="project root (default: nearest .northstar, else cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the contract, freeze the baseline, wire the agents")
    init.add_argument("objective", nargs="?", default="", help="what this run is meant to achieve")
    init.add_argument("--from-task", help="compile the contract from a task description file")
    init.add_argument("--behavior", action="store_true", help="also freeze the test suite outcomes")
    init.add_argument("--agent", action="append", choices=["claude", "codex"], help="repeatable")
    init.add_argument("--no-install", action="store_true", help="skip agent wiring")
    init.set_defaults(func=cmd_init)

    comp = sub.add_parser("compile", help="translate a task description into constraints")
    comp.add_argument("text", nargs="?", help="the task description")
    comp.add_argument("--file", help="read the description from a file instead")
    comp.add_argument("--write", action="store_true", help="save the result as the contract")
    comp.set_defaults(func=cmd_compile)

    bn = sub.add_parser("bench", help="run IntentDriftBench, with and without the runtime")
    bn.add_argument("--json", action="store_true")
    bn.add_argument("--output", help="also write the full report here")
    bn.set_defaults(func=cmd_bench)

    fr = sub.add_parser("freeze", help="re-freeze the baseline from the current tree")
    fr.set_defaults(func=cmd_freeze)

    check = sub.add_parser("check", help="verify the tree against the frozen baseline")
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=cmd_check)

    status = sub.add_parser("status", help="restate the objective and the live verdict")
    status.set_defaults(func=cmd_status)

    amend = sub.add_parser("amend", help="human-signed, scoped widening of the contract")
    amend.add_argument("--grant", action="append", required=True, help="kind:identifier, repeatable")
    amend.add_argument("--reason", required=True)
    amend.add_argument("--signed-by", default="human", dest="signed_by")
    amend.set_defaults(func=cmd_amend)

    receipt = sub.add_parser("receipt", help="bind contract, baseline, diff and decisions")
    receipt.add_argument("--json", action="store_true")
    receipt.set_defaults(func=cmd_receipt)

    inst = sub.add_parser("install", help="wire hooks for Claude Code and/or Codex")
    inst.add_argument("--agent", action="append", choices=["claude", "codex"])
    inst.set_defaults(func=cmd_install)

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
    except (ContractError, FileNotFoundError) as exc:
        sys.stderr.write(f"northstar: {exc}\n")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
