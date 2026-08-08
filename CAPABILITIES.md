# NEXUS CLI and `nova_codex` capability report

## Nexus 3.8.2 launch and repository-intelligence boundary

- Engineering deliberation compiles falsifiable hypotheses, evidence requirements, invariants, alternatives, and stop conditions before high-risk mutation.
- Failure evidence drives repository context expansion through stack paths, failing tests, symbols, imports, reverse dependencies, callers, configuration, migration surfaces, and concurrency signals under risk-scaled file/token/graph-hop budgets.
- Multi-file completion is an executable contract: missing inspection, required coordinated edits, targeted verification, unexpected paths, or preserved-file violations block completion.
- Test evidence is fail-closed: only a recognized runner with observable execution, exact scope, revision binding, and content-addressed provenance can satisfy completion; narrow passes cannot erase broad failures.
- Recovery extracts stack paths, symbols, test nodes, missing modules, migration signals, and concurrency symptoms directly from raw runtime failures; it then requires evidence delta and structural plan mutation, so duplicate evidence cannot masquerade as replanning or loop indefinitely.
- Model routing distinguishes measured capability evidence from conservative priors and blocks autonomous high-risk certification when evidence is missing.
- `nexus benchmark duel` supports Nexus, a same-model direct baseline, and real Claude Code on blind matched repositories; `nexus benchmark superiority-gate` refuses a claim without private unseen tasks, withheld oracles, distinct provenance, repeated trials, per-category wins, cost, latency, intervention evidence, and a valid independent Ed25519 evaluator signature.
- `nexus sandbox qualify` behaviorally attests process containment, filesystem isolation, network isolation, and the supported execution mode for the current host.
- Release qualification now requires both per-module process isolation and the entire suite in one interpreter.

The product objective is to outperform Claude Code. The runtime now contains a strict empirical gate for that claim; until real private benchmark evidence passes it, the software reports the goal as unproven rather than lowering the goal.

Updated on 2026-07-29. The local Nova model used by Nexus is the Ollama model named **`nova_codex`**. Nexus exposes it through the model key `nova3b` and aliases including `nova_codex`, `nova`, and `local`.

This report separates model generation ability from Nexus enforcement. “Guarded” means Nexus can detect and stop a bad result; it does not mean `nova_codex` always generates the right result on its first attempt.

## `nova_codex` model

### Proven strengths

- Executes narrow, explicit, single-file create and modify tasks locally.
- Produced independently verified Python entrypoints, Python surgical fixes, C++ programs, and valid JSON manifests in recorded runs.
- Uses the trained `<<THINKING>>` / `<<FILES>>` protocol with exact file/action metadata when it complies; Nexus also accepts the versioned `nova.patch.v1` JSON protocol.
- Can repair some protocol, syntax, entrypoint, boundary-condition, and recursive-code failures after receiving concrete verifier output.
- Runs fully locally through Ollama, with no hosted inference charge.

### Nexus-enforced model safeguards

- Exact requested path matching; invented `src/` prefixes and version-like fake paths are rejected.
- Strict `CREATE` versus `MODIFY` semantics; nonexistent modify targets and existing create targets fail.
- Empty, truncated, unbalanced, malformed, and nested-marker output is rejected or canonicalized before application.
- Python AST and JSON parsing; Node syntax checks; Go, C, C++, and Rust compiler checks when the compiler exists.
- Required Python, Go, C/C++, Rust, and JavaScript entrypoint checks. Python
  framework entrypoints such as `cli()` are accepted when called from an
  `if __name__ == "__main__"` guard.
- Targeted semantic checks for nonnegative boundary errors, recursive self-calls, stable relative-path roots, missing JavaScript built-in imports, and exact-output trailing separators/newlines.
- One clean compiler/semantic repair from the beginning; the hosted two-node path additionally retries Nova and can escalate the individual failed subtask to the Ceiling.
- Candidate files are validated in isolated trees. Failed Nova candidates are never promoted into Ceiling retry context or the real workspace.

### Current limitations

