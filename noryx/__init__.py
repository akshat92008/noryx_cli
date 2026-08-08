"""Public Python facade for Noryx CLI 3.8.4.

The implementation remains in the ``nexus`` package for the 3.x compatibility
window.  Extending this package's search path makes canonical imports such as
``noryx.api`` and ``noryx.sandbox`` available without copying modules or
creating divergent runtime implementations.
"""

from pathlib import Path

from nexus import __version__

_implementation_path = Path(__file__).resolve().parent.parent / "nexus"
if _implementation_path.is_dir():
    __path__.append(str(_implementation_path))

__all__ = ["__version__"]
