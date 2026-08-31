"""Exact diagnostic fingerprints for grouping."""

from __future__ import annotations

from typing import Hashable

from diagrun.diagnostics.model import Diagnostic
from diagrun.diagnostics.textutil import normalize_message


def fingerprint(diagnostic: Diagnostic) -> tuple[Hashable, ...]:
    """Return a high-confidence grouping key.

    Prefer kind+symbol (same header fault across TUs). Fall back to
    kind+location+normalized message.
    """
    if diagnostic.symbol:
        return (diagnostic.kind, diagnostic.symbol)
    loc = diagnostic.location
    message = normalize_message(diagnostic.message)
    if loc is not None:
        return (diagnostic.kind, loc.file, loc.line, loc.column, message)
    return (diagnostic.kind, message)