- It is a small local model and remains nondeterministic on exact multi-constraint generation. Repeated runs exposed wrong paths, missing imports, malformed fences, changed boundary behavior, missing entrypoint calls, and subtly wrong relative paths.
- Compiler success is not proof of behavior. Nexus labels completion unverified until a real test/build command or independent behavior assertion passes.
- Complex, ambiguous, multi-file, concurrency, security, database, and architecture tasks should use the two-node mode; Nexus routes known weak spots directly to the Ceiling or escalates after bounded Nova retries.
- It is not honestly equivalent to a frontier hosted coding model. Its practical role is a fast, free Intern behind strict gates.

## Nexus CLI

### Agent and workflow

- Interactive streaming and non-interactive `--print` execution.
- Text, JSON, and stream-JSON outputs with complete local-model/guardrail traces.
- Session autosave, `--continue`, `--resume`, history, compaction, and configurable maximum turns.
- Plans and read-only plan mode; project structure, architecture, active context, and project/user memory.
- Plans persist acceptance criteria, permitted paths, dependency-aware tasks,
  risk, retry ceilings, checks, and tool budgets.
- Model switching plus visible per-subtask Nova/Ceiling routing reasons, retries, escalations, and free-first counters.

### 38 built-in tools

- Files: read, write, edit, line patch, batch edit, metadata, and diff.
- Search/context: regex search, glob discovery, project tree, RepoGraph
  relevance, symbols, callers, reverse dependencies, impacted tests, API
  routes, database models, ownership, Git changes, LSP, and Tree-sitter.
- Shell: shell-free argv commands, reviewed compatibility commands, and
  Nexus-owned background start, status/full logs, and stop.
- Git: status, diff, commit, log, and branch operations.
- Web: fetch and search.
- Verification: API contracts, browser workflows, SQLite integrity, migration
  risk, and bounded security scanning.

### Safety and trust

- Dry-run unified diffs before mutations, with `/apply`, `/reject`, `/edit-pending`, and `acceptEdits` mode.
- Safe, warning, dangerous, and permanently blocked operation classes.
- Dangerous actions require an exact, one-shot confirmation ID; repeating the command does not grant permission.
- Workspace confinement and explicit `--add-dir` authorization; batched edits scope-check every path.
- Content-addressed trust for project instructions, MCP definitions, hooks, and plugin manifests. Any byte change invalidates approval and displays a diff.
- Anti-slopsquatting checks against live PyPI, npm, crates.io, and Go registries before dependency writes or install commands. Confirmed-missing packages are blocked; registry outages are labelled unverified and require explicit approval; new/low-download packages warn.
- Web file APIs are workspace-confined and localhost CORS-scoped.
- `NEXUS_DISABLE_NETWORK=1` blocks hosted inference, web fetch, and web search
  before a transport is opened; web search also uses the same pinned-address
  SSRF policy as web fetch.

### Verified completion and recovery

- Every applied mutation is re-read, SHA-256 fingerprinted, compiler-checked where applicable, and written to append-only JSONL evidence.
- Commands retain real exit codes and complete raw output; verification paths contain no `|| true` success masking.
- `/verify` re-reads artifact hashes and reruns eligible verification commands, detecting post-completion drift.
- Persistent pre-edit snapshots support `/undo N` and `/rewind N`.
- Every request persists a canonical run contract with request, plan, events,
  verified checkpoints, costs, criterion outcomes, risks, and final report.
- `/rollback-run` reverses every file operation made by the current run;
  `/run-status` reports the durable state and latest checkpoint.
- Compiler/semantic failures roll back real-workspace writes; two-node candidate failures remain isolated.
- Completion prose that claims tests passed without recorded passing evidence is prefixed with `UNVERIFIED TEST CLAIM`.
- Hard logical hosted-call, physical provider-attempt, and token limits are
  enforced. Currency limits are enforced
  only when the user supplies explicit provider prices, avoiding fabricated
  cost claims.
- Modifying CLI and web sessions automatically create a dedicated Git
  branch/worktree; non-Git projects use a persistent isolated copy.
- Native Bubblewrap or macOS sandbox execution is selected only after a real
  capability probe. Typed processes can fail closed when OS isolation is
  required; fallback results explicitly report that network isolation is
  policy-only.
