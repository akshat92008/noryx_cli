"""Independent acceptance contract for the startup control-plane benchmark."""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def expect_raises(types, operation, label):
    try:
        operation()
    except types:
        return
    raise AssertionError(f"{label} did not raise {types}")


def main() -> None:
    from startup.app import create_app
    from startup.service import (
        AuthorizationError,
        CommerceService,
        ConflictError,
        NotFoundError,
        ValidationError,
    )

    tests = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert tests.returncode == 0, tests.stdout + "\n" + tests.stderr

    with tempfile.TemporaryDirectory(prefix="nexus-startup-acceptance-") as temp:
        database = Path(temp) / "startup.sqlite3"
        service = CommerceService(str(database))
        alpha = service.register_tenant("Alpha", "owner@alpha.test", "Alpha-pass-123!")
        beta = service.register_tenant("Beta", "owner@beta.test", "Beta-pass-123!")
        alpha_token = service.login(alpha["tenant_id"], "owner@alpha.test", "Alpha-pass-123!")
        beta_token = service.login(beta["tenant_id"], "owner@beta.test", "Beta-pass-123!")

        service.create_user(alpha_token, "viewer@alpha.test", "Viewer-pass-123!", "viewer")
        viewer_token = service.login(alpha["tenant_id"], "viewer@alpha.test", "Viewer-pass-123!")
        expect_raises(
            AuthorizationError,
            lambda: service.create_product(viewer_token, "VIEW", "Denied", 100, 1),
            "viewer mutation",
        )

        service.create_product(alpha_token, "PRO-1", "Professional plan", 2500, 3)
        first = service.create_order(
            alpha_token,
            "checkout-001",
            [{"sku": "PRO-1", "quantity": 2}],
        )
        repeated = service.create_order(
            alpha_token,
            "checkout-001",
            [{"sku": "PRO-1", "quantity": 2}],
        )
        assert first["order_id"] == repeated["order_id"]
        assert first["total_cents"] == 5000
        assert service.get_product(alpha_token, "PRO-1")["stock"] == 1
        expect_raises(
            ConflictError,
            lambda: service.create_order(
                alpha_token,
                "checkout-001",
                [{"sku": "PRO-1", "quantity": 1}],
            ),
            "conflicting idempotency request",
        )
        expect_raises(
            (AuthorizationError, NotFoundError),
            lambda: service.get_order(beta_token, first["order_id"]),
            "cross-tenant order read",
        )
        expect_raises(
            (ValidationError, ConflictError),
            lambda: service.create_order(
                alpha_token,
                "checkout-002",
                [{"sku": "PRO-1", "quantity": 2}],
            ),
            "insufficient stock",
        )
        assert service.get_product(alpha_token, "PRO-1")["stock"] == 1

        # Persistence and credential handling survive a process-style restart.
        restarted = CommerceService(str(database))
        restarted_token = restarted.login(alpha["tenant_id"], "owner@alpha.test", "Alpha-pass-123!")
        assert restarted.get_order(restarted_token, first["order_id"])["total_cents"] == 5000
        assert restarted.health()["status"] == "ok"
        raw_database = database.read_bytes()
        assert b"Alpha-pass-123!" not in raw_database
        assert b"Beta-pass-123!" not in raw_database

        events = restarted.list_audit_events(restarted_token)
        actions = {str(item.get("action", "")) for item in events}
        assert any("tenant" in action or "register" in action for action in actions)
        assert any("product" in action for action in actions)
        assert any("order" in action for action in actions)

        # Two independent service instances contend for the final unit.
        restarted.create_product(restarted_token, "LAST-1", "Last unit", 999, 1)
        barrier = threading.Barrier(2)
        successes = []
        failures = []

        def buy(key: str) -> None:
            contender = CommerceService(str(database))
            token = contender.login(alpha["tenant_id"], "owner@alpha.test", "Alpha-pass-123!")
            barrier.wait(timeout=10)
            try:
                successes.append(
                    contender.create_order(
                        token,
                        key,
                        [{"sku": "LAST-1", "quantity": 1}],
                    )
                )
            except (ConflictError, ValidationError, sqlite3.OperationalError) as exc:
                failures.append(type(exc).__name__)

        threads = [threading.Thread(target=buy, args=(f"race-{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)
        assert len(successes) == 1, (successes, failures)
        assert CommerceService(str(database)).get_product(restarted_token, "LAST-1")["stock"] == 0

        # Exercise the deployable WSGI health surface.
        app = create_app(str(database))
        response_meta = {}

        def start_response(status, headers):
            response_meta["status"] = status
            response_meta["headers"] = dict(headers)

        body = b"".join(
            app(
                {
                    "REQUEST_METHOD": "GET",
                    "PATH_INFO": "/health",
                    "QUERY_STRING": "",
                    "SERVER_NAME": "localhost",
                    "SERVER_PORT": "80",
                    "SERVER_PROTOCOL": "HTTP/1.1",
                    "wsgi.version": (1, 0),
                    "wsgi.url_scheme": "http",
                    "wsgi.input": io.BytesIO(b""),
                    "wsgi.errors": io.StringIO(),
                    "wsgi.multithread": False,
                    "wsgi.multiprocess": False,
                    "wsgi.run_once": False,
                },
                start_response,
            )
        )
        assert response_meta["status"].startswith("200")
        assert json.loads(body.decode("utf-8"))["status"] == "ok"

    print("STARTUP_CONTROL_PLANE_ACCEPTANCE=PASS")


if __name__ == "__main__":
    main()
