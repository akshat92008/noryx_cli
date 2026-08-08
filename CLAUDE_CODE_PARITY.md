# Nexus and Claude Code — evidence boundary

## Version 3.8.2 competitive contract

The product goal remains to outperform Claude Code. Version 3.8.2 retains the 3.8.0 competitive gate and converts that goal into a fail-closed qualification contract rather than lowering it or declaring success from internal smoke tests.

Nexus now requires a sealed three-way campaign:

1. Nexus with the selected underlying model.
2. The same underlying model with only a direct baseline tool loop.
3. A real Claude Code executable.

Qualification requires at least 50 private unseen tasks across 10 repositories, all seven hard-task categories, at least three trials per task, identical repository revisions/prompts/oracles/budgets, withheld oracles, zero post-selection task changes, complete cost/latency/intervention data, distinct executable provenance, category-level wins, an aggregate verified-completion margin, same-model uplift, and an Ed25519 signature from the independent evaluator. Editing any signed result invalidates the report.

**The goal is not compromised, but the claim remains unproven until that external campaign passes.** Local tests prove the runtime and the qualification machinery; they cannot substitute for real Claude Code executions or private unseen repositories.

Version 3.8.2 additionally hardens the internal truth boundary: a command cannot become test evidence merely because it exits successfully or contains a runner name; broad failures remain blocking until an equal or broader validated suite passes against the final workspace revision. This strengthens benchmark integrity but is not itself evidence of parity.

Campaign flow:

```bash
cp benchmarks/superiority.example.json private-superiority.json
# Replace every placeholder and add the full sealed private task set.
nexus benchmark duel --manifest private-superiority.json --output unsigned-report.json
python scripts/sign_superiority_report.py \
  --report unsigned-report.json \
  --private-key /secure/evaluator-ed25519.key \
  --evaluator-id independent-lab \
  --output signed-report.json
nexus benchmark superiority-gate --report signed-report.json
```

The private evaluator key must never be stored in the Nexus repository or its product CI.


# NEXUS local-agent parity audit

Research date: 2026-07-29. Source of truth: Anthropic's official [Claude Code documentation index](https://code.claude.com/docs/llms.txt), [tools reference](https://code.claude.com/docs/en/tools-reference), [extension overview](https://code.claude.com/docs/en/features-overview), [checkpointing guide](https://code.claude.com/docs/en/checkpointing), and [CLI reference](https://code.claude.com/docs/en/cli-reference).

“Parity” here means local agentic coding capability, not a claim that Nexus can reproduce Anthropic-account services. Cloud hosting, Claude subscriptions, proprietary models, phone push delivery, claude.ai Artifacts, Remote Control, Chrome computer use, Slack/GitHub hosted apps, organization analytics, and Anthropic-managed Routines require external services Nexus does not own. They are explicitly out of scope rather than simulated.

## Implemented and enforced

| Claude Code capability | NEXUS implementation |
|---|---|
| Multi-turn tool loop and streaming | Persistent OpenAI-compatible tool-call loop, streaming terminal UI, bounded turns |
| Read, write, edit, glob/grep, repository graph, shell, git, web, behavioral checks | 38 built-in tools in `nexus/tools.py` |
| Diff approval and permission modes | Dry-run unified diff; `/apply`, `/reject`, `/edit-pending`; `default`, `acceptEdits`, and read-only `plan` modes |
| Checkpoint/rewind | Persistent pre-edit snapshots; canonical run checkpoints; `/undo N`, `/rewind N`, `/rollback-run`, `/diff`, `/changes` |
| Session continue/resume | Auto-saved history plus canonical run state, `runs`, `inspect`, `replay`, `resume`, `rollback`, `--continue`, and `--resume` |
| Context compaction | `/compact` and automatic compaction on provider context overflow |
| Project instructions/memory | Combined nearest `NEXUS.md`, `AGENTS.md`, trusted `CLAUDE.md`, and project decision memory |
| Content trust | Exact SHA-256 approval, diff on every change, immediate deactivation until re-approved |
| Plan/task tracking | Persisted typed DAGs with acceptance criteria, file scope, dependencies, risk, retries, checks, budgets, checkpoints, repair, and independent review |
| Skills | Built-in, user, and trusted declarative project skills with automatic activation |
| Subagents | Isolated specialized subagent contexts and templates |
| Hooks | Before/after file, command, commit, test, error, session, and model lifecycle events |
| MCP | Stdio discovery and tool invocation; config is disabled unless its exact bytes are trusted |
| Plugins | Project/global plugins providing skills, hooks, and tools; local manifests are trust-gated |
| Background work | Start, poll, read complete logs, and stop Nexus-owned typed processes |
| Safety/permissions | Allow/deny/ask policy rules; blocked, dangerous, warning, and safe tiers; exact one-shot confirmations; native sandbox adapters with visible fail-closed behavior |
| Large-codebase support | Persistent RepoGraph v2 plus routes, models, ownership, Git relevance, incremental context, persistent LSP clients, and Tree-sitter fallback |
| Non-interactive automation | `run`, CI mode, text/JSON/JSONL output, issue ingestion, deterministic exit codes, and public benchmark manifests |
| Model selection | CLI/model command switching plus explicit Ceiling/Nova per-subtask routing reasons |
| Cost visibility and limits | Local/Ceiling counts plus hard hosted-call/token limits; configured-currency limits require explicit prices |
| Verification | Real exit codes and raw output plus syntax, compiler, tests, build, security, API, browser, read-only database, migration-risk, and disk-replay gates |
| Worktree isolation | Modifying runs automatically use a Git worktree or persistent isolated non-Git copy; `--no-workspace` is explicit |
| Run reporting | Versioned request, plan, task, model, tool, cost, patch, test, checkpoint, criterion, risk, and canonical final-report artifacts |
| Extension SDK | Versioned provider, tool, and policy contracts discovered through standard entry points |

## NEXUS differentiators

1. Verified completion: every actual mutation is re-read, SHA-256 fingerprinted, compiler-checked where applicable, and logged to append-only JSONL. Unsupported test-success prose is flagged.
2. Anti-slopsquatting: dependencies and install commands are checked against PyPI, npm, crates.io, or the Go module proxy before execution.
3. Trust re-verification: changing one byte of project instructions, MCP config, hooks, or plugin metadata invalidates approval.
4. Free-first routing: each subtask records why it stayed local or went directly to the Ceiling, including Nova retries and escalation evidence.

## Product-boundary gaps

| Capability | Status |
|---|---|
| Notebook cell editing | Files can be edited, but `.ipynb` cell-aware editing is not implemented |
| Scheduled/monitor-triggered prompts | Background processes exist; cron/routine orchestration is not implemented |
| Agent teams with peer messaging | Isolated subagents exist; independent peer-to-peer agent teams are not implemented |
| IDE plugins, voice, mobile, Remote Control, browser computer use | External product surfaces; not simulated |
| Hosted artifacts, PR cloud review, enterprise analytics/policy delivery | Anthropic/cloud services; not simulated |

These are not part of the Nexus 3.2.1 open CLI specification. Host-dependent
features such as LSP, Tree-sitter, Playwright, compilers, services, and native
sandboxing are reported as available, unavailable, or policy-only at runtime;
Nexus never converts an unavailable check into verified success.
