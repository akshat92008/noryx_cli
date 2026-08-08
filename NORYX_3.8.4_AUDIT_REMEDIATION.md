# Noryx CLI 3.8.4 Audit Remediation

Date: 2026-08-08

This report maps every recommendation in the supplied “Nexus CLI 3.8.3 — Deep Independent Audit” to the 3.8.4 implementation. “Complete” means the source and automated tests implement the remediation. It does not substitute for target-host qualification or an external competitive campaign.

| Audit item | 3.8.4 status | Implemented evidence |
| --- | --- | --- |
| P0 external evaluator trust anchor | Complete | The report can no longer establish its own root of trust. `load_trust_policy` supplies trusted Ed25519 keys and the sealed campaign identity out of band; unknown keys and self-signed synthetic reports fail closed. |
| P0 benchmark-agent/oracle isolation | Complete in the harness; target-host gate required | Candidate processes execute only through `SandboxRunner`; candidate workspaces exclude the oracle, evaluator, and sealed source. The campaign records isolation provenance and refuses unsupported native isolation. |
| P0 external sealed provenance | Complete | The trust policy pins campaign ID plus manifest, sealed-manifest, oracle-bundle, task-set, and environment hashes. Values carried only by the report are insufficient. |
| P0/P1 evaluator-owned telemetry | Complete | Cost, tokens, duration, retries, and interventions must originate in an evaluator-owned directory outside the candidate workspace and carry evaluator identity/content hashes. Candidate-generated telemetry is rejected. |
| P1 process termination | Complete | Termination is now `SIGTERM → bounded wait → SIGKILL → wait/reap → cleanup → unregister`; a live child is never silently dropped from the registry. |
| P1 macOS Mach/XPC hardening | Code complete; physical-Mac qualification pending | Broad Mach lookup was removed in favor of explicit global service rules. Adversarial profile tests cover the restricted policy, but the macOS behavioral test remains correctly skipped on Linux. |
| macOS profile cleanup | Complete | `wait`, `terminate`, and `kill` clean generated sandbox profiles after the child is reaped. |
| P2 command-policy duplication | Complete | `ToolExecutor` now delegates command classification to the tested authoritative `CommandPolicy`; compound/unknown commands fail closed. |
| P2 supply-chain reproducibility | Substantially complete; artifact-hash lock pending | Exact runtime/development locks, lock digests in provenance, a 32-package SPDX SBOM, wheel/source checks, deterministic builds, and installed-wheel E2E tests are included. Dependency artifact `--hash` pins could not be generated without package-index access and are not falsely claimed. |
| P3 release metadata drift | Complete | The distribution, command, docs, constraints, installer, reports, and version are Noryx CLI 3.8.4. Historical `nexus` import/command and 3.x schema spellings remain explicit compatibility aliases. |

## New security regression coverage

`tests/test_noryx_public_launch_384.py` adds direct regressions for:

- rejection of a report-supplied evaluator key;
- mandatory out-of-band campaign identity and evaluator keys;
- rejection of evaluator metrics inside candidate workspaces;
- bounded process TERM/KILL/reap ordering and cleanup;
- macOS service allowlisting and temporary-profile cleanup;
- canonical Noryx entrypoints, imports, environment precedence, and state paths;
- authoritative command-policy behavior.

## Competitive-claim boundary

The audit’s scientific conclusion remains valid: code hardening cannot prove that Noryx is universally better than Claude Code. Version 3.8.4 makes that claim harder to forge. A claim is allowed only after an external evaluator runs the declared private campaign (at least 50 unseen tasks, 10 repositories, and 3 trials per task), signs the raw evidence with a separately trusted key, and every configured category/safety/cost/latency threshold passes. No such campaign was available in this build environment, so the release makes no Claude Code parity or superiority claim.

## Deployment boundary

This source and package qualify as a supervised Verified Repair release candidate. Each installation must pass `noryx deploy check --mode review --deep` on its real host. Autonomous operation additionally requires the native sandbox behavioral gate and externally trusted competitive evidence. On the Linux build host used here, Bubblewrap and a model backend were absent; Noryx therefore returned `NOT_READY` and blocked the production claim as designed.
