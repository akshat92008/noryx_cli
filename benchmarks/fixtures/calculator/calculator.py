"""Tiny deterministic benchmark fixture."""


def add(left: int, right: int) -> int:
    return left + right


def subtract(left: int, right: int) -> int:
    return left - right


def multiply(left: int, right: int) -> int:
    # Intentional benchmark defect: multiplication must not add.
    return left + right
