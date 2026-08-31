"""Reduce parsed diagnostics to compact roots."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional, Sequence

from diagrun.diagnostics.buildsys import count_build_system_lines
from diagrun.diagnostics.compiler import parse_compiler_log
from diagrun.diagnostics.model import (
    BuildResult,
    Diagnostic,
    DiagnosticGroup,
    DiagnosticKind,
    RawRef,
    Role,
    RunStatus,
    Severity,
    SuppressedCounts,
    raw_ref,
)
from diagrun.diagnostics.snippet import attach_snippets
from diagrun.reducer.fingerprint import fingerprint
from diagrun.store.runs import RunStore

if False:  # pragma: no cover — typing only
    from diagrun.exec.runner import CapturedRun


_SYNTAX_MARKERS = ("expected ", "primary-expression")


def _is_syntax(diagnostic: Diagnostic) -> bool:
    if diagnostic.kind == DiagnosticKind.SYNTAX_CASCADE:
        return True
    lower = diagnostic.message.lower()
    return any(marker in lower for marker in _SYNTAX_MARKERS)


def _visible_severity(diagnostic: Diagnostic) -> bool:
    return diagnostic.severity in (Severity.ERROR, Severity.FATAL)


def group_diagnostics(diagnostics: Sequence[Diagnostic]) -> list[DiagnosticGroup]:
    """Cluster exact-fingerprint matches; keep first as representative."""
    buckets: OrderedDict[tuple, list[Diagnostic]] = OrderedDict()
    for item in diagnostics:
        buckets.setdefault(fingerprint(item), []).append(item)
    groups: list[DiagnosticGroup] = []
    for members in buckets.values():
        representative = members[0]
        tus = []
        for member in members:
            tu = member.translation_unit or (member.location.file if member.location else None)
            if tu:
                tus.append(tu)
        unique_tus = list(dict.fromkeys(tus))
        evidence: list[str] = []
        if len(unique_tus) > 1 and representative.symbol:
            evidence.append(
                f"same symbol {representative.symbol} in {len(unique_tus)} translation units"
            )
        elif len(members) > 1 and representative.location:
            evidence.append(
                f"same source location {representative.location.file}:{representative.location.line}"
            )
        groups.append(
            DiagnosticGroup(
                representative=representative,
                members=[],
                affected_translation_units=max(1, len(unique_tus) or 1),
                collapsed_diagnostics=max(0, len(members) - 1),
                evidence=evidence,
                role=Role.ROOT,
            )
        )
    return groups


def _apply_parse_recovery(
    groups: list[DiagnosticGroup],
    *,
    collapse: bool,
) -> tuple[list[DiagnosticGroup], list[Diagnostic], int]:
    """Optionally hide same-file follow-ons after a syntax error.

    A collapsed cascade is not just deleted: its count is folded into the
    causing root's ``evidence`` (plan.md §1.2 rule 6). Silently dropping it
    into a suppressed counter elsewhere gave the model no reason to expect
    that fixing one root would also fix the diagnostics after it.
    """
    if not collapse:
        return groups, [], 0
    roots: list[DiagnosticGroup] = []
    unclassified: list[Diagnostic] = []
    cascaded = 0
    last_syntax_root: Optional[DiagnosticGroup] = None
    last_syntax_file: Optional[str] = None
    last_syntax_line = 0
    last_syntax_count = 0
    for group in groups:
        loc = group.representative.location
        file_name = loc.file if loc else None
        line = loc.line if loc else 0
        if last_syntax_file and file_name == last_syntax_file and line >= last_syntax_line:
            cascaded += 1
            last_syntax_count += 1
            dependent = group.representative
            dependent.role = Role.DEPENDENT
            unclassified.append(dependent)
            if last_syntax_root is not None:
                note = f"{last_syntax_count} parse-recovery diagnostic(s) after this location"
                if last_syntax_root.evidence and last_syntax_root.evidence[-1].endswith(
                    "after this location"
                ):
                    last_syntax_root.evidence[-1] = note
                else:
                    last_syntax_root.evidence.append(note)
            continue
        roots.append(group)
        if _is_syntax(group.representative):
            last_syntax_file = file_name
            last_syntax_line = line
            last_syntax_root = group
            last_syntax_count = 0
        else:
            last_syntax_root = None
    return roots, unclassified, cascaded


def reduce_log(
    text: str,
    *,
    command: str,
    exit_code: int,
    run_id: str,
    raw_bytes: int,
    collapse_parse_recovery: bool = False,
    cwd: Optional[str] = None,
) -> BuildResult:
    """Parse a captured log and return a compact BuildResult."""
    parsed = [item for item in parse_compiler_log(text) if _visible_severity(item)]
    attach_snippets(parsed, cwd)
    build_msgs = count_build_system_lines(text)
    groups = group_diagnostics(parsed)
    unclassified: list[Diagnostic] = []
    cascaded = 0
    if collapse_parse_recovery:
        groups, unclassified, cascaded = _apply_parse_recovery(groups, collapse=True)
    for index, group in enumerate(groups, start=1):
        group.representative.id = f"D{index}"
    status = RunStatus.PASSED if exit_code == 0 else RunStatus.FAILED
    if status is RunStatus.PASSED:
        groups = []
        unclassified = []
    return BuildResult(
        status=status,
        command=command,
        exit_code=exit_code,
        raw=RawRef(ref=raw_ref(run_id), bytes=raw_bytes),
        roots=groups,
        unclassified=unclassified,
        suppressed=SuppressedCounts(
            cascaded_diagnostics=cascaded,
            build_system_messages=build_msgs,
        ),
    )


def persist_diagnostics(store: RunStore, captured: "CapturedRun") -> BuildResult:
    """Reduce the stored log and write ``diagnostics.json`` under the run dir."""
    blob = store.raw_bytes(captured.id)
    text = blob.decode("utf-8", errors="replace")
    command = " ".join(captured.command)
    result = reduce_log(
        text,
        command=command,
        exit_code=captured.exit_code,
        run_id=captured.id,
        raw_bytes=len(blob),
        collapse_parse_recovery=captured.collapse_parse_recovery,
        cwd=captured.cwd,
    )
    path = store.run_dir(captured.id) / "diagnostics.json"
    path.write_text(json.dumps(result.to_dict(verbose=True), indent=2) + "\n", encoding="utf-8")
    return result


def _progress_path(store: RunStore) -> Path:
    return store.root / "progress_by_cwd.json"


def _load_progress(store: RunStore) -> dict[str, Any]:
    path = _progress_path(store)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_progress(store: RunStore, data: dict[str, Any]) -> None:
    path = _progress_path(store)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _root_fingerprints(result: BuildResult) -> list[str]:
    return sorted(
        "|".join(str(part) for part in fingerprint(group.representative)) for group in result.roots
    )


def check_progress(store: RunStore, cwd: str, result: BuildResult) -> bool:
    """Record this run's root fingerprints for ``cwd`` and report if they changed.

    Plan.md §1.2 rule 7: a `diagrun_build` call that returns the same roots
    as the previous call in the same directory means the last edit did not
    change the diagnostic. Returns ``True`` on progress (or a passed build,
    or the first run for this ``cwd``), ``False`` when the failure is a
    verbatim repeat and the caller should stop rebuilding blindly.
    """
    key = os.path.abspath(cwd) if cwd else "."
    data = _load_progress(store)
    if result.status is RunStatus.PASSED:
        if key in data:
            del data[key]
            _save_progress(store, data)
        return True
    current = _root_fingerprints(result)
    previous = data.get(key)
    data[key] = current
    _save_progress(store, data)
    if not current:
        return True
    return previous != current