- Durable runs contain separate model/tool call logs, costs, patch/test
  artifacts, checkpoints, and the canonical `final_report.json`.
- `nexus runs`, `inspect`, `replay`, `resume`, and `rollback` provide
  command-line lifecycle management and interrupted-task continuation.

### Extensibility and interfaces

- Skills, isolated subagents, lifecycle hooks, trust-gated local plugins, and stdio MCP tool discovery/invocation.
- Stable versioned provider, tool, and policy protocols discovered through
  Python entry points.
- Terminal CLI and Starlette/WebSocket browser UI.
- Project instruction support for `NEXUS.md`, `AGENTS.md`, and `CLAUDE.md`
  after explicit digest approval, plus `.nexus/policies.yml`.
- Headless operational presets for plan, review, workspace, autonomous,
  local-only, maximum-quality, budget, and CI execution.
- Versioned public benchmark manifests with disposable repository copies,
  shell-free checks, scope measurement, model/cost metrics, and JSON results.

## Current Claude Code parity boundary

Nexus 3.5.0 covers the open runtime described in its product
specification: guarded model orchestration, persistent repository
intelligence, typed task contracts and tools, automatic workspaces, optional
native sandboxing, bounded repair and review, behavioral verification,
recovery, budgets, policies, extensions, CI, issue workflows, and public
benchmark infrastructure.

This is a capability boundary, not a claim that every provider/model will
solve every broad product prompt. Browser verification requires the optional
Playwright runtime; precise language navigation requires an installed language
server or Tree-sitter extra; compiler, service, database, and deployment checks
require those local systems. Nexus reports unavailable checks and remaining
risk rather than converting them into success. Anthropic-specific managed
cloud, mobile, IDE, and Remote Control services are not Nexus features.

## Raw verification record

- Version 3.6.1 deterministic Python suite and release-gate totals are recorded
  by the tagged CI run and benchmark JSON rather than hard-coded before CI.
- The release gate also runs Ruff, byte-compilation, sdist/wheel builds, an
  isolated wheel-target install, packaged Nova and web backend imports,
  `nexus --version`, and `nexus --doctor`.
- Historical scenario matrices are published as tagged CI artifacts rather than
  asserted from files that are not shipped in the source distribution. Failed
  scenarios remain failures; they are not relabeled as passes.

No benchmark percentage beyond these recorded runs is claimed.


## 3.6.1 execution-truth hardening

The canonical runtime now adds these enforced invariants:

- plugin, extension, and MCP calls always normalize to structured `ToolResult` values;
- orchestration success is determined only by `ToolStatus.SUCCESS`, never display text;
- MCP `isError` responses remain failures through the Agent boundary;
- shell commands are wrapped in a content-addressed workspace transaction that detects creations, modifications, deletions, permission changes, symlink changes, and timestamp-preserving rewrites;
- failed commands automatically restore and verify the complete pre-command snapshot;
- command mutation evidence uses typed artifact records and verifies expected deletion as well as expected content;
- explicit repair intent outranks secondary “add tests” language, risky moderate repairs require a plan, and canonical plan tools must exist in the live registry;
- a process-isolated pytest-module gate detects functional regressions without allowing leaked global state from one module to contaminate another.

These controls improve runtime reliability and eliminate the audit findings. Model-level Claude Code equivalence still requires external matched-model benchmarks.

## 3.6.0 truth-integrity boundary

Nexus now guarantees the following deterministic invariants in the active Verified Repair path:

- repository cache reuse requires a matching content hash, not matching timestamps or sizes;
- planning, editing, verification, and recovery evidence carry repository revision identities;
- explicit prohibitions compile into tool-level and semantic policies;
- model prose is never acceptance evidence;
- scope expansion requires Nexus-registered dependency/compiler/test evidence or explicit human authority;
- writes use optimistic concurrency checks;
- persistent engineering state is HMAC-authenticated with external key material;
- the installed wheel must execute a real bounded repair and adversarial integrity suite;
- supervised production readiness and autonomous production readiness are reported separately.

The release still does not establish frontier-model coding intelligence, unattended production safety, or Claude Code equivalence.
