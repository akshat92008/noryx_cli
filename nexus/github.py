"""
GitHub integration subsystem via the `gh` CLI.
"""

import json
import os
import subprocess
from typing import Any


class GitHubError(Exception):
    """Raised when a GitHub CLI operation fails."""

    pass


class GitHubIntegration:
    """Wrapper for the `gh` CLI tool."""

    @staticmethod
    def _run_gh(args: list[str]) -> str:
        """Run a gh command and return stdout. Raises GitHubError on failure."""
        try:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                ["gh"] + args,
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except OSError as exc:
            raise GitHubError("GitHub CLI (`gh`) is required but not found.") from exc

        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            raise GitHubError(f"GitHub CLI error: {err}")

        return result.stdout.strip()

    @classmethod
    def list_issues(cls, limit: int = 10, state: str = "open") -> list[dict[str, Any]]:
        """List repository issues."""
        out = cls._run_gh(
            [
                "issue",
                "list",
                "--state",
                state,
                "--limit",
                str(limit),
                "--json",
                "number,title,state,createdAt",
            ]
        )
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []

    @classmethod
    def view_issue(cls, number: str) -> dict[str, Any]:
        """View a specific issue with comments."""
        out = cls._run_gh(
            ["issue", "view", str(number), "--json", "number,title,body,comments,url,state"]
        )
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {}

    @classmethod
    def create_pull_request(cls, title: str, body: str, base_branch: str = "") -> str:
        """Create a pull request from the current branch. Returns the PR URL."""
        args = ["pr", "create", "--title", title, "--body", body]
        if base_branch:
            args.extend(["--base", base_branch])
        out = cls._run_gh(args)
        return out

    @classmethod
    def view_pr(cls, number: str = "") -> dict[str, Any]:
        """View a specific PR or current branch PR with comments."""
        args = ["pr", "view"]
        if number:
            args.append(str(number))
        args.extend(["--json", "number,title,body,comments,url,state"])
        out = cls._run_gh(args)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {}
