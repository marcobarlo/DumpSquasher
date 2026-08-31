"""Compact JSON encoding for agent-facing results."""

from __future__ import annotations

import json
from typing import Any, Mapping

from diagrun.diagnostics.model import BuildResult, RunStatus


def dumps_compact(payload: Mapping[str, Any]) -> str:
    """Encode JSON with no pretty-print and no ASCII escaping."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def agent_payload(result: BuildResult, run_id: str, *, no_progress: bool = False) -> dict[str, Any]:
    """Slim build result: roots the model can act on, no fetch-the-log bait.

    Omits inject/reducer/argv/cwd and the ``diagnostics: null`` + hint pair that
    previously bloated context and instructed the model to call get_raw.

    ``no_progress=True`` (plan.md §1.2 rule 7) means this failure's roots are
    identical to the previous ``diagrun_build`` call in the same directory —
    the last edit did not change anything the agent needs to know about.
    """
    roots = []
    for group in result.roots:
        item = group.to_dict(compact=True)
        if item.get("collapsed_diagnostics") == 0:
            item.pop("collapsed_diagnostics", None)
        if item.get("confidence") == 1.0:
            item.pop("confidence", None)
        if item.get("affected_translation_units") == 1:
            item.pop("affected_translation_units", None)
        roots.append(item)
    payload: dict[str, Any] = {
        "status": result.status.value,
        "command": result.command,
        "exit_code": result.exit_code,
        "run_id": run_id,
        "roots": roots,
        "suppressed": result.suppressed.to_dict(),
        "raw": result.raw.to_dict(),
    }
    if result.unclassified:
        payload["unclassified"] = [item.to_dict(compact=True) for item in result.unclassified]
    if no_progress and result.status is RunStatus.FAILED and (result.roots or result.unclassified):
        payload["no_progress"] = True
        payload["hint"] = "Same roots as your last build here; your last edit did not change them."
    if result.roots or result.unclassified:
        return payload
    if result.status is RunStatus.FAILED:
        payload["hint"] = "No parsed roots; call diagrun_get_raw only if needed."
    return payload
