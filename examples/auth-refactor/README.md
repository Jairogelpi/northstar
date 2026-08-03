# Example: refactor authentication without changing the public API

Reproduces the full loop in about a minute: freeze, block, ask, sign, receipt.

```bash
mkdir -p demo/src/auth demo/tests && cd demo

printf '[project]\nname = "demo"\ndependencies = ["requests"]\n' > pyproject.toml
printf 'def login(user: str, password: str) -> bool:\n    return bool(user and password)\n' > src/auth/service.py
printf 'from auth.service import login\n\n\ndef test_login():\n    assert login("a", "b")\n' > tests/test_auth.py

northstar init "refactor authentication without changing the public API"
```

```
northstar: contract v1 for "refactor authentication without changing the public API"
  baseline frozen: 7 files, 2 public symbols, 1 runtime deps
  wired: settings.json, CLAUDE.md, AGENTS.md, config.toml
```

## 1. The agent tries to edit the tests

This is the pre-tool gate, so the file is never written.

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"tests/test_auth.py"}}' | northstar hook
```

```
  [DENY] protected_path: tests/test_auth.py is protected by `tests/**`
      grant needed: protected_path:tests/test_auth.py
```

Exit code `2` — Claude Code blocks the call and shows this to the model.

## 2. The agent breaks the API and adds a dependency

Both edits look locally reasonable. Neither is checked against the previous step —
they are checked against the baseline.

```bash
printf 'def login(user: str, password: str, tenant: str) -> bool:\n    return bool(user and password)\n' > src/auth/service.py
printf '[project]\nname = "demo"\ndependencies = ["requests", "httpx"]\n' > pyproject.toml
northstar check
```

```
[DENY] public_api: signature changed: (user: str, password: str) -> bool -> (user: str, password: str, tenant: str) -> bool
[DENY] dependency: runtime dependency added in pyproject.toml
```

Note the tests still pass. Nothing in the suite covers the new argument. A test-only
gate sees a green run.

## 3. The human signs one of them, and only one

```bash
northstar amend --grant "public_api:src/auth/service.py::login" --reason "multi-tenant agreed"
northstar check
```

```
[ALLOW] public_api: ... -- signed in amendment v2: multi-tenant agreed
[DENY]  dependency: runtime dependency added in pyproject.toml
```

The signature re-baselined `login` and nothing else. One exception did not become a
general amnesty.

## 4. The agent tries to sign for itself

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"northstar amend --grant dependency:httpx --reason needed"}}' | northstar hook
```

```
  [DENY] governance: amendments are signed by the human, not by the agent;
         stop and state which grant you need

This refusal is not amendable. Take another route.
```

## 5. The receipt

```bash
northstar amend --grant "dependency:httpx" --reason "async client agreed"
northstar receipt
```

```
  objective:      "refactor authentication without changing the public API"
  contract:       v3
  final verdict:  ALLOW
  steps:          8
  wasted steps:   0
```

The run ended somewhere the original contract forbade — and every step of how it got
there was signed, reasoned and recorded.
