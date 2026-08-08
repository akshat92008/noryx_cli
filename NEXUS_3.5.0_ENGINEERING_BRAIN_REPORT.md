# Nexus CLI 3.5.0 Engineering Brain — Technical Release Report

## Executive verdict

Nexus 3.5.0 converts several previously disconnected intelligence features into an enforced engineering control plane. Repository understanding, task memory, scope authority, phase state, failure lessons, semantic verification, and matched evaluation now participate in the active runtime rather than existing only as optional utilities.

This release is suitable for controlled-beta, supervised repository-repair evaluation after its attached artifact-bound qualification passes. It is not evidence that Nexus matches Claude Code's proprietary model intelligence, and it is not a guarantee of perfect outcomes on every repository.

## Intelligence architecture

```text
User objective
     |
Repository Intelligence
     |-- decisive files and symbols
     |-- callers and related tests
     |-- architecture constraints
     |-- repository tree hash
     v
Engineering Contract + Plan Critic
     |-- goals and non-goals
     |-- permitted files and budgets
     |-- required baseline and checks
     v
Ceiling / Intern routing
     v
Policy and Surgical Mutation Guard
     v
External Verification + Independent Review
     v
Semantic Acceptance
     v
Nexus Proof or honest BLOCKED / FAILED / PARTIALLY_VERIFIED
```

## Reliability controls

### Repository intelligence

The runtime creates a compact repository context bundle before mutation. Explicit user-named paths are preserved even for new files, preventing a strict repository graph from blocking legitimate creation tasks.

### Persistent memory

Task memory records the objective, repository hash, constraints, non-goals, decisions, mutations, failures, remaining work, and verification summary. It is atomically replaced, SHA-256 sealed, sequence checked, and protected by a cross-process lock.

### Long-horizon execution

Every task moves through evidence-aware phases. VERIFY, REVIEW, and COMPLETE require evidence identifiers. Corrupt state raises an integrity error and cannot be silently reset.

### Surgical editing

File and line budgets, forbidden path patterns, explicit prohibitions, and bounded scope expansion are enforced by the tool executor. The model cannot bypass these controls by merely asserting that another file is necessary.

### Semantic completion

Nexus distinguishes “tests passed” from “the requirement is supported by evidence.” Production behavior cannot be called verified when only tests changed, when independent review is absent, when scope expanded without authorization, or when acceptance criteria remain unmapped.

### Failure learning

Repeated failures create redacted, hash-chained lessons. The strategy escalates from narrow reproduction to new root-cause hypotheses and finally to Ceiling-level re-analysis rather than repeating the same edit loop.

### Matched evaluation

The comparison gate prevents misleading model comparisons. Direct and Nexus trials must share task ID, model, source revision, and budget. Promotion gates can then evaluate verified uplift, false completion, regressions, and budget compliance.

## Deployment boundary

`nexus deploy check` validates local architecture, doctor readiness, and installed benchmark availability while explicitly refusing to convert local readiness into a production or parity claim. Real-provider long-horizon and cross-platform evidence remain separate promotion requirements.

## Remaining work

- Decompose the largest CLI, tool, agent, planner, backend, and pipeline modules behind tested interfaces.
- Run matched real-model trials across multiple languages and repository types.
- Add fresh hidden tests and malicious-repository scenarios.
- Qualify native sandboxing on Linux, macOS, and Windows.
- Publish signed provenance, SBOM, dependency vulnerability results, and hosted benchmark evidence.
- Learn routing and recovery policies from consented verified-outcome data rather than unverified model self-reports.
