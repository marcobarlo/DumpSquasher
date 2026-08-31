"""Identify build-system consequence lines (Make/Ninja/ld wrappers)."""

from __future__ import annotations

import re

_BUILD_LINE = re.compile(
    r"^(?:"
    r"make(?:\[\d+\])?:\s+\*\*\*"
    r"|make:\s+Target "
    r"|ninja:\s+build stopped"
    r"|collect2:"
    r"|compilation terminated\."
    r")"
)


def is_build_system_line(line: str) -> bool:
    """Return True when ``line`` is a Make/Ninja/ld wrapper, not a compiler diagnostic."""
    stripped = line.strip()
    if not stripped:
        return False
    return _BUILD_LINE.match(stripped) is not None


def count_build_system_lines(text: str) -> int:
    """Count high-confidence build-system consequence lines."""
    return sum(1 for line in text.splitlines() if is_build_system_line(line))
