"""Cryptographic sealing for independent competitive benchmark evaluation."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ATTESTATION_SCHEMA = "nexus.superiority-attestation.v1"


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
        "oracle_bundle_sha256": str(
            qualification.get("oracle_bundle_sha256", "")
        ),
        "budget_policy_sha256": str(
            qualification.get("budget_policy_sha256", "")
        ),
        "environment_manifest_sha256": str(
            qualification.get("environment_manifest_sha256", "")
        ),
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
        raise RuntimeError(
            "Ed25519 signing requires the 'qualification' extra."
        ) from exc
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
        raise RuntimeError(
            "Ed25519 signing requires the 'qualification' extra."
        ) from exc
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
    qualification["evaluator_public_key"] = base64.b64encode(public_raw).decode(
        "ascii"
    )
    qualification["evaluator_signature"] = base64.b64encode(signature).decode(
        "ascii"
    )
    return signed


def verify_evaluator_signature(report: Mapping[str, Any]) -> tuple[bool, str]:
    """Verify the independent evaluator signature without trusting report flags."""
    qualification = report.get("qualification") or {}
    if not isinstance(qualification, Mapping):
        return False, "qualification mapping missing"
    if str(qualification.get("signature_algorithm", "")).lower() != "ed25519":
        return False, "signature algorithm must be ed25519"
    try:
        public_raw = base64.b64decode(
            str(qualification.get("evaluator_public_key", "")), validate=True
        )
        signature = base64.b64decode(
            str(qualification.get("evaluator_signature", "")), validate=True
        )
    except (ValueError, TypeError):
        return False, "evaluator public key or signature is not valid base64"
    if len(public_raw) != 32 or len(signature) != 64:
        return False, "invalid Ed25519 key or signature length"
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
    return True, "verified Ed25519 evaluator signature"


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
    target.write_text(
        json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return target
