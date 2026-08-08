# NexusAI CLI

NexusAI is an open-source, multi-provider coding CLI focused on verification, worktree safety, and extensibility. A hosted model plans and reviews difficult work; local Nova V11 executes suitable atomic changes through Ollama. Nexus owns repository understanding, permissions, workspaces, tools, tests, repair, evidence, budgets, rollback, and recovery.

Version 3.8.3 is the lifecycle/provenance remediation release built on the 3.8.0 repository-intelligence offensive. It replaces fixed context expansion and near-duplicate retries with evidence-directed graph expansion, operational hard-task profiles, structural replanning, concurrency triage, and a persistent completion ledger. Native command isolation now fails closed by default, while trusted host execution is a separate explicit capability. A strict three-way superiority gate compares Nexus, the same underlying model without Nexus, and real Claude Code on private unseen repositories; no smoke or self-authored result can authorize a competitive claim.

- requests become acceptance criteria and dependency-aware execution
  contracts with risk, file scope, checks, retries, and budgets;
- the Engineering Brain builds a repository-aware contract containing decisive files, callers, related tests, architecture constraints, explicit non-goals, and a plan-critic decision before mutation;
- persistent task memory and long-horizon phase state are atomically written, HMAC-authenticated with repository-external keys, sequence checked, and rejected on signature failure or stale concurrent writes;
- a typed constraint compiler turns natural-language prohibitions into executable file, schema, dependency, API, authentication, and compatibility policies; surgical editing enforces those policies, optimistic concurrency, changed-file ceilings, changed-line ceilings, and authority-separated evidence-backed scope expansion beneath the model layer;
- semantic acceptance rejects model prose and stale checks, then requires criterion-specific typed evidence, exact source revisions, verified mutations, external behavioral checks, scope compliance, and independent review;
- the matched benchmark gate compares the identical task, model, content-hashed repository revision, and sealed budget directly versus Nexus and measures verified uplift, false completion, regressions, budget compliance, cost, tokens, latency, and intervention;
- a superiority-campaign preflight rejects duplicated repositories/tasks, model-identity mismatches, missing private oracles, placeholder environment declarations, and underpowered category coverage before any paid benchmark run;
- modifying sessions open an isolated Git worktree, or a persistent temporary
  copy for non-Git projects, unless the user explicitly opts out;
- commands support shell-free argv execution, filtered environments, timeouts,
  output ceilings, native Linux/macOS isolation, and fail-closed isolation;
- persistent RepoGraph indexing covers symbols, imports, callers, tests,
  routes, models, configuration, ownership, frameworks, and Git relevance;
- LSP clients and optional Tree-sitter parsing provide precise navigation;
- Nova V11 and hosted candidates use strict patch contracts, temporary-tree
  replay, compiler checks, bounded repair, escalation, and independent review;
- verification can combine lint, types, tests, build, security, read-only
  database checks, HTTP contracts, and optional browser workflows;
- every run persists request, plan, task, model, tool, cost, patch, test,
  checkpoint, state, and final-report artifacts;
- interrupted work resumes the persisted plan from its latest checkpoint,
  preserving completed steps and retrying only unfinished work;
- SDK contracts, skills, hooks, plugins, subagents, MCP, CI mode, issue
  solving, and a versioned benchmark harness are included.

## Release status

Nexus 3.8.3 is **launch-ready for repository analysis, planning, and supervised Verified Repair with mandatory human diff review** when installed under `release-constraints.txt`, and `nexus deploy check --deep` and `nexus sandbox qualify` pass on the target host. Autonomous generated-command execution remains fail-closed until the exact deployment host passes native filesystem, process, and network isolation qualification. It does not claim universal Claude Code parity or unattended production autonomy. The included three-way duel and superiority gate make that goal measurable. A better-than-Claude-Code claim is emitted only after real Claude Code executions on private unseen repositories satisfy every predeclared quality, safety, cost, latency, intervention, and provenance threshold.

See [CAPABILITIES.md](CAPABILITIES.md) for the measured capability boundary,
[ROADMAP.md](ROADMAP.md) for the complete long-term product contract, and
[SECURITY.md](SECURITY.md) for the vulnerability-reporting policy.

## Requirements

