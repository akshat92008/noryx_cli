# Nexus CLI 3.7.0 Cognitive Reliability Report

Date: 2026-08-06

## Executive decision

Nexus 3.7.0 converts seven previously qualitative gaps into executable runtime contracts and measurable release gates. It is designed as a supervised verified-repair runtime that can strengthen any compatible coding model while refusing unsupported completion claims.

This release does not assert that orchestration can manufacture frontier-model intelligence. It also does not claim Claude Code parity without blind external trials. Instead, it supplies the engineering controls and proof infrastructure required to make those claims testable.

## 1. Underlying model engineering intelligence

### Implemented

- `nexus/intelligence/deliberation.py` compiles the objective into falsifiable hypotheses, alternative explanations, required evidence, invariants, stop conditions, and a risk-specific confidence floor.
- High-risk routing now distinguishes measured Model Doctor profiles from conservative priors.
- A high-risk model selected only from prior assumptions is marked as not meeting requirements and requires approval or escalation.
- Model prose is never accepted as completion evidence.

### Boundary

Nexus can enforce disciplined reasoning and reject unsupported output, but the model still determines the quality of novel code synthesis and architectural judgment. That capability must be measured on hidden tasks.

## 2. Repository-scale context selection

### Implemented

- `nexus/intelligence/repository/adaptive.py` expands high-value seed files through imports, reverse imports, callers, symbol references, related tests, configuration files, and risk boundaries.
- Context selection records relationship evidence, coverage, limitations, and confidence.
- The canonical repository engine routes context bundles through the adaptive selector.
- Command-driven modifications and deletions invalidate stale indexed candidates.

### Reliability properties

- Selection is bounded by file and token budgets.
- Risky dependencies and mapped tests receive priority.
- Missing graph coverage lowers confidence instead of being silently ignored.

## 3. Difficult unseen multi-file task completion

### Implemented

- `nexus/multifile/orchestrator.py` derives executable obligations to inspect, change, verify, or preserve specific paths.
- Completion checks enforce required coordinated changes, required verification files, allowed roots, minimum changed-file counts, and preserved non-goals.
- Hard contracts are used for high-risk, security, migration, and sufficiently complex multi-file work.
- The finalizer downgrades VERIFIED to PARTIALLY_VERIFIED when obligations remain unresolved.
- Explicitly preserved files cannot simultaneously become required-change obligations.

### Result

The system no longer treats “tests passed” or model prose as proof that a multi-file request is complete.

## 4. Intelligent recovery strategy

### Implemented

- `nexus/recovery/intelligent.py` fingerprints failures while removing volatile identifiers.
- Recovery separately fingerprints the available evidence and detects whether a retry has learned anything new.
- Repeated failures escalate through:
  1. smaller patch;
  2. context expansion;
  3. plan revision;
  4. model switch;
  5. rollback;
  6. terminal stop.
- The Agent, pipeline, and tool executor share one canonical recovery controller.

### Result

Equivalent retries without an evidence delta cannot loop indefinitely or be mislabeled as progress.

## 5. Blind head-to-head benchmarks against Claude Code

### Implemented

- `nexus benchmark duel --manifest ... --output ...` executes exactly two agents against matched disposable copies of each repository.
- Agent order is randomized deterministically.
- Hidden oracle content is excluded during agent execution and installed only for verification.
- Both agents receive the same task, repository state, and verification commands.
- Reports capture executable/version provenance, argv, duration, changed-file digests, scope violations, claimed success, verified success, and false success.
- The report schema refuses to mark parity as proven automatically.

### External requirement

A real Claude Code executable and private hidden repository corpus were not available in the local build environment. Therefore, the harness is implemented and adversarially tested, but Claude Code parity remains unproven.

## 6. Native OS sandbox validation

### Implemented

- `nexus sandbox qualify` performs behavioral probes for workspace writes, timeout/process-group termination, outside-workspace reads, outside-workspace writes, and network denial.
- Native filesystem isolation is reported only for strong OS backends; lexical policy guards are never mislabeled as native isolation.
- Autonomous readiness requires a strong backend plus process, filesystem, network, and workspace-write probes.
- `.github/workflows/native-sandbox-matrix.yml` collects Linux, macOS, and Windows evidence.

### Local boundary

The local qualification host uses the restricted-process backend, so it supports review mode but does not prove native autonomous isolation. Actual platform promotion requires CI artifacts from each supported OS.

## 7. Shared-process test lifecycle cleanup

### Implemented

- `nexus/runtime/process_state.py` tracks child processes, executors, pools, context variables, caches, and one-shot cleanup callbacks.
- MCP workers, plugin workers, and process-gateway children register with the lifecycle authority.
- Test cleanup uses the production reset path.
- `scripts/qualify_shared_process.py` runs the complete suite in one interpreter under a watchdog, terminates the process group on timeout, and verifies the source-tree SHA-256 before and after execution.
- The release gate now requires both per-module isolation and the one-interpreter lifecycle run.

## Release designation

Nexus 3.7.0 is a cognitive-reliability hardened candidate for supervised, isolated verified-repair workflows with mandatory human diff review.

It is not yet authorized for unattended production merge or deployment. Promotion requires:

- real blind Claude Code trials on private hidden repositories;
- matched same-model minimal-harness comparisons;
- signed Linux/macOS/Windows sandbox artifacts;
- live-provider long-horizon and chaos evidence;
- published thresholds for verified completion, false success, regressions, intervention, repeatability, cost, and latency.

The exact tested revision, test totals, package smoke results, host sandbox status, and artifact checksums are recorded in the separate final qualification report.
