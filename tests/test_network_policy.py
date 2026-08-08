"""Fail-closed DNS and address-pinning contracts for outbound HTTP."""

from __future__ import annotations

import time
import urllib.request

from nexus.network_policy import NetworkPolicy, network_globally_disabled
from nexus.tools import _safe_urlopen, tool_web_fetch, tool_web_search


def _address_info(address: str):
    return [(2, 1, 6, "", (address, 443))]


def test_dns_failure_and_empty_answers_fail_closed():
    def failing(*_args):
        raise OSError("resolver unavailable")

    failure = NetworkPolicy(resolver=failing).check_url("https://example.test")
    empty = NetworkPolicy(resolver=lambda *_args: []).check_url("https://example.test")

    assert failure is not None and failure.category == "dns_error"
    assert empty is not None and empty.category == "dns_empty"


def test_dns_timeout_is_bounded_and_does_not_leave_a_blocking_worker():
    def blocked(*_args):
        time.sleep(1)
        return _address_info("93.184.216.34")

    started = time.monotonic()
    violation = NetworkPolicy(
        resolver=blocked,
        dns_timeout_seconds=0.02,
    ).check_url("https://example.test")

    assert time.monotonic() - started < 0.25
    assert violation is not None and violation.category == "dns_timeout"


def test_every_dns_answer_must_be_public():
    policy = NetworkPolicy(
        resolver=lambda *_args: [
            *_address_info("93.184.216.34"),
            *_address_info("127.0.0.1"),
        ]
    )

    violation = policy.check_url("https://example.test")

    assert violation is not None and violation.category == "loopback"


def test_userinfo_and_non_public_special_ranges_are_blocked():
    userinfo = NetworkPolicy().check_url_syntax("https://user:password@example.com")
    carrier_nat = NetworkPolicy(
        resolver=lambda *_args: _address_info("100.64.0.1")
    ).check_url("https://example.test")

    assert userinfo is not None and userinfo.category == "userinfo"
    assert carrier_nat is not None and carrier_nat.category == "special_range"


def test_safe_urlopen_connects_to_the_validated_address(monkeypatch):
    observed = {}

    class Response:
        status = 200
        reason = "OK"
        headers = {"Content-Type": "text/plain"}

        def read(self, _amount=None):
            return b"ok"

        def close(self):
            return None

    class Connection:
        def __init__(self, hostname, address, port, *, timeout):
            observed.update(
                hostname=hostname,
                address=address,
                port=port,
                timeout=timeout,
            )

        def request(self, method, path, body=None, headers=None):
            observed.update(method=method, path=path, headers=headers)

        def getresponse(self):
            return Response()

        def close(self):
            return None

    monkeypatch.setattr("nexus.tools.tools_impl._PinnedHTTPSConnection", Connection)
    policy = NetworkPolicy(resolver=lambda *_args: _address_info("93.184.216.34"))
    request = urllib.request.Request("https://example.test/health?deep=1")

    with _safe_urlopen(request, timeout=2, policy=policy) as response:
        assert response.read() == b"ok"

    assert observed["hostname"] == "example.test"
    assert observed["address"] == "93.184.216.34"
    assert observed["path"] == "/health?deep=1"
    assert observed["headers"]["Host"] == "example.test"


def test_global_network_kill_switch_blocks_web_tools(monkeypatch):
    monkeypatch.setenv("NEXUS_DISABLE_NETWORK", "true")

    assert network_globally_disabled() is True
    assert "network_disabled" in tool_web_fetch("https://example.com")
    assert "network_disabled" in tool_web_search("nexus coding agent")
