"""Independent acceptance oracle for the operations ERP benchmark."""

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
    from erp.app import create_app
    from erp.service import (
        AuthorizationError,
        ConflictError,
        ERPService,
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

    with tempfile.TemporaryDirectory(prefix="nexus-erp-acceptance-") as temp:
        database = Path(temp) / "erp.sqlite3"
        service = ERPService(str(database))
        alpha = service.create_organization("Alpha", "owner@alpha.test", "Alpha-pass-123!")
        beta = service.create_organization("Beta", "owner@beta.test", "Beta-pass-123!")
        alpha_token = service.login(
            alpha["organization_id"], "owner@alpha.test", "Alpha-pass-123!"
        )
        beta_token = service.login(
            beta["organization_id"], "owner@beta.test", "Beta-pass-123!"
        )

        service.create_user(alpha_token, "auditor@alpha.test", "Audit-pass-123!", "auditor")
        auditor_token = service.login(
            alpha["organization_id"], "auditor@alpha.test", "Audit-pass-123!"
        )
        expect_raises(
            AuthorizationError,
            lambda: service.create_vendor(
                auditor_token, "Forbidden vendor", "denied@vendor.test"
            ),
            "auditor mutation",
        )

        vendor = service.create_vendor(alpha_token, "Parts Co", "orders@parts.test")
        purchase = service.create_purchase_order(
            alpha_token,
            "po-create-001",
            vendor["vendor_id"],
            [
                {"sku": "BOLT-1", "quantity": 4, "unit_cost_cents": 125},
                {"sku": "CASE-1", "quantity": 2, "unit_cost_cents": 750},
            ],
        )
        repeated_purchase = service.create_purchase_order(
            alpha_token,
            "po-create-001",
            vendor["vendor_id"],
            [
                {"sku": "BOLT-1", "quantity": 4, "unit_cost_cents": 125},
                {"sku": "CASE-1", "quantity": 2, "unit_cost_cents": 750},
            ],
        )
        assert purchase["purchase_order_id"] == repeated_purchase["purchase_order_id"]
        assert purchase["total_cents"] == 2000
        expect_raises(
            ConflictError,
            lambda: service.create_purchase_order(
                alpha_token,
                "po-create-001",
                vendor["vendor_id"],
                [{"sku": "BOLT-1", "quantity": 1, "unit_cost_cents": 125}],
            ),
            "conflicting purchase-order idempotency key",
        )

        expect_raises(
            (AuthorizationError, NotFoundError),
            lambda: service.approve_purchase_order(beta_token, purchase["purchase_order_id"]),
            "cross-tenant purchase-order mutation",
        )
        service.approve_purchase_order(alpha_token, purchase["purchase_order_id"])
        receipt = service.receive_inventory(
            alpha_token, "receive-001", purchase["purchase_order_id"]
        )
        repeated_receipt = service.receive_inventory(
            alpha_token, "receive-001", purchase["purchase_order_id"]
        )
        assert receipt == repeated_receipt
        assert service.get_inventory(alpha_token, "BOLT-1")["quantity"] == 4
        assert service.get_inventory(alpha_token, "CASE-1")["quantity"] == 2
        expect_raises(
            (AuthorizationError, NotFoundError),
            lambda: service.get_inventory(beta_token, "BOLT-1"),
            "cross-tenant inventory read",
        )

        invoice = service.create_invoice(
            alpha_token,
            "invoice-001",
            vendor["vendor_id"],
            purchase["purchase_order_id"],
            2000,
        )
        expect_raises(
            (ValidationError, ConflictError),
            lambda: service.create_invoice(
                alpha_token,
                "invoice-invalid",
                vendor["vendor_id"],
                purchase["purchase_order_id"],
                2001,
            ),
            "over-total invoice",
        )
        assert service.get_invoice(auditor_token, invoice["invoice_id"])["status"] != "paid"

        restarted = ERPService(str(database))
        restarted_token = restarted.login(
            alpha["organization_id"], "owner@alpha.test", "Alpha-pass-123!"
        )
        assert restarted.get_inventory(restarted_token, "BOLT-1")["quantity"] == 4
        assert restarted.health()["status"] == "ok"
        raw_database = database.read_bytes()
        assert b"Alpha-pass-123!" not in raw_database
        assert b"Beta-pass-123!" not in raw_database
        assert b"Audit-pass-123!" not in raw_database

        barrier = threading.Barrier(2)
        successes = []
        failures = []

        def pay(key: str) -> None:
            contender = ERPService(str(database))
            token = contender.login(
                alpha["organization_id"], "owner@alpha.test", "Alpha-pass-123!"
            )
            barrier.wait(timeout=10)
            try:
                successes.append(contender.pay_invoice(token, key, invoice["invoice_id"]))
            except (ConflictError, ValidationError, sqlite3.OperationalError) as exc:
                failures.append(type(exc).__name__)

        threads = [threading.Thread(target=pay, args=(f"pay-race-{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)
        assert len(successes) == 1, (successes, failures)
        paid = ERPService(str(database)).get_invoice(restarted_token, invoice["invoice_id"])
        assert paid["status"] == "paid"
        assert paid.get("paid_at")

        events = restarted.list_audit_events(restarted_token)
        actions = {str(item.get("action", "")) for item in events}
        assert any("vendor" in action for action in actions)
        assert any("purchase" in action or "order" in action for action in actions)
        assert any("invoice" in action for action in actions)
        assert any("payment" in action or "pay" in action for action in actions)

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

    print("OPERATIONS_ERP_ACCEPTANCE=PASS")


if __name__ == "__main__":
    main()
