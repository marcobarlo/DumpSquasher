"""GCC and Clang diagnostic parsing (JSON first, text fallback)."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

from diagrun.diagnostics.buildsys import is_build_system_line
from diagrun.diagnostics.model import (
    Diagnostic,
    DiagnosticKind,
    Location,
    Note,
    Severity,
    TemplateFrame,
)
from diagrun.diagnostics.textutil import classify_kind, extract_symbol, normalize_message

_TEXT_DIAG = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<column>\d+):)?\s*"
    r"(?P<severity>fatal error|error|warning|note|remark):\s*(?P<message>.*)$"
)
_LINKER = re.compile(
    r"(?P<file>\S+\.(?:cpp|cc|cxx|c|C))(?::\((?P<section>[^)]+)\))?:\s+"
    r"undefined reference to\s+(?P<sym>.+)$"
)
_LINKER_ALT = re.compile(
    r"undefined reference to\s+(?P<sym>.+)$"
)
_TU_FROM_ARGV = re.compile(r"-c\s+(\S+\.(?:cpp|cc|cxx|C|c)\b)")
_INSTANTIATION = re.compile(
    r"in instantiation of|required from|required by substitution",
    re.IGNORECASE,
)


def _severity(kind: str) -> Severity:
    lowered = kind.lower().strip()
    if lowered in ("fatal", "fatal error"):
        return Severity.FATAL
    if lowered == "warning":
        return Severity.WARNING
    if lowered == "note":
        return Severity.NOTE
    if lowered == "remark":
        return Severity.REMARK
    return Severity.ERROR


def _location_from_json(item: Mapping[str, Any]) -> Optional[Location]:
    locations = item.get("locations") or []
    if not locations:
        caret = item.get("caret")
        if isinstance(caret, dict) and caret.get("file"):
            return Location(
                file=str(caret["file"]),
                line=int(caret["line"]),
                column=int(caret["column"]) if caret.get("column") is not None else None,
            )
        return None
    first = locations[0]
    caret = first.get("caret") if isinstance(first, dict) else None
    if not isinstance(caret, dict) or not caret.get("file"):
        loc = first.get("file") if isinstance(first, dict) else None
        if loc:
            return Location(file=str(loc), line=int(first.get("line") or 1))
        return None
    column = caret.get("column")
    return Location(
        file=str(caret["file"]),
        line=int(caret["line"]),
        column=int(column) if column is not None else None,
    )


def _notes_and_frames(item: Mapping[str, Any]) -> tuple[list[Note], list[TemplateFrame], bool]:
    notes: list[Note] = []
    frames: list[TemplateFrame] = []
    has_instantiation = False
    for child in item.get("children") or []:
        if not isinstance(child, dict):
            continue
        message = normalize_message(str(child.get("message") or ""))
        if not message:
            continue
        loc = _location_from_json(child)
        if _INSTANTIATION.search(message):
            has_instantiation = True
            frames.append(TemplateFrame(message=message, location=loc))
            continue
        notes.append(Note(message=message, location=loc, kind=str(child.get("kind") or "note")))
    return notes, frames, has_instantiation


def _diagnostic_from_json(item: Mapping[str, Any], ident: str, tu: Optional[str]) -> Optional[Diagnostic]:
    kind_label = str(item.get("kind") or "error")
    if kind_label.lower() in ("note", "remark"):
        return None
    message = normalize_message(str(item.get("message") or ""))
    if not message:
        return None
    notes, frames, has_inst = _notes_and_frames(item)
    if _INSTANTIATION.search(message):
        has_inst = True
    return Diagnostic(
        id=ident,
        severity=_severity(kind_label),
        message=message,
        kind=classify_kind(message, has_instantiation=has_inst),
        location=_location_from_json(item),
        symbol=extract_symbol(message),
        translation_unit=tu,
        notes=notes,
        template_instantiation_frames=frames,
    )


def _extract_json_arrays(text: str) -> list[tuple[int, list[dict[str, Any]]]]:
    decoder = json.JSONDecoder()
    arrays: list[tuple[int, list[dict[str, Any]]]] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "[":
            index += 1
            continue
        start = index
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index += 1
            continue
        index = end
        if not isinstance(value, list) or not value:
            continue
        if isinstance(value[0], dict) and "kind" in value[0]:
            arrays.append((start, value))
    return arrays


def _tu_from_nearby_invocation(text: str, pos: int) -> Optional[str]:
    start = max(0, pos - 400)
    window = text[start:pos]
    match = None
    for found in _TU_FROM_ARGV.finditer(window):
        match = found
    if match:
        return match.group(1)
    return None


def parse_structured(text: str) -> list[Diagnostic]:
    """Parse GCC/Clang ``-fdiagnostics-format=json`` arrays embedded in a log."""
    diagnostics: list[Diagnostic] = []
    counter = 1
    for start, array in _extract_json_arrays(text):
        tu = _tu_from_nearby_invocation(text, start)
        current: Optional[Diagnostic] = None
        for item in array:
            if not isinstance(item, dict):
                continue
            kind_label = str(item.get("kind") or "")
            if kind_label.lower() in ("note", "remark") and current is not None:
                message = normalize_message(str(item.get("message") or ""))
                if not message:
                    continue
                loc = _location_from_json(item)
                if _INSTANTIATION.search(message):
                    current.template_instantiation_frames.append(
                        TemplateFrame(message=message, location=loc)
                    )
                    current.kind = classify_kind(current.message, has_instantiation=True)
                else:
                    current.notes.append(Note(message=message, location=loc, kind=kind_label))
                continue
            ident = f"D{counter}"
            parsed = _diagnostic_from_json(item, ident, tu)
            if parsed is None:
                continue
            if parsed.location and tu is None:
                parsed.translation_unit = parsed.location.file
            diagnostics.append(parsed)
            current = parsed
            counter += 1
    return diagnostics


def parse_text(text: str) -> list[Diagnostic]:
    """Parse classic ``file:line:col: error:`` lines and linker records."""
    diagnostics: list[Diagnostic] = []
    current: Optional[Diagnostic] = None
    counter = 1
    pending_tu: Optional[str] = None
    for line in text.splitlines():
        tu_match = _TU_FROM_ARGV.search(line)
        if tu_match:
            pending_tu = tu_match.group(1)
        if is_build_system_line(line):
            continue
        match = _TEXT_DIAG.match(line.strip())
        if match:
            severity = _severity(match.group("severity"))
            message = normalize_message(match.group("message"))
            loc = Location(
                file=match.group("file"),
                line=int(match.group("line")),
                column=int(match.group("column")) if match.group("column") else None,
            )
            if severity in (Severity.NOTE, Severity.REMARK) and current is not None:
                current.notes.append(Note(message=message, location=loc, kind=match.group("severity")))
                continue
            parsed = Diagnostic(
                id=f"D{counter}",
                severity=severity,
                message=message,
                kind=classify_kind(message),
                location=loc,
                symbol=extract_symbol(message),
                translation_unit=pending_tu or loc.file,
            )
            diagnostics.append(parsed)
            current = parsed
            counter += 1
            continue
        linker = _LINKER.search(line) or _LINKER_ALT.search(line)
        if linker and "undefined reference" in line:
            sym = linker.group("sym").strip().strip("'\"")
            file_name = linker.groupdict().get("file")
            loc = Location(file=file_name, line=1) if file_name else None
            parsed = Diagnostic(
                id=f"D{counter}",
                severity=Severity.ERROR,
                message=normalize_message(line.strip()),
                kind=DiagnosticKind.LINKER_UNDEFINED_SYMBOL,
                location=loc,
                symbol=sym,
            )
            diagnostics.append(parsed)
            current = parsed
            counter += 1
    return diagnostics


def parse_compiler_log(text: str) -> list[Diagnostic]:
    """Prefer structured JSON; fall back to text if no diagnostics decoded."""
    structured = parse_structured(text)
    if structured:
        # Linker errors are never in the JSON array (GCC emits ``[]`` then ld text).
        extra = [
            item
            for item in parse_text(text)
            if item.kind == DiagnosticKind.LINKER_UNDEFINED_SYMBOL
        ]
        if extra:
            start = len(structured) + 1
            for offset, item in enumerate(extra):
                item.id = f"D{start + offset}"
            structured.extend(extra)
        return structured
    return parse_text(text)
