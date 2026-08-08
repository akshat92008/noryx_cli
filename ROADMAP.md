# Nexus roadmap

## Nexus 3.7.0 Cognitive Reliability milestone

Implemented locally: hypothesis-driven deliberation, graph-propagated context selection, enforceable multi-file completion, evidence-delta recovery, blind duel infrastructure, behavioral sandbox qualification, and shared-process lifecycle cleanup.

Promotion gates still external: execute at least 50 private unseen matched tasks against a real Claude Code installation and a same-model minimal harness; publish signed Linux/macOS/Windows sandbox artifacts; demonstrate repeatability, bounded intervention, cost, latency, regression, and false-success thresholds.

## Nexus 3.6.0 Truth Integrity milestone

The P0 correctness contract is implemented: no stale repository cache reuse, no prose-based acceptance, no unsupported scope expansion, typed user prohibitions, authenticated engineering state, optimistic concurrency, and executable installed-artifact reliability evidence.

The next milestone is external performance qualification rather than another governance subsystem: hidden real-provider tasks, cross-platform execution, repeatability, and blind matched comparisons. Autonomous production remains blocked until those thresholds are met.


## Nexus 3.5.0 Engineering Brain milestone

- Enforce repository-aware task contracts before mutation.
- Preserve authenticated task, phase, and failure state plus corruption-evident change logs across long runs.
- Make surgical scope and semantic acceptance runtime authorities beneath the model.
- Compare identical models directly versus Nexus before making uplift claims.
- Keep release-candidate and parity promotion blocked until real-provider and cross-platform evidence passes.

# Nexus CLI product contract and roadmap

Updated: 2026-08-06

## North star

Nexus CLI is an open-source, multi-provider coding CLI focused on verification, worktree safety, and extensibility.

The long-term product promise is:

> Give Nexus a goal. Nexus understands the repository, creates a plan,
> implements the change, verifies the result, repairs failures, and returns a
> reviewable working diff.

Nexus is the product. Nova V11 is one low-cost local execution worker within
the product. Hosted models may plan, review, or handle difficult subtasks, but
no model is the source of truth.

## Non-negotiable engineering principles

1. **Models propose; Nexus verifies.** The repository, compiler, tests, static
   analysis, and runtime evidence determine success.
2. **No success without evidence.** Generated code alone is never a verified
   result.
3. **Use the cheapest capable model.** Deterministic tools and local models
   handle bounded work; stronger models handle architecture and escalation.
4. **Persist important state.** Requests, plans, patches, commands, evidence,
   costs, failures, approvals, and checkpoints must survive process restarts.
5. **Work inside controlled boundaries.** Filesystem, command, network,
   package, Git, database, and deployment authority must be explicit.
6. **Make uncertainty visible.** Results must be labelled `VERIFIED`,
   `PARTIALLY VERIFIED`, `UNVERIFIED`, `BLOCKED`, `FAILED`, or
   `AWAITING APPROVAL`.

## Version 3.8 implementation boundary

`Implemented` means the package contains the behavior and deterministic tests
exercise its critical path. `Host-dependent` means Nexus contains the adapter
and correctly reports availability, but the external compiler, language
server, browser, service, database, credentials, or sandbox must exist on the
machine. No unavailable adapter is presented as verified.

