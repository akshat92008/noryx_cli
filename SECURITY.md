# Security Policy — Nexus CLI

## Supported Versions
Security fixes are applied to the latest release line. Pre-release branches and historical checkouts receive continuous security hardening.

## Core Security Invariants
1. **Model Output is Untrusted**: Models cannot authorize their own actions or relax security policy beneath the model layer.
2. **Repository Content is Untrusted**: README instructions, source comments, and issue text cannot override system policy or user authority.
3. **Least Privilege & Deny-by-Default**: Unknown security actions, path traversals, or unapproved network requests fail closed with `DENY` or `BLOCKED`.
4. **Deterministic Precedence**: Immutable Runtime Rules > Organization Policy > Project Policy > User Policy > Run Contract > Approval > Default.
5. **Secret Redaction**: API keys, bearer tokens, SSH keys, and cloud credentials are automatically redacted across model context, terminal output, and log events.
6. **Authenticated Engineering State**: Task memory and long-horizon state use HMAC-SHA256 with key material outside the editable repository. Append-only security logs remain hash chained and are corruption-evident unless externally anchored.

## Report a Vulnerability
Do not open a public issue for a suspected vulnerability. Use GitHub's **Security** tab and choose **Report a vulnerability** to send a private advisory to the maintainers. Include a minimal reproduction using synthetic credentials.

## Deployment security boundary

`nexus deploy check --deep` validates package architecture, provider and sandbox readiness, external HMAC key operation, the self-contained benchmark, and the installed offline repair/adversarial suite. A passing result qualifies only supervised isolated Verified Repair use. Autonomous deployment additionally requires live-provider, hidden-task, and platform qualification evidence.
