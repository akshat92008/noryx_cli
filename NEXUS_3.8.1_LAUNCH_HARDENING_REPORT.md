# Nexus CLI 3.8.1 — Launch Hardening Report

Release date: 2026-08-06

## Release scope

Nexus CLI 3.8.1 is the stable launch release for repository analysis, planning,
and supervised Verified Repair with mandatory human diff review. The runtime
fails closed when verification provenance is incomplete and when the deployment
host lacks qualified native isolation. Autonomous generated-command execution
is enabled only after host-specific sandbox qualification.

## Verification-integrity repairs

- Test success is accepted only from a supported test-runner invocation with a
  zero exit status and observable executed-test evidence.
- Each test record carries the runner, normalized scope, exact targets,
  workspace revision, command fingerprint, observed count, validation result,
  and test provenance.
- Arbitrary commands, compound shell commands, zero-test runs, and commands
  that merely contain test-related words cannot satisfy acceptance criteria.
- Narrow passes cannot erase broad-suite failures. A later validated full-suite
  pass may supersede narrower obligations only for the same runner and revision.
- Pre-existing test provenance is content-addressed at planning time; modified,
  generated, mixed, and unknown tests remain distinct.
- A configured project-level test suite must pass after the final mutation.
- Completion-ledger credit is granted only to explicitly targeted validated
  tests or to related tests covered by a validated full-suite run.

## Qualification results

- Collected tests: **887**
- Passed: **885**
- Platform-specific skips: **2**
- Failures/errors: **0**
- Duplicate test identities: **0**
- Packaged module imports: **248/248**
- Architecture-health gate: **PASS**

The launch matrix was executed after the finalizer refactor. One large JUnit
shard was split into two evidence parts because the local pytest/JUnit wrapper
stalled when those lifecycle-heavy files were emitted as a single report; the
same 91 tests passed in one shared process, and the two JUnit parts contain no
duplicate identities.

## Safety and competitive boundary

This release is launch-ready for analysis, planning, and supervised repository
repair. Unattended generated-command execution still requires native filesystem,
process, and network isolation to pass on the exact target host. The release does
not claim Claude Code parity or superiority. That claim remains behind the sealed
private-repository, equal-budget, repeated-trial, independently signed competitive
gate.
