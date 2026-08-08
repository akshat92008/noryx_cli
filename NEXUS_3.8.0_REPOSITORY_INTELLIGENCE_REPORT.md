# Nexus CLI 3.8.0 — Repository Intelligence Offensive

Release date: 2026-08-06

## Objective

Version 3.8.0 targets the remaining repository-task failure modes directly:
hidden multi-file bugs, framework migrations, feature additions, difficult
refactors, indirect test failures, repository-wide API changes, and state or
concurrency defects.

## Correctness and security repairs

- Fixed the tagged-release workflow's invalid sandbox-backend API call.
- Made native OS isolation the default for public execution APIs.
- Added a separate explicit capability for trusted, unisolated host execution.
- Added runtime-derived outside-workspace read/write probes and controlled
  loopback-network probes to sandbox qualification.
- Replaced ambiguous `READY` output with capability-specific readiness states.
- Rejected unknown command-shaped CLI input before any provider call.
- Removed stale 3.6 release evidence from the source distribution.
- Updated release provenance to signed artifact and SBOM attestations.
- Normalized wheel and sdist metadata for byte-for-byte reproducible archives.

## Hard-task execution upgrades

- Evidence-driven context expansion from stack traces, test nodes, symbols,
  imports, reverse dependencies, callers, configuration, migration diagnostics,
  and concurrency symptoms.
- Runtime evidence can promote an initially generic task to a stricter migration,
  indirect-failure, repository-API, hidden-multi-file, or concurrency profile.
- Transitive impact closure follows imports, re-exports, callers, tests, and
  configuration obligations.
- A persistent completion ledger blocks missing inspections, coordinated edits,
  verification, or preserved-file guarantees.
- Replanning must contradict or replace hypotheses, change the investigation
  structure, expand evidenced scope, and add targeted verification.
- Recovery derives evidence flags from raw failures rather than trusting callers
  to label the failure correctly.
- Static concurrency triage identifies shared mutable state, check-then-act
  patterns, synchronization boundaries, async/process lifecycle risks, and
  required stress evidence.

## Competitive proof boundary

The product goal is to outperform Claude Code. Nexus 3.8.0 does not lower that
goal and does not self-declare it achieved. The superiority gate requires a
real, sealed, independently evaluated campaign against both the same-model
direct baseline and real Claude Code. It requires private unseen repositories,
all seven task categories, repeated trials, equal budgets, category-level and
aggregate wins, complete safety/cost/latency/intervention metrics, distinct
provenance, and a valid Ed25519 evaluator signature.

Local qualification proves the release mechanisms and deterministic runtime. A
real externally run campaign is still required before publishing a
better-than-Claude-Code claim. The campaign preflight now verifies content-distinct
repository hashes, unique repository/prompt fingerprints, exact same-model identity
for Nexus and its direct baseline, real product/version provenance, withheld oracle
availability, sealed budgets, disclosed runtime environment, and complete cost, token,
latency, and intervention telemetry before execution.

## Local qualification

The final release evidence bundle records the exact suite, coverage, architecture,
package, import, reproducibility, sandbox, and stress results for the distributed
artifacts. Platform-specific native isolation must be qualified again on each
deployment host.