- Python 3.10 or newer
- Git for project-aware workflows
- At least one backend:
  - `NVIDIA_API_KEY`, `GROQ_API_KEY`, or `OPENROUTER_API_KEY`; or
  - Ollama with the `nova_codex` Nova 3B v11 model installed

## Install

Install directly from GitHub with `pipx`:

```bash
pipx install git+https://github.com/akshat92008/nexus_cli.git
nexus --version
nexus --doctor
```

Or create an isolated environment from a checkout:

```bash
git clone https://github.com/akshat92008/nexus_cli.git
cd nexus_cli
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/nexus --doctor
```

The distribution name is `nexusai-cli`; the installed command is `nexus`.
The similarly named `nexusai` project on PyPI is unrelated.

Optional deterministic adapters:

```bash
pip install "nexusai-cli[intelligence]"  # Tree-sitter language pack
pip install "nexusai-cli[browser]"       # Playwright API
playwright install chromium              # Browser executable
```

## Configure a backend

For hosted planning and execution, export one or more provider keys:

```bash
export NVIDIA_API_KEY="nvapi-your-key"
# Optional production fallbacks:
export GROQ_API_KEY="gsk-your-key"
export OPENROUTER_API_KEY="sk-or-your-key"
```

Nexus also reads `.env` from the current project, the Nexus checkout,
`~/.config/nexus/.env`, or `~/.nexusai/.env`. An existing process environment
value always wins. Avoid passing secrets on the command line.

Set `NEXUS_DISABLE_NETWORK=1` to fail closed before hosted-provider, web-fetch,
or web-search transports are opened. The deterministic release and stress
gates enable this kill switch and remove provider credentials automatically.