| Area | Status | Version 3.8 evidence boundary |
|---|---|---|
| Installable CLI | Implemented | Self-contained wheel, `nexus` entry point, version and doctor smoke checks |
| Hosted + local model routing | Implemented | Hosted planner paths plus local Ollama `nova_codex` executor |
| Nova V11 task contract | Implemented | Bounded tasks, `nova.patch.v1`, legacy parser compatibility, path/action/schema checks |
| Candidate isolation | Implemented | Nova output is replayed and validated in temporary candidate directories |
| Workspace isolation | Implemented | Automatic Git worktrees and persistent non-Git copies, with explicit opt-out |
| File approvals | Implemented | Exact diff preview, apply, reject, replacement, and one-use confirmations |
| Command safety | Implemented / host-dependent | Shell-free argv tools, filtered environment, limits, native sandbox capability probes, visible restricted fallback, and fail-closed mode |
| Package safety | Implemented | Registry checks; missing packages block; registry outages require explicit approval |
| Syntax and compiler checks | Implemented | Python, JSON, Node, Go, C, C++, and Rust where local compilers exist |
| Behavioral verification | Implemented / host-dependent | API contracts, SQLite integrity, migration risk, security patterns, and optional Playwright workflows |
| Evidence trail | Implemented | Persistent JSONL records, hashes, command exit codes, and re-verification |
| Recovery | Implemented | Canonical state, verified checkpoints, task-aware continuation, inspection, replay, and rollback commands |
| Repository intelligence | Implemented / host-dependent | Persistent RepoGraph plus routes, models, config, ownership, Git relevance, LSP clients, Tree-sitter fallback, and task-local context |
| Extensions | Implemented | Versioned provider/tool/policy protocols, skills, hooks, plugins, subagents, and stdio MCP |
| Headless operation | Implemented | `nexus run`, JSON/JSONL, CI policy preset, meaningful process status, and diagnostics |
| Web interface | Implemented | Loopback-only Starlette/WebSocket UI with automatic workspace and sensitive-file controls |
| Cost controls | Implemented | Separate logical hosted-call and physical provider-attempt ceilings, token and configured-currency limits, plus attempt-level persisted usage reports |
| Run contract and final report | Implemented | Complete canonical artifact layout and transparent objective, checks, permissions, network, provider, cost, assumption, and risk fields |
| Public benchmarks | Implemented | Versioned manifest, disposable copies, typed verification, scope/cost/retry metrics, and JSON output |

## Phase 1 — Reliable core

Objective: make small and medium repository changes safely and consistently.

Version 3.0 integrates the complete reliable-core contract:

- clean package installation;
- a packaged Nova V11 adapter instead of a neighboring-checkout dependency;
- safe path handling and workspace confinement;
- guarded tool execution and exact approvals;
- candidate replay and compiler validation;
- evidence-backed mutations and commands;
- CI, build, and isolated-wheel smoke tests;
- provider diagnostics and machine-readable exit behavior.

The legacy Nova V11 Markdown parser remains solely for compatibility with the
existing trained model. New hosted execution uses `nova.patch.v1`. Native
sandbox enforcement is host-dependent and never silently inferred from the
presence of a binary.

## Phase 2 — Verified agent

Objective: complete well-defined features and bug fixes with measurable
evidence.

Required capabilities:

- translate requests into explicit acceptance criteria;
- create dependency-aware task DAGs;
- attach permitted files, budgets, checks, retry policy, and risk to every task;
- discover targeted validation commands from project metadata and CI;
- classify failures and generate minimal repairs;
- rerun the smallest relevant validation after each repair;
- escalate after a bounded retry ceiling;
- use an independent reviewer for correctness, scope, architecture, tests, and
  security;
- enforce per-run token and currency ceilings;
- report every satisfied, skipped, blocked, and unverified criterion.

Version 3.0 makes this contract drive dependency gates, task checkpoints,
failure classification, focused repair ceilings, escalation, independent
review, deterministic checks, and final criterion status. Reproducibility is
measured through versioned benchmark manifests; provider performance remains
an empirical result rather than a product guarantee.

## Phase 3 — Repository intelligence

Objective: understand large repositories without sending the entire codebase
to a model.

Required capabilities:

- persistent RepoGraph connecting files, symbols, references, imports, tests,
  routes, database models, configuration, ownership, and Git changes;
- Tree-sitter and native AST indexing;
- long-lived Language Server Protocol clients where appropriate;
- symbol and caller lookup;
- dependency and test-impact graphs;
- Git-aware relevance ranking;
- task-local context selection, deduplication, caching, and bounded logs;
- incremental index updates after every accepted patch.

