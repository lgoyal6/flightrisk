"""Importing this package registers every candidate signal.

Order of import does not matter; `registry.register` rejects duplicate names.
"""

from __future__ import annotations

from ..registry import REGISTRY, all_signals, signal_names  # noqa: F401
from . import balance, controls, funding, text  # noqa: F401

__all__ = [
    "REGISTRY",
    "all_signals",
    "signal_names",
    "balance",
    "controls",
    "funding",
    "text",
]
