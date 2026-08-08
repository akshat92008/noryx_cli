"""
Final Report Contract implementation for Nexus CLI.
Transforms canonical JSON reports into markdown/terminal summaries.
"""

import json
from pathlib import Path


class FinalReportGenerator:
    """Generates a human-readable markdown report from a final_report.json."""

    @classmethod
    def generate(cls, report_json_path: str | Path) -> str:
        path = Path(report_json_path).expanduser().resolve()
        if not path.is_file():
            return f"❌ No final report found at {path}"

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return f"❌ Invalid JSON in final report: {exc}"

        status = data.get("status", "UNKNOWN")
        objective = data.get("objective", "No objective provided")

        lines = []
        lines.append("# Nexus Run Report")
        lines.append(f"**Status:** {status}")
        lines.append(f"**Objective:** {objective}\n")

        # Acceptance Criteria
        criteria = data.get("acceptance_criteria", [])
        if criteria:
            lines.append("## Acceptance Criteria")
            for c in criteria:
                c_status = c.get("status", "UNKNOWN")
                icon = "✅" if c_status == "VERIFIED" else "❌" if c_status == "FAILED" else "⚠️"
                lines.append(f"- {icon} {c.get('description', 'Unknown criterion')} ({c_status})")
            lines.append("")

        # Work Completed & Files Changed
        work = data.get("work_completed", [])
        if work:
            lines.append("## Work Completed")
            for w in work:
                lines.append(f"- {w}")
            lines.append("")

        files = data.get("files_changed", [])
        if files:
            lines.append("## Files Changed")
            for f in files:
                lines.append(f"- `{f}`")
            lines.append("")

        # Checks & Evidence
        checks = data.get("checks", [])
        if checks:
            lines.append("## Executed Checks")
            for ch in checks:
                ch_status = "✅" if ch.get("success") else "❌"
                lines.append(f"- {ch_status} {ch.get('command', ch.get('name', 'Unknown'))}")
            lines.append("")

        skipped = data.get("checks_skipped", [])
        if skipped:
            lines.append("## Skipped Checks")
            for s in skipped:
                lines.append(f"- ⚠️ {s}")
            lines.append("")

        # Analytics
        lines.append("## Trace & Analytics")
        costs = data.get("costs", {})

        total_usd = costs.get("estimated_cost_usd", 0.0)

        lines.append(f"- **Cost:** ${total_usd:.4f}")

        providers = data.get("model_providers", [])
        if providers:
            lines.append(f"- **Models:** {', '.join(providers)}")

        network = data.get("network_calls", [])
        if network:
            lines.append(f"- **Network Calls:** {len(network)}")

        perms = data.get("permissions_used", [])
        if perms:
            lines.append(f"- **Permissions:** {', '.join(perms)}")

        lines.append("")

        # Risks
        risks = data.get("remaining_risks", [])
        if risks:
            lines.append("## Remaining Risks & Assumptions")
            for r in risks:
                lines.append(f"- {r}")
            for a in data.get("assumptions", []):
                lines.append(f"- Assumption: {a}")
            lines.append("")

        return "\n".join(lines)
