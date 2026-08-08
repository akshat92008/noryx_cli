"""
Provider Resilience, Error Normalization, Privacy & Risk Policy Governance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nexus.models import PrivacyClass, model_registry


class ProviderErrorClass(str, Enum):
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    RATE_LIMIT = "RATE_LIMIT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CONTEXT_LIMIT_EXCEEDED = "CONTEXT_LIMIT_EXCEEDED"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    UNKNOWN = "UNKNOWN"


@dataclass
class NormalizedProviderError:
    error_class: ProviderErrorClass
    raw_message: str
    retryable: bool
    retry_after_seconds: float | None = None
    requires_user_action: bool = False
    suggested_fallback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_class": self.error_class.value,
            "raw_message": self.raw_message,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "requires_user_action": self.requires_user_action,
            "suggested_fallback": self.suggested_fallback,
        }


class ProviderResilienceEngine:
    """Normalizes provider exceptions and enforces privacy and risk policies."""

    @classmethod
    def normalize_error(cls, error_input: Any) -> NormalizedProviderError:
        msg = str(error_input or "")
        norm = msg.lower()

        # 1. Auth Failures
        if any(w in norm for w in ("401", "unauthorized", "invalid api key", "authentication failed", "invalid_api_key")):
            return NormalizedProviderError(
                error_class=ProviderErrorClass.AUTHENTICATION_FAILURE,
                raw_message=msg,
                retryable=False,
                requires_user_action=True,
                suggested_fallback=None,
            )

        # 2. Quota Exhausted
        if any(w in norm for w in ("insufficient_quota", "quota exceeded", "billing", "credit balance")):
            return NormalizedProviderError(
                error_class=ProviderErrorClass.QUOTA_EXHAUSTED,
                raw_message=msg,
                retryable=False,
                requires_user_action=True,
                suggested_fallback="nova3b",
            )

        # 3. Rate Limits
        if any(w in norm for w in ("429", "rate limit", "too many requests", "resourceexhausted")):
            retry_after = cls._extract_retry_after(norm)
            return NormalizedProviderError(
                error_class=ProviderErrorClass.RATE_LIMIT,
                raw_message=msg,
                retryable=True,
                retry_after_seconds=retry_after or 2.0,
                requires_user_action=False,
                suggested_fallback="nova3b",
            )

        # 4. Model Unavailable
        if any(w in norm for w in ("404", "model not found", "model unavailable", "does not exist")):
            return NormalizedProviderError(
                error_class=ProviderErrorClass.MODEL_UNAVAILABLE,
                raw_message=msg,
                retryable=False,
                requires_user_action=False,
                suggested_fallback="deepseek-flash",
            )

        # 5. Context Limit Exceeded
        if any(w in norm for w in ("context_length_exceeded", "maximum context length", "prompt is too long")):
            return NormalizedProviderError(
                error_class=ProviderErrorClass.CONTEXT_LIMIT_EXCEEDED,
                raw_message=msg,
                retryable=False,
                requires_user_action=False,
                suggested_fallback="deepseek-v4",
            )

        # 6. Network Timeout
        if any(w in norm for w in ("timeout", "timed out", "connection error", "econnereset")):
            return NormalizedProviderError(
                error_class=ProviderErrorClass.NETWORK_TIMEOUT,
                raw_message=msg,
                retryable=True,
                retry_after_seconds=1.0,
                requires_user_action=False,
            )

        return NormalizedProviderError(
            error_class=ProviderErrorClass.UNKNOWN,
            raw_message=msg,
            retryable=False,
            requires_user_action=False,
        )

    @staticmethod
    def _extract_retry_after(norm_text: str) -> float | None:
        match = re.search(r"retry in ([0-9]+(?:\.[0-9]+)?)s", norm_text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None

    @classmethod
    def validate_privacy_policy(
        cls,
        model_name: str,
        policy: PrivacyClass,
    ) -> tuple[bool, str]:
        """Validate if target model satisfies project/user privacy constraints."""
        desc = model_registry.get_descriptor(model_name)
        if not desc:
            return True, "Unknown model"

        if policy == PrivacyClass.LOCAL_ONLY and not desc.local:
            return False, f"Privacy Violation: Policy is LOCAL_ONLY but model '{desc.display_name}' sends code to remote cloud."

        if policy == PrivacyClass.PRIVATE_INFRASTRUCTURE and not desc.local and desc.privacy_class not in (PrivacyClass.LOCAL_ONLY, PrivacyClass.PRIVATE_INFRASTRUCTURE):
            return False, f"Privacy Violation: Policy requires private infrastructure but '{desc.display_name}' uses public cloud."

        return True, "Privacy policy satisfied"


# Global singleton
resilience_engine = ProviderResilienceEngine()
