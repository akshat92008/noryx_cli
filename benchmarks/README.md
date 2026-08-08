# Nexus reproducible benchmarks

`nexus benchmark` runs each manifest task in a disposable repository copy and
records deterministic checks, changed files, time, model calls, tokens, cost,
retries, checkpoints, resumptions, quality gates, and human-intervention state.
Results include the exact Nexus version.

Validate the bundled manifest without invoking a model:

```bash
nexus benchmark --manifest benchmarks/core.json --dry-run
```

Run it and preserve the result:

```bash
nexus benchmark \
  --manifest benchmarks/core.json \
  --output benchmarks/results/nexus-3.1.1.json
```

The v2 long-horizon suite contains independent commerce-control-plane and
operations-ERP projects. It can automatically resume the same original prompt
after a process interruption or bounded turn budget. It never converts a failed
check into a pass and never sends a new product prompt:

```bash
nexus benchmark \
  --manifest benchmarks/long_horizon.json \
  --artifact-dir verification_evidence/long-horizon \
  --keep-workspaces
```

Real-provider execution is deliberately separate because it can spend money:

```bash
python scripts/run_live_provider_gate.py --allow-cost \
  --manifest benchmarks/long_horizon.json \
  --trials 3 --required-pass-rate 1.0
```

The live gate requires hosted credentials, runs every trial in a fresh
workspace, preserves redacted evidence, and fails unless every result is both
externally accepted and internally `VERIFIED`. The GitHub workflow is manual;
it is never triggered by an untrusted pull request or schedule.

Verification commands are JSON argv arrays, never shell strings. A passing
result requires a zero Nexus exit, deterministic checks, permitted file scope,
expected mutations, required artifacts, minimum test depth, placeholder checks,
and a fully `VERIFIED` internal run report. External and internal outcomes
remain separate in the result schema.
Provider-dependent results are not committed as product claims until the
manifest, model configuration, environment, and raw result are published.

## Better-than-Claude-Code qualification

A superiority claim uses three agents on the same private task revisions:
Nexus, the same underlying model through a minimal direct baseline, and real
Claude Code. Run the preflight before any paid execution:

```bash
nexus benchmark superiority-preflight \
  --manifest /secure/private/superiority-campaign.json \
  --output verification_evidence/superiority-preflight.json
```

The default preflight requires at least 50 unique task fingerprints, 10
content-distinct repositories, three trials, at least five tasks in every hard
category, non-placeholder product/model identities, the exact same model for
Nexus and the direct baseline, non-empty withheld oracles, equal sealed budgets,
and a disclosed immutable runner environment.

After the completed report is signed in the independent evaluator environment,
run the claim gate:

```bash
nexus benchmark superiority-gate \
  --report /secure/evaluator/signed-superiority-report.json \
  --output verification_evidence/superiority-evaluation.json
```

The gate rejects dry runs, scripted smoke stand-ins, duplicate repository
content, duplicated repository/prompt tasks, missing cost/token/intervention
telemetry, budget overruns, incomplete agent provenance, unsigned evidence, and
any campaign where Nexus does not beat Claude Code both overall and in every
required task category.
