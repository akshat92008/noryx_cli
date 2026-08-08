# Noryx CLI 3.8.4 Validation Summary

Date: 2026-08-08

## Release identity

- Distribution: `noryx-cli`
- Version: `3.8.4`
- Canonical console command: `noryx`
- Canonical import namespace: `noryx`
- Canonical user/project state: `NORYX_HOME`, `.noryx`, and `NORYX.md`
- Compatibility: legacy `nexus` console/import paths, `NEXUS_*` variables, and legacy schema identifiers remain accepted during the 3.x migration window.

## Executed validation

| Gate | Result |
| --- | --- |
| Full deterministic pytest suite | **920 passed, 3 skipped, 0 failed** in 254.21 s |
| Full randomized pytest suite (`--randomly-seed=384`) | **920 passed, 3 skipped, 0 failed** in 201.62 s |
| Ruff lint | **PASS** |
| Python bytecode compilation | **PASS** |
| Architecture/import/complexity gate | **PASS**: 252/252 packaged modules, 277 source paths, 2,383 functions |
| Installed wheel E2E | **15 passed, 0 failed** |
| Adversarial stress groups | **4/4 passed**; 77 focused tests passed with 2 expected platform skips |
| Concurrent stress matrix | **20/20 passed**, 4 workers |
| Offline reliability benchmark | **5/5 passed**, including 1 real repository repair; 0 model calls and no intelligence claim |
| Installed-core benchmark manifest | **VALID** in dry-run; model execution intentionally not performed |
| Exact dependency lock consistency | **PASS** with offline/no-index dry run and `pip check` |
| SPDX SBOM | **32 packages** |
| Package build | Wheel and sdist built successfully |

The three skipped full-suite cases are one optional Playwright integration (dependency not installed), one macOS-only sandbox assertion, and one Windows-only sandbox assertion. They are reported as skips, not converted into passes.

## Stress and failure-mode coverage

Validation exercises concurrent CLI startup, long-term runtime loops, agent safety, launch contracts, provider chaos, sandbox boundaries, network denial, process teardown, false-success rejection, repository-wide changes, migrations, multi-file recovery, command-policy bypass attempts, state-signature tampering, and competitive-evidence forgery attempts.

## Package evidence

The release bundle contains:

- complete source ZIP;
- wheel and source distribution;
- concatenated all-text-source TXT with per-file SHA-256 digests;
- SPDX SBOM;
- audit-remediation and validation reports;
- adversarial, concurrent, offline, architecture, sandbox, benchmark, and deploy-readiness JSON evidence;
- top-level SHA-256 manifest.

## Honest readiness classification

- **Release artifact quality:** qualified for public distribution as a supervised Verified Repair release candidate.
- **Target-host supervised production:** requires a successful deep deploy check, native sandbox, and a configured model on that host.
- **This build host:** `NOT_READY` because Bubblewrap and a model backend were absent; fail-closed behavior worked.
- **Unattended autonomy:** not certified on this host.
- **Better than Claude Code:** unproven; no external private Claude Code campaign was run.

No report asserts that absence of observed failures proves the absence of all defects. The statements above are limited to executed, retained evidence.
