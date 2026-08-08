# Nexus CLI 3.4.0 — Verified Repair Reliability Report

## Release decision

Nexus CLI 3.4.0 is a **hardened controlled-beta technical preview** of Nexus Verified Repair. It is suitable for supervised evaluation on bounded repository repairs. It is not certified for unattended production engineering, release-candidate promotion, or Claude Code-equivalent performance.

The release is built around one defensible product promise:

> Nexus is the verification and reliability operating layer for coding models. It attempts the smallest coherent repair, enforces budget and workspace boundaries, verifies the result independently, emits a tamper-evident proof receipt, or stops honestly.

## Flagship workflow

```bash
nexus fix \
  "Fix the refresh-token regression without changing the database schema" \
  --budget-inr 20 \
  --model auto \
  --proof
```

The workflow:

1. Classifies task risk and likely file scope.
2. Selects a model through the task-aware router, unless the user pins one.
3. Opens an isolated workspace by default.
4. Uses the local Intern only for bounded low-risk atomic subtasks.
5. Requires the Ceiling for ambiguity, broad changes, security, concurrency, migrations, and final review.
6. Reproduces the defect before editing where the repository permits it.
7. Maps callers, interfaces, tests, data contracts, and security boundaries.
8. Applies the smallest coherent patch inside explicit budget and turn ceilings.
9. Runs targeted and regression checks through the canonical process boundary.
10. Emits a Nexus Proof receipt only from recorded evidence.

## Reliability changes delivered

### Artifact integrity

- Restored release files omitted from 3.3.0 archives.
- Added a source-distribution manifest for workflows, tests, benchmark fixtures, scripts, and package resources.
- Packaged the installed benchmark repository with the wheel.
- Installed-wheel qualification runs from an unrelated empty directory and cannot access source-checkout fixtures.
- Source and wheel module parity are verified through a complete installed import scan.

### Evidence integrity

- Release evidence is bound to the exact source-tree hash, source archive hash, wheel hash, version, runner, command, counts, timestamp, JUnit XML, coverage XML, and benchmark report.
- Qualification rejects missing reports, stale or mismatched artifacts, invalid counts, failed tests, path escapes, and report hash drift.
- The test matrix is resumable and uses separate process, HOME, cache, Nexus state, JUnit, log, and coverage files for every shard.

### Architecture consolidation

- `nexus.execution` is one package with one public execution authority.
- `nexus.recovery` is one package with one canonical recovery surface.
- Shadowed module/package collisions were removed.
- `AgentSession` is fail-closed and cannot manufacture a successful verified result.
- Architecture qualification checks physical source paths as well as import discovery.
- Concrete `NotImplementedError`, fake-success markers, unreachable modules, module/package collisions, and complexity regressions block qualification.

### Verification and proof

- Proof receipts use the `nexus.proof.v2` schema.
- Receipts include source revision, repository-state hash, changed-file fingerprints, acceptance evidence, commands, evidence hashes, models, routing decision, costs, risks, and rollback checkpoint.
- A claimed `VERIFIED` result is downgraded unless external checks pass, all acceptance criteria are satisfied, and underlying evidence exists.
- Budget excess produces `BLOCKED`, not success.
- Receipt tampering and optional workspace drift are detectable.

### Ceiling + Intern differentiator

- The Intern is restricted to explicit low-risk atomic work and capability thresholds.
- Ambiguity, multi-file work, manifests, architecture, security, authentication, concurrency, migrations, and conflicts route to the Ceiling.
- Intern candidates remain isolated until deterministic validation succeeds.
- Intern failure escalates rather than being converted into success.
- The Ceiling is the sole final semantic-review authority.

## Fresh deterministic qualification

The exact source tree contains 771 collected tests. The resumable release matrix produced:

- **769 passed**
- **0 failed**
- **2 skipped** because macOS- and Windows-specific sandbox assertions cannot run on Linux
- 12 isolated process shards
- aggregate branch-coverage evidence
- installed-wheel benchmark and import evidence

The final artifact-bound evidence JSON and qualification decision are shipped in `release_evidence/`.

## Remaining proof required

Release-candidate or parity claims remain blocked until Nexus demonstrates:

- repeated real-provider long-horizon repairs on fresh repositories;
- matched direct-model-versus-Nexus uplift experiments;
- false verified completion below the public-beta threshold;
- Linux Bubblewrap, macOS, Windows, and supported Python matrix results;
- dependency vulnerability scanning, SBOM, and signed provenance;
- measured success, cost, latency, unnecessary-change, recovery, and human-intervention rates;
- external comparison under identical task, model, revision, budget, and acceptance checks.

## Correct designation

**Nexus CLI 3.4.0 — Verified Repair controlled-beta technical preview.**
