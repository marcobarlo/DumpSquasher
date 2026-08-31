"""Bounded source-context snippets attached to diagnostics.

Plan.md §1.2 rule 5: a compact ``message`` + ``location`` is not always
enough to fix a bug (e.g. a duplicated closing brace produces "expected
declaration before '}' token" with nothing showing the duplicate). A raw
compiler dump gives that context away for free; the structured root must
too, or the model ends up spending a full extra ``diagrun_build`` round
trip just to see the lines it already had.

Snippets are read straight from the checked-out source (already on disk,
no extra process or network cost) and hard-capped so they cannot reopen
the byte budget this module exists to protect.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

from diagrun.diagnostics.model import Diagnostic, Location

MAX_SNIPPET_BYTES = 160
CONTEXT_LINES = 1


def read_snippet(cwd: Optional[str], location: Location) -> Optional[str]:
    """Return up to ``2 * CONTEXT_LINES + 1`` source lines around ``location``.

    Bounded to ``MAX_SNIPPET_BYTES``. Returns ``None`` on any I/O failure
    (missing file, permission error, binary content, no ``cwd``) — a missing
    snippet must degrade gracefully, never raise.
    """
    if not location.file:
        return None
    path = location.file if os.path.isabs(location.file) else os.path.join(cwd or "", location.file)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    if not lines:
        return None
    start = max(0, location.line - 1 - CONTEXT_LINES)
    end = min(len(lines), location.line + CONTEXT_LINES)
    if start >= end:
        return None
    chunk = "".join(lines[start:end]).rstrip("\n")
    if not chunk.strip():
        return None
    if len(chunk) > MAX_SNIPPET_BYTES:
        chunk = chunk[:MAX_SNIPPET_BYTES].rstrip() + "\u2026"
    return chunk


def attach_snippets(diagnostics: Iterable[Diagnostic], cwd: Optional[str]) -> None:
    """Populate ``snippet`` in place for diagnostics that have a location."""
    for diagnostic in diagnostics:
        if diagnostic.snippet or diagnostic.location is None:
            continue
        diagnostic.snippet = read_snippet(cwd, diagnostic.location)
