"""Cryptographic sealing for independent competitive benchmark evaluation.

The benchmark report is evidence, never a trust root.  Verification therefore
requires an evaluator key supplied out-of-band by the caller.  A public key
embedded in a report is retained only for diagnostics and cannot authorize the
report that contains it.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

ATTESTATION_SCHEMA = "noryx.superiority-attestation.v2"


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def attestation_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact immutable payload an independent evaluator signs."""
    qualification = report.get("qualification") or {}
    if not isinstance(qualification, Mapping):
        qualification = {}
    return {
        "schema_version": ATTESTATION_SCHEMA,
        "campaign_id": str(qualification.get("campaign_id", "")),
        "dataset_revision": str(qualification.get("dataset_revision", "")),
        "evaluator_id": str(qualification.get("evaluator_id", "")),
        "manifest_sha256": str(report.get("manifest_sha256", "")),
        "oracle_bundle_sha256": str(qualification.get("oracle_bundle_sha256", "")),
        "budget_policy_sha256": str(qualification.get("budget_policy_sha256", "")),
        "environment_manifest_sha256": str(qualification.get("environment_manifest_sha256", "")),
        "task_results_sha256": _sha256_json(report.get("task_results") or []),
        "summary_sha256": _sha256_json(report.get("summary") or {}),
    }


def _load_private_key(value: bytes):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Ed25519 signing requires the 'qualification' extra.") from exc
    stripped = value.strip()
    if stripped.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(stripped, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Evaluator key must be an Ed25519 private key")
        return key
    try:
        raw = base64.b64decode(stripped, validate=True)
    except ValueError:
        raw = stripped
    if len(raw) != 32:
        raise ValueError("Raw Ed25519 private key must contain exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def _load_public_key(value: bytes | str) -> bytes:
    """Return a raw Ed25519 public key from PEM, base64, or raw bytes."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Ed25519 verification requires the 'qualification' extra.") from exc
    encoded = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    stripped = encoded.strip()
    if stripped.startswith(b"-----BEGIN"):
        key = serialization.load_pem_public_key(stripped)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Trusted evaluator key must be an Ed25519 public key")
        return key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    try:
        raw = base64.b64decode(stripped, validate=True)
    except ValueError:
        raw = stripped
    if len(raw) != 32:
        raise ValueError("Trusted Ed25519 public key must contain exactly 32 bytes")
    return raw


def evaluator_key_fingerprint(value: bytes | str) -> str:
    """Return the stable SHA-256 fingerprint of a trusted evaluator key."""
    return hashlib.sha256(_load_public_key(value)).hexdigest()


def load_trust_policy(path: str | Path) -> tuple[dict[str, bytes | str], dict[str, Any]]:
    """Load evaluator keys and campaign identity from an external policy file."""
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Competitive trust policy must be a JSON object")
    evaluators = payload.get("trusted_evaluators")
    campaign = payload.get("campaign")
    if not isinstance(evaluators, Mapping) or not evaluators:
        raise ValueError("Competitive trust policy requires trusted_evaluators")
    if not isinstance(campaign, Mapping) or not campaign:
        raise ValueError("Competitive trust policy requires campaign identity")
    trusted = {str(key): value for key, value in evaluators.items()}
    for evaluator_id, key in trusted.items():
        if not evaluator_id.strip():
            raise ValueError("Trusted evaluator IDs cannot be blank")
        evaluator_key_fingerprint(key)
    return trusted, dict(campaign)


def attach_evaluator_signature(
    report: Mapping[str, Any],
    *,
    private_key: bytes,
    evaluator_id: str,
) -> dict[str, Any]:
    """Return a copy signed by the independent evaluator's Ed25519 key."""
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Ed25519 signing requires the 'qualification' extra.") from exc
    signed = copy.deepcopy(dict(report))
    qualification = dict(signed.get("qualification") or {})
    qualification["evaluator_id"] = evaluator_id.strip()
    qualification["signature_algorithm"] = "ed25519"
    signed["qualification"] = qualification
    key = _load_private_key(private_key)
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = key.sign(_canonical_json(attestation_payload(signed)))
    qualification["evaluator_public_key"] = base64.b64encode(public_raw).decode("ascii")
    qualification["evaluator_key_fingerprint"] = hashlib.sha256(public_raw).hexdigest()
    qualification["evaluator_signature"] = base64.b64encode(signature).decode("ascii")
    return signed


def verify_evaluator_signature(
    report: Mapping[str, Any],
    *,
    trusted_public_keys: Mapping[str, bytes | str] | None,
) -> tuple[bool, str]:
    """Verify a report against an evaluator trust store supplied out-of-band."""
    qualification = report.get("qualification") or {}
    if not isinstance(qualification, Mapping):
        return False, "qualification mapping missing"
    if str(qualification.get("signature_algorithm", "")).lower() != "ed25519":
        return False, "signature algorithm must be ed25519"
    evaluator_id = str(qualification.get("evaluator_id", "")).strip()
    if not evaluator_id:
        return False, "evaluator identity missing"
    if not trusted_public_keys:
        return False, "trusted evaluator keys were not supplied out-of-band"
    trusted_value = trusted_public_keys.get(evaluator_id)
    if trusted_value is None:
        return False, f"evaluator is not trusted: {evaluator_id}"
    try:
        public_raw = _load_public_key(trusted_value)
        signature = base64.b64decode(
            str(qualification.get("evaluator_signature", "")), validate=True
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        return False, f"trusted evaluator key or signature is invalid: {exc}"
    if len(public_raw) != 32 or len(signature) != 64:
        return False, "invalid Ed25519 key or signature length"
    fingerprint = hashlib.sha256(public_raw).hexdigest()
    declared_fingerprint = str(qualification.get("evaluator_key_fingerprint", "")).lower()
    if declared_fingerprint != fingerprint:
        return False, "report evaluator fingerprint does not match the trusted key"
    embedded_key = str(qualification.get("evaluator_public_key", "")).strip()
    if embedded_key:
        try:
            if _load_public_key(embedded_key) != public_raw:
                return False, "report evaluator key does not match the trusted key"
        except (ValueError, RuntimeError) as exc:
            return False, f"report evaluator key is invalid: {exc}"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError:
        return False, "Ed25519 verifier unavailable; install nexusai-cli[qualification]"
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(
            signature, _canonical_json(attestation_payload(report))
        )
    except InvalidSignature:
        return False, "evaluator signature does not match the benchmark evidence"
    except ValueError as exc:
        return False, f"invalid evaluator public key: {exc}"
    return True, "verified Ed25519 signature against the external evaluator trust store"


def sign_report_file(
    report_path: str | Path,
    private_key_path: str | Path,
    *,
    evaluator_id: str,
    output_path: str | Path | None = None,
) -> Path:
    source = Path(report_path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    signed = attach_evaluator_signature(
        payload,
        private_key=Path(private_key_path).expanduser().read_bytes(),
        evaluator_id=evaluator_id,
    )
    target = Path(output_path).expanduser().resolve() if output_path else source
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
