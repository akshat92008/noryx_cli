"""
Offline-capable HTML Regression Dashboard generator for Nexus benchmark results.
"""

import json
from datetime import datetime
from pathlib import Path

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nexus Regression Dashboard</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --surface: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --success: #10b981;
            --danger: #ef4444;
            --border: #334155;
            --highlight: #3b82f6;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 2rem;
            line-height: 1.5;
        }}
        h1, h2, h3 {{ font-weight: 600; margin-top: 0; }}
        .header {{ border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 2rem; }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .kpi-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.5rem;
            text-align: center;
        }}
        .kpi-value {{ font-size: 2rem; font-weight: bold; margin: 0.5rem 0; color: var(--highlight); }}
        .kpi-label {{ color: var(--text-muted); font-size: 0.875rem; text-transform: uppercase; letter-spacing: 0.05em; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--surface);
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}
        th, td {{ padding: 1rem; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: rgba(0,0,0,0.2); font-weight: 600; color: var(--text-muted); text-transform: uppercase; font-size: 0.8rem; }}
        tr:last-child td {{ border-bottom: none; }}
        .status-badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
        }}
        .status-passed {{ background: rgba(16, 185, 129, 0.1); color: var(--success); border: 1px solid var(--success); }}
        .status-failed {{ background: rgba(239, 68, 68, 0.1); color: var(--danger); border: 1px solid var(--danger); }}
        .status-not-run {{ background: rgba(148, 163, 184, 0.1); color: var(--text-muted); border: 1px solid var(--text-muted); }}
        .cost {{ font-family: monospace; color: #cbd5e1; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Nexus Regression Dashboard</h1>
        <p style="color: var(--text-muted);">Generated on {generated_at} from {manifest_id}</p>
    </div>

    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">Success Rate</div>
            <div class="kpi-value">{success_rate}%</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Total Tasks</div>
            <div class="kpi-value">{total_tasks}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Total Cost</div>
            <div class="kpi-value">${total_cost}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Total Duration</div>
            <div class="kpi-value">{total_duration}s</div>
        </div>
    </div>

    <h2>Task Breakdown</h2>
    <table>
        <thead>
            <tr>
                <th>Task ID</th>
                <th>Category</th>
                <th>Status</th>
                <th>Agent State</th>
                <th>Tokens</th>
                <th>Cost</th>
                <th>Duration</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
</body>
</html>"""


class RegressionDashboard:
    """Generates offline-capable HTML dashboards from benchmark results."""

    @classmethod
    def generate(cls, input_path: str, output_path: str) -> None:
        path = Path(input_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Result file not found: {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in result file: {exc}") from exc

        schema_version = data.get("schema_version")
        result_schemas = {
            "nexus.benchmark-result.v1",
            "nexus.benchmark-result.v2",
            "nexus.benchmark-result.v3",
        }
        manifest_schemas = {"nexus.benchmark.v1"}
        if schema_version not in result_schemas | manifest_schemas:
            raise ValueError(f"Unsupported schema version: {schema_version}")

        if schema_version in manifest_schemas:
            tasks = data.get("tasks", [])
            summary = {
                "total": len(tasks),
                "passed": 0,
                "failed": 0,
                "total_duration_ms": 0,
                "estimated_cost_usd": 0.0,
            }
            results = [
                {
                    "task_id": task.get("id", "unknown"),
                    "category": task.get("category", "unspecified"),
                    "status": "NOT_RUN",
                    "agent_status": "NOT_RUN",
                    "duration_ms": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "estimated_cost_usd": 0.0,
                }
                for task in tasks
            ]
        else:
            summary = data.get("summary", {})
            results = data.get("results", [])

        total_tasks = summary.get("tasks", summary.get("total", 0))
        passed = summary.get("passed", 0)
        success_rate = round((passed / total_tasks * 100) if total_tasks else 0, 1)
        total_cost = f"{summary.get('total_cost_usd', summary.get('estimated_cost_usd', 0.0)):.4f}"
        total_duration = round(summary.get("total_duration_ms", 0) / 1000, 1)

        table_rows = []
        for res in results:
            status = res.get("status", "FAILED")
            if status == "PASSED":
                badge_class = "status-passed"
            elif status == "NOT_RUN":
                badge_class = "status-not-run"
            else:
                badge_class = "status-failed"
            cost = res.get("estimated_cost_usd")
            cost_str = f"${cost:.4f}" if cost is not None else "N/A"
            duration = round(res.get("duration_ms", 0) / 1000, 1)
            tokens = res.get("prompt_tokens", 0) + res.get("completion_tokens", 0)

            row = f"""
            <tr>
                <td>{res.get("task_id")}</td>
                <td>{res.get("category")}</td>
                <td><span class="status-badge {badge_class}">{status}</span></td>
                <td>{res.get("agent_status")}</td>
                <td class="cost">{tokens}</td>
                <td class="cost">{cost_str}</td>
                <td>{duration}s</td>
            </tr>"""
            table_rows.append(row)

        html = HTML_TEMPLATE.format(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            manifest_id=data.get("manifest_id", data.get("name", "Unknown Manifest")),
            success_rate=success_rate,
            total_tasks=total_tasks,
            total_cost=total_cost,
            total_duration=total_duration,
            table_rows="\\n".join(table_rows),
        )

        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
