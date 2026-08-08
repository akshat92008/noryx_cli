import json
from unittest.mock import MagicMock, patch

import pytest

from nexus.github import GitHubError, GitHubIntegration


def test_github_list_issues():
    mock_output = json.dumps([{"number": 1, "title": "Test Issue", "state": "open"}])
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output
        mock_run.return_value = mock_result

        issues = GitHubIntegration.list_issues()
        assert len(issues) == 1
        assert issues[0]["number"] == 1
        assert issues[0]["title"] == "Test Issue"


def test_github_view_issue():
    mock_output = json.dumps(
        {
            "number": 42,
            "title": "Fix bug",
            "body": "There is a bug",
            "comments": [{"body": "I agree", "author": {"login": "user1"}}],
            "state": "open",
            "url": "https://github.com/test/test/issues/42",
        }
    )
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output
        mock_run.return_value = mock_result

        issue = GitHubIntegration.view_issue("42")
        assert issue["number"] == 42
        assert len(issue["comments"]) == 1
        assert issue["comments"][0]["author"]["login"] == "user1"


def test_github_create_pr():
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "https://github.com/test/test/pull/1"
        mock_run.return_value = mock_result

        url = GitHubIntegration.create_pull_request("Test PR", "Test Body", "main")
        assert url == "https://github.com/test/test/pull/1"


def test_github_error():
    with patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Validation failed"
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        with pytest.raises(GitHubError, match="Validation failed"):
            GitHubIntegration.list_issues()
