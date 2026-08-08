"""Compatibility shim: ``nexus.runtime.engine`` → ``nexus.runtime.kernel``.

Tests and external code that import ``ExecutionEngine`` from this module
continue to work unchanged.  The canonical class is
:class:`nexus.runtime.kernel.ExecutionKernel`.
"""

from nexus.runtime.kernel import ExecutionKernel as ExecutionEngine  # noqa: F401

__all__ = ["ExecutionEngine"]
