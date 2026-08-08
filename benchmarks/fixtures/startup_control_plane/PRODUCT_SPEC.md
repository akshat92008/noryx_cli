# Startup Control Plane — acceptance contract

Build a production-oriented, dependency-free Python service in this repository.
The initial user request is the only product prompt. Nexus may recover from its
own checkpoints, but no human clarification or follow-up prompt is allowed.

## Required package

Create a `startup` package with:

- `startup/service.py` containing `CommerceService` and the exceptions
  `AuthenticationError`, `AuthorizationError`, `ConflictError`,
  `NotFoundError`, and `ValidationError`.
- `startup/app.py` containing `create_app(db_path)`, which returns a WSGI app.
- `startup/cli.py` containing a usable administrative CLI.
- A SQLite schema/migration owned by the package.

Use only the Python standard library at runtime. All data must survive creating
a new `CommerceService` against the same database file.

## Service API

`CommerceService(db_path, clock=None)` must expose:

- `register_tenant(name, admin_email, admin_password) -> dict`
- `login(tenant_id, email, password) -> str`
- `create_user(admin_token, email, password, role) -> dict`
- `create_product(token, sku, name, price_cents, stock) -> dict`
- `get_product(token, sku) -> dict`
- `adjust_stock(token, sku, delta, reason) -> dict`
- `create_order(token, idempotency_key, items) -> dict`
- `get_order(token, order_id) -> dict`
- `list_audit_events(token) -> list[dict]`
- `health() -> dict`

Roles are `admin`, `operator`, and `viewer`. Viewers cannot mutate data.
Every tenant must be isolated, including authentication, products, orders,
idempotency keys, and audit records.

Passwords must be salted and hashed with a standard-library password KDF.
Tokens must be unguessable. Validate emails, prices, stock, quantities, roles,
and idempotency keys. Never use floating point for money.

Order creation must be one SQLite transaction. It must reject insufficient
stock without partially changing inventory. Repeating the same idempotency key
with the same request returns the original order without decrementing stock
again; using the key with different items raises `ConflictError`.

All mutations must append tenant-scoped audit events. Concurrent attempts to
buy the final unit must allow exactly one successful order.

## HTTP and operational surface

The WSGI app must provide `GET /health` and return JSON containing
`{"status": "ok"}` with HTTP 200. It may expose additional API routes.

Include a useful `README.md`, a `pyproject.toml`, and at least three executable
test files under `tests/`. The following must pass without network access:

```bash
python -m unittest discover -s tests -v
python acceptance.py
```

Do not modify this specification or `acceptance.py`.
