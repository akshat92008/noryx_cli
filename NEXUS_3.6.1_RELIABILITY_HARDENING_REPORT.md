# Nexus CLI 3.6.1 Reliability Hardening Report

Release date: 2026-08-06  
Source baseline: Nexus CLI 3.6.0 Truth Integrity Production Preview  
Release classification: supervised, isolated Verified Repair candidate

## Executive decision

Nexus 3.6.1 closes the independently identified canonical tool-dispatch, false-success, command-mutation, rollback, and planner/tool-contract defects in the 3.6.0 preview. The repaired runtime now uses structured execution truth and content-addressed command transactions through the normal Agent boundary.

This release materially improves reliability, but it does **not** claim that orchestration alone has produced Claude Code-equivalent model intelligence. That claim remains blocked until blind hidden-repository trials demonstrate competitive verified completion, low false-success rates, and low human intervention using comparable models and budgets.

## Audit blockers closed

### 1. Canonical MCP, plugin, and extension execution

`ToolResult` and `ToolStatus` are now imported and used in the active `ToolExecutionController` path. Plugin, extension, and MCP results are normalized at one boundary. Exceptions become structured failures instead of escaping as runtime crashes.

MCP responses with `isError` or `is_error` remain failures through the Agent event and reporting layers. Multi-part MCP content is preserved instead of reading only the first text item.

### 2. Structured status is authoritative

Agent success is now determined exclusively by:

```python
result.status == ToolStatus.SUCCESS
```

Human-readable output prefixes no longer decide orchestration truth. Legacy string-returning built-in handlers are converted to `ToolResult` once at the registry boundary while they are migrated incrementally.

Malformed or unknown external status values fail closed. Legacy numeric `ToolResult` statuses are normalized into the canonical enum for backward compatibility.

### 3. Content-addressed command mutation journal

The mtime-only command snapshot was replaced with a SHA-256 workspace journal. It records file and symlink type, digest, size, mode, link target, and an immutable preimage where restoration is required.

The union diff detects:

- created files;
- modified content;
- deleted files;
- timestamp-preserving rewrites;
- permission changes;
- file/symlink type transitions;
- symlink-target changes.

Nexus internal state and preimage storage are excluded even when `NEXUS_HOME` is nested inside the working directory.

### 4. Transactional failed-command rollback

A synchronous command now starts only after Nexus can establish a complete bounded pre-command file/symlink snapshot. Unreadable directory enumeration fails closed instead of silently producing a partial journal. Every transaction uses a collision-resistant UUID.

If the command fails after mutating the workspace, Nexus validates every stored preimage before touching the live tree, restores the complete tracked snapshot as one transaction, and verifies the resulting digest tree. The transaction handles simultaneous creation, modification, and deletion, including a file being replaced by a directory. History metadata is committed atomically in one batch; transaction entries are removed only after independent rollback verification succeeds.

Snapshot limits fail closed and are configurable through:

- `NEXUS_COMMAND_SNAPSHOT_MAX_FILES`;
- `NEXUS_COMMAND_SNAPSHOT_MAX_BYTES`.

### 5. Deletion-aware history and evidence

File history now records change type, before/after SHA-256, modes, preimage kind, symlink target, and transaction identity. Undo can recreate deleted files and symlinks and verifies restored file digests.

Evidence artifacts are typed dictionaries rather than path strings. Re-verification now proves expected existence and expected deletion, and handles symlink hashes without following the target. Failed-command mutations are not left behind as misleading successful artifact claims; the append-only trail instead records the verified restored state and the complete rolled-back mutation set.

### 6. Planner and live-tool consistency

Explicit leading intent is authoritative. A request such as “Fix the authentication race and add regression tests” is classified as repair, not build.

Risk-sensitive moderate tasks now require planning when they involve concurrency, authentication, authorization, public APIs, compatibility, databases, migrations, transactions, security, dependencies, multiple files, rollback, or production behavior.

Canonical planning now:

- binds allowed tools to the live registry;
- uses active names such as `read_file`, `edit_file`, `multi_edit`, and `run_process`;
- creates explicit root-cause hypotheses for repair and security tasks;
- maps the canonical task type back into the executable plan intent to prevent contradictory classifications;
- fails validation when a plan references unavailable tools.

## New regression boundary

`tests/test_truth_integrity_hardening_361.py` contains 17 collected cases across the previously missing production boundaries:

1. plugin execution through Agent;
2. extension structured failure through Agent;
3. MCP `isError` through Agent;
4. unknown external status fail-closed behavior;
5. invalid boolean structured status fail-closed behavior;
6. legacy internal handler normalization;
7. unreadable workspace enumeration failure;
8. corrupt-preimage preflight before destructive rollback;
9. timestamp-preserving rewrite detection;
10. deletion detection and verified restoration;
11. failed-command single-file rollback;
12. multi-path create/modify/delete transaction rollback;
13. file-to-directory replacement rollback;
14. rollback evidence re-verification against restored state;
15. repair intent and risk-sensitive planning;
16. risky concurrency/authentication/race build tasks forced through planning;
17. canonical live-tool, root-cause-hypothesis, and security-intent consistency.

## Qualification evidence

The final source tree is qualified using test-module process isolation because Nexus deliberately exercises provider registries, subprocesses, background workers, servers, environment state, and extension registries. This prevents one module's global state from creating either a false green or a false red in later modules.

The process-isolated gate records module outcomes, test totals, runtime, the source-tree SHA-256 before and after qualification, and whether the qualified tree remained stable. Exact final values are recorded in `release_evidence/isolated-pytest-3.6.1.json` and the delivery manifest. The release also requires:

- Python byte-compilation;
- architecture-health and secret checks;
- targeted canonical boundary tests;
- source distribution and wheel builds;
- clean installed-wheel import of every packaged module;
- installed `nexus --version` and `nexus --doctor` smoke tests;
- SHA-256 manifests for all delivered artifacts.

## Operational boundary

Use 3.6.1 with:

- isolated Git worktrees or disposable copies;
- mandatory human diff review;
- no autonomous merge or deployment;
- credentials scoped away from generated code and command sandboxes;
- native OS isolation for command-capable autonomous modes;
- external hidden-task evaluation before any parity claim.

## Remaining Claude Code-level blockers

The runtime integrity blockers from the audits are closed. Remaining parity work is primarily empirical engineering intelligence:

- stronger live-model root-cause reasoning;
- repository-scale context selection under long horizons;
- multi-file completion on unseen repositories;
- recovery strategy quality rather than retry quantity;
- blind matched-model benchmarks against Claude Code;
- cross-platform native-sandbox qualification;
- demonstrated low false-success and low human-intervention rates.

Nexus 3.6.1 should therefore be described as a hardened, model-agnostic engineering runtime and supervised Verified Repair candidate—not as proven Claude Code parity.
