"""
Deterministic Failure Signal Extraction for Nexus CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ExtractedSignal:
    primary_signal: str
    secondary_signals: list[str] = field(default_factory=list)
    probable_cascades: list[str] = field(default_factory=list)
    relevant_paths: list[str] = field(default_factory=list)
    stack_trace_frames: list[str] = field(default_factory=list)
    assertion_diff: str = ""
    exception_type: str = ""


class SignalExtractor:
    """Extracts deterministic structures from python, JS/TS, and generic CLI output."""

    @classmethod
    def extract(cls, raw_output: str) -> ExtractedSignal:
        output = raw_output or ""
        lines = output.splitlines()

        # Exception type extraction
        exc_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Panic|Fault)):?\s*(.*)", output)
        exception_type = exc_match.group(1) if exc_match else ""
        primary_msg = exc_match.group(0) if exc_match else (lines[0] if lines else "")

        # Stack trace frames
        frames = re.findall(r'File "[^"]+", line \d+, in [^\n]+', output)

        # Assertion diff
        assertion_diff = ""
        if "AssertionError" in output or "assert " in output:
            diff_lines = [line for line in lines if line.startswith(">") or line.startswith("E ") or "assert" in line]
            assertion_diff = "\n".join(diff_lines[:10])

        # Paths
        paths = re.findall(r'[a-zA-Z0-9_\-\./]+\.(?:py|js|ts|json)', output)
        uniq_paths = list(dict.fromkeys(paths))

        # Cascades & secondary signals
        secondary = [line.strip() for line in lines if "During handling of the above exception" in line or "Caused by" in line]
        cascades = [line.strip() for line in lines if "ModuleNotFoundError" in line or "ImportError" in line]

        return ExtractedSignal(
            primary_signal=primary_msg[:200],
            secondary_signals=secondary,
            probable_cascades=cascades,
            relevant_paths=uniq_paths[:10],
            stack_trace_frames=frames[:10],
            assertion_diff=assertion_diff,
            exception_type=exception_type,
        )
