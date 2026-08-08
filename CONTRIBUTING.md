# Contributing

Thanks for helping improve NexusAI CLI.

## Set up

```bash
git clone https://github.com/akshat92008/nexus_cli.git
cd nexus_cli
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Before opening a pull request

Run the deterministic gate:

```bash
.venv/bin/python scripts/run_release_gate.py
```

For a faster edit loop:

```bash
.venv/bin/ruff check nexus tests
.venv/bin/pytest
```

Keep changes focused and include regression tests for behavior changes. Do not
commit API keys, model weights, generated verification evidence, virtual
environments, or build artifacts.

Real-model scenarios are opt-in:

```bash
.venv/bin/python scripts/run_release_e2e.py --scenario 01_python_entrypoint
```

They require configured providers or a running `nova_codex` Ollama model and
are not a substitute for deterministic tests.

## Safety-sensitive changes

Changes to path handling, command execution, approval state, package
installation, trust digests, or verification evidence need:

- a regression test for the original failure;
- a test that the intended allowed path still works;
- no silent widening of filesystem or command permissions.
