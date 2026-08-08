# Nexus CLI 3.8.2 — Launch Remediation Report

Release date: 2026-08-07

## Scope

3.8.2 remediates the independent 3.8.1 launch audit without converting unproven model performance into a marketing claim. The engineering release closes lifecycle, dependency-contract, sandbox-stream, machine-readable diagnostics, and provenance weaknesses.

## Closed engineering blockers

- Hosted SDK transports are cached, owned, and explicitly closed.
- Agent and provider wrappers expose deterministic, idempotent cleanup.
- Abandoned streams are closed and recorded as cancelled.
- Sandbox pipe readers use bounded cleanup and fail closed if descendants retain inherited streams.
- The full-suite shared-process qualification requires an observed clean interpreter exit.
- Qualification validates every direct runtime dependency against `pyproject.toml` and runs `pip check`.
- Source evidence carries an exact Git revision when available, otherwise a deterministic archive tree hash, plus the dependency-lock hash.
- Sandbox qualification always carries source revision identity.
- `nexus --doctor --output-format json` emits canonical JSON rather than mixed console text.
- Release CI requires native autonomous sandbox qualification before autonomous artifacts are accepted.

## Evidence boundary

Software correctness and release mechanics are independently testable offline. Frontier coding intelligence is not. The included live-provider suite is a regression campaign, not a private unseen benchmark. Claude Code parity/superiority remains fail-closed behind the existing sealed three-way benchmark contract and cannot be asserted until external evidence passes it.

## Deployment boundary

Supervised analysis/planning/Verified Repair may be released after deterministic gates pass. Autonomous generated-command execution additionally requires the exact target host to pass native filesystem, process, and network isolation qualification.
