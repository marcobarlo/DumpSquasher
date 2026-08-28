"""Runtime configuration for capture injection and reducer policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


def env_bool(name: str, default: bool, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Parse a boolean environment variable.

    True: ``1``, ``true``, ``yes``, ``on``. False: ``0``, ``false``, ``no``, ``off``.
    """
    source = os.environ if environ is None else environ
    raw = source.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"invalid boolean for {name}: {raw!r}")


@dataclass(frozen=True)
class DiagrunConfig:
    """User-visible settings.

    ``inject_diagnostics`` (default True): add codegen-neutral compiler flags
    such as ``-fdiagnostics-format=json`` through argv/env/wrappers.

    ``collapse_parse_recovery`` (default False): when True, hide likely
    follow-on syntax-recovery errors. Default prefers false negatives.
    """

    inject_diagnostics: bool = True
    collapse_parse_recovery: bool = False

    @classmethod
    def resolve(
        cls,
        *,
        inject_diagnostics: Optional[bool] = None,
        collapse_parse_recovery: Optional[bool] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> DiagrunConfig:
        """Build config from explicit CLI values, else environment, else defaults."""
        inject = (
            inject_diagnostics
            if inject_diagnostics is not None
            else env_bool("DIAGRUN_INJECT_DIAGNOSTICS", True, env)
        )
        collapse = (
            collapse_parse_recovery
            if collapse_parse_recovery is not None
            else env_bool("DIAGRUN_COLLAPSE_PARSE_RECOVERY", False, env)
        )
        return cls(inject_diagnostics=inject, collapse_parse_recovery=collapse)

    def to_dict(self) -> dict[str, bool]:
        return {
            "inject_diagnostics": self.inject_diagnostics,
            "collapse_parse_recovery": self.collapse_parse_recovery,
        }