Version 3.0 implements this through RepoGraph v2, native Python AST analysis,
conservative multi-language parsing, Git/CODEOWNERS relevance, persistent LSP
clients, and optional Tree-sitter language packs.

## Phase 4 — One-prompt engineering workflows

Objective: implement complex features and applications milestone by milestone.

Required capabilities:

- clarify only high-value unknowns and propose safe defaults;
- turn broad goals into a product specification and architecture;
- scaffold and implement backend, frontend, database, authentication,
  integrations, and tests incrementally;
- launch and inspect services;
- validate APIs, database mutations, browser behavior, console errors,
  accessibility, and responsive states;
- detect destructive migrations and require elevated approval;
- perform security checks without overstating audit coverage;
- prepare deployment configuration and documentation;
- return a working diff plus evidence and remaining risks.

This phase does not mean one model generates an application in one response.
Nexus manages a checkpointed software-development lifecycle across models and
deterministic tools.

Version 3.0 supplies that lifecycle, including API, database, browser, security,
and service-process adapters. The actual breadth of a product build depends on
the configured models and locally available runtimes, and skipped checks remain
visible in the final report.

## Phase 5 — Platform ecosystem

Objective: make Nexus an extensible engineering platform.

Required capabilities:

- stable provider, tool, policy, skill, hook, and plugin SDKs;
- versioned MCP integration;
- issue-to-pull-request workflows;
- IDE and CI integrations;
- headless policy profiles;
- extension signing, trust, compatibility, and discovery;
- public benchmark datasets, harnesses, results, and regression dashboards.

Version 3.0 ships the provider/tool/policy SDK contracts, existing skill/hook/
plugin/subagent/MCP systems, CI and issue entry points, and a reproducible
benchmark manifest/result format. Dedicated vendor-hosted IDE, mobile, and
cloud-control products are outside this open CLI runtime.

## Long-term operating modes

- **Plan:** inspect, search, estimate, and plan without modifying files.
- **Review:** propose patches and commands, then wait for approval.
- **Workspace:** edit and verify inside an isolated Git worktree.
- **Autonomous:** execute only pre-approved policies and stop at protected
  actions.
- **Local-only:** use Nova V11 or another local model with cloud providers
  disabled.
- **Maximum quality:** use stronger planning and review models plus broader
  verification.
- **Budget:** prefer deterministic tools and low-cost models, escalating only
  when necessary.

## Final report contract

Every completed run should report:

- objective and acceptance criteria;
- work completed and files changed;
- tests and checks executed with real outcomes;
- checks skipped or unavailable;
- dependencies added;
- permissions, network calls, and model providers used;
- token and monetary cost;
- assumptions and remaining risks;
- one evidence-based verification status.

The system must never convert missing credentials, unavailable services,
registry outages, or skipped checks into a false success.

## Reliability and benchmark targets

Nexus will optimize for:

- verified task-completion rate;
- regression-free completion rate;
- false-success rate;
- cost per verified task;
- unnecessary file-touch rate;
- successful resume rate;
- command failure rate;
- human intervention rate;
- average repair attempts;
- consistency across repeated runs.

The primary KPI is:

> How often does Nexus declare success when every acceptance criterion is
> genuinely satisfied?

The false-success rate should approach zero. Public capability claims must be
tied to a Nexus version and reproducible evidence.

## End-state flow

```text
Simple user request
        ↓
Deep repository understanding
        ↓
Structured engineering plan
        ↓
Cost-efficient model orchestration
        ↓
Safe code execution
        ↓
Automatic testing and repair
        ↓
Verified working result
```

This contract describes the integrated Nexus 3.0 runtime. It is not a claim
that external models, compilers, browsers, services, or deployment platforms
are present, nor that every broad prompt succeeds. Current measured behavior
and all host-dependent boundaries remain documented in
[CAPABILITIES.md](CAPABILITIES.md).
