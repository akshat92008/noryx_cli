# Nexus CLI 3.6.0 Truth Integrity — Technical Release Report

## Release decision

Nexus 3.6.0 is designed to qualify for **supervised production use in isolated Verified Repair workflows with mandatory human review**. It is not designated for unattended autonomous production, autonomous deployment, or Claude Code parity without external live-provider, hidden-task, and cross-platform evidence.

## Audit defects closed

### Repository truth

Repository intelligence now hashes file contents. A rapid same-size rewrite invalidates cache entries, repository snapshots exclude Nexus runtime state, and evidence produced against another source revision is rejected.

### User constraints

Natural-language prohibitions compile into immutable typed policies. Explicitly prohibited files are removed from the allowed scope. Schema, public API, dependency, authentication, behavior, and compatibility constraints participate in tool authorization and semantic verification.

### Verification truth

Raw model output, notes, claims, and planner prose cannot satisfy acceptance criteria. Each criterion must map to a typed independent record such as a test execution, HTTP observation, compiler result, compatibility check, security check, database assertion, static invariant, file-hash assertion, or build artifact.

### Scope authority

Free-form explanations are ignored. A scope expansion is accepted only when Nexus has pre-registered the exact evidence from repository indexing, a compiler/test failure, or explicit human authority. The requesting model cannot register its own evidence as trusted.

### Persistent state

Task memory and long-horizon checkpoints use HMAC-SHA256 with key material outside the repository. State also uses atomic writes, cross-process locks, monotonic sequences, stale-writer rejection, and strict signature verification.

### Concurrent edits

Before mutation, Nexus compares the current file hash to the hash observed during planning. External edits require a new repository snapshot and plan rather than being overwritten.

## Executable artifact evidence

The installed wheel is required to run five fresh offline scenarios:

1. reproduce and repair a real calculator regression through the canonical Agent pipeline;
2. reject an explicitly prohibited file change;
3. reject model prose as acceptance evidence;
4. reject fabricated typed scope evidence;
5. reject a concurrent external file modification.

This benchmark proves deterministic runtime and governance behavior. It explicitly reports zero live model calls and makes no model-intelligence claim.

## Deployment classifications

- `LOCAL_SMOKE_READY`: architecture, doctor, package resource, and authenticated-state checks pass.
- `SUPERVISED_PRODUCTION_READY`: the deep installed repair/adversarial suite also passes in review, quality, or CI mode.
- Autonomous production: always blocked in this release unless external qualification is supplied through the release-candidate process.

## Autonomous promotion requirements

The external hidden-task gate requires a hash-pinned task pack outside the source tree, at least 30 unique tasks, at least three trials per task, executable independent oracles, verified success of at least 60%, false verification at most 1%, zero prohibited changes, repeatability of at least 80%, and human intervention at most 10%.

Live-provider long-horizon, Linux native sandbox, macOS, Windows, and blind comparative evidence remain mandatory.
