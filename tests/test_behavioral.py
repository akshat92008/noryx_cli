import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from nexus.behavioral import (
    ApiProbeSpec,
    ApiVerifier,
    BrowserProbeSpec,
    BrowserStep,
    BrowserVerifier,
    DatabaseVerifier,
    ProbeStatus,
    SecurityScanner,
)


def test_api_verifier_local_only():
    verifier = ApiVerifier()
    # allow_external is False by default
    res = verifier.verify(ApiProbeSpec(method="GET", url="https://google.com"))
    assert res.status == ProbeStatus.BLOCKED
    assert "network approval" in res.summary


def test_api_verifier_success():
    verifier = ApiVerifier()
    spec = ApiProbeSpec(
        method="GET",
        url="http://127.0.0.1:8000/api",
        expected_status=200,
        expected_json={"status": "ok"},
    )

    with patch("httpx.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok"}
        mock_resp.headers = {"content-type": "application/json"}
        mock_req.return_value = mock_resp

        res = verifier.verify(spec)
        assert res.status == ProbeStatus.PASSED


def test_api_verifier_failure():
    verifier = ApiVerifier()
    spec = ApiProbeSpec(
        method="GET",
        url="http://127.0.0.1:8000/api",
        expected_status=200,
        expected_json={"status": "ok"},
    )

    with patch("httpx.request") as mock_req:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.json.return_value = {"status": "error"}
        mock_resp.headers = {"content-type": "application/json"}
        mock_req.return_value = mock_resp

        res = verifier.verify(spec)
        assert res.status == ProbeStatus.FAILED
        assert "expected status 200" in res.summary


def test_database_verifier_sqlite(tmp_path):
    import sqlite3

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.close()

    verifier = DatabaseVerifier()
    res = verifier.verify_sqlite(db_path)
    assert res.status == ProbeStatus.PASSED
    assert "users" in res.evidence["tables"]


def test_database_verifier_migration_risks():
    verifier = DatabaseVerifier()
    sql = "ALTER TABLE users DROP COLUMN email;"
    findings = verifier.migration_risks(sql)
    assert len(findings) > 0
    assert findings[0]["kind"] == "drop"


def test_security_scanner(tmp_path):
    # Setup files
    (tmp_path / "safe.py").write_text("print('Hello world')")
    (tmp_path / "unsafe.py").write_text("API_KEY = 'sk-1234567890abcdef1234567890abcdef'")

    scanner = SecurityScanner()
    res = scanner.scan(tmp_path, [tmp_path / "safe.py", tmp_path / "unsafe.py"])
    assert res.status == ProbeStatus.FAILED
    assert len(res.evidence["findings"]) == 1
    assert res.evidence["findings"][0]["kind"] == "hardcoded-credential"


def test_browser_verifier_unavailable():
    verifier = BrowserVerifier()
    # Without playwright installed or mocked
    with patch.dict("sys.modules", {"playwright.sync_api": None}):
        res = verifier.verify(BrowserProbeSpec(url="http://127.0.0.1/"))
        assert res.status == ProbeStatus.UNAVAILABLE


def test_browser_verifier_success():
    verifier = BrowserVerifier()
    spec = BrowserProbeSpec(
        url="http://127.0.0.1/", steps=(BrowserStep(action="click", selector="#btn"),)
    )

    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_page = MagicMock()

    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page

    # Context manager setup for sync_playwright()
    mock_sync_pw_cm = MagicMock()
    mock_sync_pw_cm.__enter__.return_value = mock_playwright

    playwright_module = ModuleType("playwright")
    sync_api_module = ModuleType("playwright.sync_api")
    sync_api_module.sync_playwright = MagicMock(return_value=mock_sync_pw_cm)
    playwright_module.sync_api = sync_api_module
    with patch.dict(
        sys.modules,
        {"playwright": playwright_module, "playwright.sync_api": sync_api_module},
    ):
        res = verifier.verify(spec)
        assert res.status == ProbeStatus.PASSED
        mock_page.goto.assert_called_with("http://127.0.0.1/", wait_until="networkidle")
        mock_page.locator.assert_called_with("#btn")
        mock_page.locator().click.assert_called_once()
