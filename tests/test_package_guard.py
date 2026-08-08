"""Tests for Package Guard functionality."""

import json
import urllib.error
from unittest.mock import patch

from nexus.package_guard import PackageCheck, PackageGuard


def test_package_guard_check_file_change():
    guard = PackageGuard()
    # Replace the resolver for offline testing
    guard.resolver = lambda reg, name: PackageCheck(name, reg, "pass", "ok")

    checks = guard.check_file_change(
        "requirements.txt",
        "requests==2.31.0\nnexus-test>1.0.0"
    )
    assert len(checks) == 2
    assert checks[0].name == "nexus-test"
    assert checks[1].name == "requests"


def test_package_guard_check_command():
    guard = PackageGuard()
    guard.resolver = lambda reg, name: PackageCheck(name, reg, "blocked", "bad")

    checks = guard.check_command("pip install malicious-pkg")
    assert len(checks) == 1
    assert checks[0].name == "malicious-pkg"
    assert checks[0].status == "blocked"
    assert checks[0].blocked is True


def test_package_guard_lookup_pass():
    guard = PackageGuard()
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = mock_urlopen.return_value.__enter__.return_value
        mock_response.read.return_value = json.dumps({
            "info": {"name": "requests"},
            "releases": {"2.31.0": [{"upload_time_iso_8601": "2020-01-01T00:00:00Z"}]}
        }).encode("utf-8")
        
        check = guard._lookup("pypi", "requests")
        assert check.status == "pass"


def test_package_guard_lookup_404():
    guard = PackageGuard()
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 404, "Not Found", {}, None)):
        check = guard._lookup("pypi", "nonexistent-pkg")
        assert check.status == "blocked"


def test_package_guard_lookup_unverified():
    guard = PackageGuard()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network error")):
        check = guard._lookup("pypi", "requests")
        assert check.status == "unverified"
        assert check.requires_confirmation is True
