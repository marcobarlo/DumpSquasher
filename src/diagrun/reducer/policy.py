"""Reducer policy: conservative defaults, with explicit opt-in for aggression."""

from __future__ import annotations

from diagrun.config import DiagrunConfig


def retain_uncertain_dependents(config: DiagrunConfig) -> bool:
    """Return True when probable parse-recovery follow-ons must stay visible.

    Default config prefers false negatives (do not collapse). Set
    ``collapse_parse_recovery=True`` to hide them.
    """
    return not config.collapse_parse_recovery