For local Nova 3B v11, install [Ollama](https://ollama.com), obtain the Nova v11
model artifact from the
[Nova repository](https://github.com/akshat92008/nova-1.5b), and build the
production tag using its v11 Modelfile:

```bash
cd nova-1.5b/legacy/v11
# Place the v11 model artifact named codex_nova beside the Modelfile first.
ollama create nova_codex -f modelfiles/Modelfile.nova_codex
ollama run nova_codex "Create hello.py that prints hello"
```

The model artifact is intentionally not bundled in the Python wheel. Nexus
communicates with it through Ollama and packages the parser, guardrails, retry,
and escalation runtime needed for Nova v11 output.

## Use

```bash
# Interactive hosted mode
nexus

# Flagship bounded repair with a hard rupee budget and Nexus Proof receipt
nexus fix \
  "Fix the refresh-token regression without changing the database schema" \
  --budget-inr 20 --model auto --proof

# One prompt with structured automation-friendly output
nexus run --prompt "add a health endpoint and tests" --mode autonomous --output json

# Local-only Nova v11
nexus --model nova_codex

# Hosted planner with Nova available for atomic subtasks
nexus --model glm-5.2

# Read-only planning
nexus run --prompt "plan the authentication migration" --mode plan

# Review-only and autonomous execution
nexus run --prompt "implement the feature and test it" --mode review
nexus run --prompt "implement the feature and test it" --mode autonomous

# Hard logical-call, physical-attempt, token, and configured-cost limits
nexus --max-hosted-calls 8 --max-provider-attempts 12 \
  --max-prompt-tokens 100000 --max-completion-tokens 30000 \
  --max-cost-usd 1.00 --input-price-per-million 0.50 \
  --output-price-per-million 1.50 \
  "fix the failing integration tests"

# Local, budget, quality, and CI policy presets
nexus run --prompt "add regression tests" --local-only
nexus run --prompt "fix the bug" --prefer-cheap
nexus run --prompt "review authentication" --quality maximum
nexus run --prompt "fix failing checks" --mode ci --output json

# Browser interface
nexus --web --port 3000
```

Useful startup commands:

```bash
nexus --version
nexus --doctor
nexus --list-models
nexus --help

# Inspect the repository-aware contract without invoking a model
nexus intelligence inspect "Fix calculator.py without changing README.md" --strict --json

# Fast artifact and host smoke check
nexus deploy check --mode review --json

# Execute the real installed repair/adversarial gate and qualify only the supervised deployment scope
nexus deploy check --mode review --deep --output deploy-readiness.json --json

# Execute one real offline repair plus four adversarial truth-integrity scenarios
nexus benchmark offline-reliability --output offline-reliability.json

# Enforce a matched direct-versus-Nexus evaluation contract
nexus benchmark compare-matched --direct direct.json --nexus nexus.json

# Run a blind matched-repository Nexus-versus-Claude duel manifest
nexus benchmark duel --manifest competitive.json --output duel-report.json

# Produce behavioral sandbox evidence for this exact host
nexus sandbox qualify --workspace . --output sandbox-qualification.json

# Run the sealed three-way private campaign, sign it in the evaluator environment,
# then fail closed unless every category and aggregate threshold is satisfied.
nexus benchmark duel --manifest private-superiority.json --output unsigned-report.json
python scripts/sign_superiority_report.py --report unsigned-report.json \
  --private-key /secure/evaluator-ed25519.key --evaluator-id independent-lab \
  --output signed-report.json
nexus benchmark superiority-gate --report signed-report.json
```

An optional currency ceiling requires explicit prices so Nexus never invents
provider pricing:

```bash
nexus --max-cost-usd 1.00 \
  --input-price-per-million 0.50 \
  --output-price-per-million 1.50 \
  "implement and verify the endpoint"
```

### Approval model

The default permission mode does not apply file edits immediately. Nexus
returns a pending edit with an exact diff:

- `/apply <id>` applies that reviewed proposal;
- `/reject <id>` discards it;
- `/edit-pending <id> <file>` replaces it with reviewed file content;
- `/confirm <id>` executes one exact dangerous or out-of-scope operation;
- `/cancel <id>` discards that operation.

Use `--permission-mode acceptEdits` only in a workspace where automatic edits
are acceptable. Safety blocks and out-of-scope confirmation still apply.

Autonomous, `acceptEdits`, quality, budget, local-only, and CI presets fail
closed if the host cannot provide a native OS sandbox (`bubblewrap` on Linux
or `sandbox-exec` on macOS). These presets expose shell-free `run_process`
instead of the compatibility `run_command` shell. This prevents a command
that merely looks safe from reading outside the authorized workspace.

### OS Sandbox Support

Nexus relies on kernel-level sandboxing for autonomous workflows (like the `autonomous` mode and CI presets) to enforce strict network and file-system boundaries for generated code execution:

See the **OS Sandbox Setup** section below for installation instructions before
running autonomous or editing workflows.

## OS Sandbox Setup

Nexus uses kernel-level sandboxing for autonomous and editing workflows. Run
`nexus --doctor` to check your sandbox status. Without a native backend, modes
that require isolation (`review`, `workspace`, `autonomous`, `quality`, `budget`,
`local-only`, `ci`) will fail closed rather than run with reduced safety.

**Linux** — install `bubblewrap` before first use:

```bash
# Debian / Ubuntu
sudo apt-get install bubblewrap

# Fedora / RHEL
sudo dnf install bubblewrap

# Arch
sudo pacman -S bubblewrap
```

**macOS** — `sandbox-exec` ships with macOS. No additional installation is
needed. The `nexus --doctor` command will confirm it is operational.

**Windows** — there is no integrated native sandbox backend on Windows.
Autonomous, quality, budget, local-only, and CI modes will fail closed with
a clear error. Plan mode and direct `!command` execution work without a
sandbox. For full capability on Windows, use WSL2 with bubblewrap installed.

After installation, verify:

```bash
nexus --doctor
# Sandbox: [✓] native backend: bubblewrap   (Linux)
# Sandbox: [✓] native backend: sandbox-exec  (macOS)
```

## Models

Hosted model IDs are drawn from the NVIDIA NIM catalog. Provider availability
can change, so `nexus --list-models` is the runtime source of truth.

| Nexus key | Provider model | Role |
|---|---|---|
| `glm-5.2` | `z-ai/glm-5.2` | Default hosted planner |
| `deepseek-v4` | `deepseek-ai/deepseek-v4-pro` | Long-horizon reasoning and coding |
| `deepseek-flash` | `deepseek-ai/deepseek-v4-flash` | Fast coding and tools |
| `kimi` | `moonshotai/kimi-k2.6` | Long-horizon coding |
| `minimax` | `minimaxai/minimax-m3` | Reasoning, coding, and tools |
| `qwen` | `qwen/qwen3.5-397b-a17b` | Software engineering |
| `nemotron` | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | Reasoning and function calling |
| `llama` | `meta/llama-3.3-70b-instruct` | General coding and tools |
| `nova_codex` | Ollama `nova_codex` | Local Nova 3B v11 |

If NVIDIA is unavailable and `GROQ_API_KEY` is configured, Nexus uses Groq
production model IDs only. `openai/gpt-oss-120b` is the default high-capability
fallback, followed by production Llama and GPT-OSS alternatives.

## Verification and evidence

Nexus does not treat an assistant's statement as proof of completion. It
records file mutations, command results, routing decisions, compiler checks,
and package-registry decisions. `/verify` re-reads recent file artifacts and
re-runs safe verification commands; `/verify project` runs detected project
checks.

For contributors, the deterministic release gate is:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,browser]"
.venv/bin/python scripts/run_release_gate.py
```

The deterministic gate never spends provider credits. Real hosted-provider
qualification is explicitly cost-gated through
`scripts/run_live_provider_gate.py` and the manual GitHub workflow.

The public benchmark manifest is versioned and shell-free:

```bash
nexus benchmark --manifest benchmarks/core.json --dry-run
nexus benchmark --manifest benchmarks/core.json \
  --output benchmarks/results/nexus-3.4.0.json

# Validate the large single-prompt product contract without spending credits
nexus benchmark --manifest benchmarks/long_horizon.json --dry-run

# Run real-provider qualification and preserve redacted attempt evidence
python scripts/run_live_provider_gate.py --allow-cost \
  --manifest benchmarks/long_horizon.json --trials 3
```

Dry runs validate manifests only. Their `VALID` tasks are reported under
`manifest_valid_tasks` and `not_executed_tasks`; they never increase execution
`passed` or `pass_rate`. Only real runs that satisfy external quality gates can
produce `PASSED` results.

## Durable runs and recovery

Every turn is saved under the Nexus state directory with `request.json`,
`plan.json`, `tasks.json`, `events.jsonl`, `model_calls.jsonl`,
`tool_calls.jsonl`, `costs.json`, `patches/`, `tests/`, `checkpoints/`,
`state.json`, and `final_report.json`.

```bash
nexus runs
nexus inspect <run-id>
nexus replay <run-id>
nexus resume <run-id>
nexus rollback <run-id>
```

Resume reloads the same workspace, original objective, saved plan, completed
task state, and latest checkpoint. Automated long-horizon benchmarks use this
mechanism to continue one initial product prompt across bounded attempts; they
do not inject follow-up product instructions.

Massive builds also persist a product specification, architecture decisions,
subsystem contracts, integration/deployment gates, and failure-driven plan
revisions. File/interface summaries and dependency impact are cached across
restarts so later turns do not have to reconstruct the entire system from raw
file clips.

## Security Limitations

The safety layer reduces risk, but is not a bulletproof sandbox. Note the following limitations:
- **Restricted-Process Mode is not full isolation**: The `restricted-process` mode filters environment variables and uses basic controls, but it is **not equivalent to container or native kernel isolation**. It lacks strong CPU, memory, and process-count limits.
- **Windows Support**: Windows does not have a native kernel sandbox isolation implementation in Nexus.

## Built-in tools

Nexus exposes 38 built-in tools across file editing, repository intelligence,
LSP/Tree-sitter navigation, shell-free and managed processes, Git, web
retrieval, API checks, browser workflows, database integrity, and bounded
security analysis. Tool execution is wrapped by scope, policy, approval,
trust, package, budget, run-state, and evidence layers.

Key interactive commands include `/models`, `/project`, `/pending`,
`/permissions`, `/trust`, `/changes`, `/undo`, `/verify`, `/history`,
`/compact`, `/run-status`, `/rollback-run`, and `/help`.

## Development

```bash
.venv/bin/ruff check nexus tests
.venv/bin/pytest
.venv/bin/python -m build
```

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change.
Release history is in [CHANGELOG.md](CHANGELOG.md). The launch-versus-target
boundary is maintained in [ROADMAP.md](ROADMAP.md).

## License

MIT. See [LICENSE](LICENSE).
