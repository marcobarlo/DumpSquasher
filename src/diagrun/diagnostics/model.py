"""Normalized diagnostic types.

These types are the contract between parsers, the reducer, and renderers.
Parsers may use any input format; they must emit this model.

Public JSON for ``BuildResult`` matches the compact agent schema in plan.md §4.
Internal fields (members, notes, edges) are omitted from that compact form
unless explicitly requested via ``to_dict(verbose=True)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


class RunStatus(str, Enum):
    """Outcome of a wrapped build command."""

    PASSED = "passed"
    FAILED = "failed"


class Severity(str, Enum):
    """Compiler diagnostic severity."""

    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"
    REMARK = "remark"


class Role(str, Enum):
    """Reducer classification of a diagnostic.

    Unknown must be retained. Prefer false negatives over dropping
    a potentially independent failure.
    """

    ROOT = "root"
    DEPENDENT = "dependent"
    BUILD_CONSEQUENCE = "build_consequence"
    DUPLICATE_MANIFESTATION = "duplicate_manifestation"
    UNKNOWN = "unknown"


class EdgeType(str, Enum):
    """High-confidence dependency edge families from plan.md §11."""

    COMPILER_EXPLICIT = "compiler_explicit"
    BUILD = "build"
    REPEATED_ORIGIN = "repeated_origin"
    PARSE_RECOVERY = "parse_recovery"


class DiagnosticKind:
    """Well-known diagnostic kinds. Parsers may emit other strings."""

    MISSING_INCLUDE = "missing_include"
    MISSING_MEMBER = "missing_member"
    UNDECLARED_IDENTIFIER = "undeclared_identifier"
    WRONG_FUNCTION_SIGNATURE = "wrong_function_signature"
    TEMPLATE_INSTANTIATION = "template_instantiation"
    CONCEPT_FAILURE = "concept_failure"
    SYNTAX_CASCADE = "syntax_cascade"
    LINKER_UNDEFINED_SYMBOL = "linker_undefined_symbol"
    GENERATED_HEADER_MISSING = "generated_header_missing"
    UNKNOWN = "unknown"


def raw_ref(run_id: str) -> str:
    """Return the stable raw-log URI for a run id."""
    if not run_id:
        raise ValueError("run_id must be non-empty")
    return f"diag://run/{run_id}/raw"


def _require_non_empty(name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _require_confidence(value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {value}")
    return value


def _enum_value(enum_cls: type[Enum], value: Any, name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_cls)
        raise ValueError(f"invalid {name} {value!r}; expected one of: {allowed}") from exc


def _omit_empty(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if value == "" or value == [] or value == ():
            continue
        cleaned[key] = value
    return cleaned


@dataclass
class Location:
    """Exact source location. Never replace this with prose."""

    file: str
    line: int
    column: Optional[int] = None

    def __post_init__(self) -> None:
        _require_non_empty("file", self.file)
        if self.line < 1:
            raise ValueError(f"line must be >= 1, got {self.line}")
        if self.column is not None and self.column < 0:
            raise ValueError(f"column must be >= 0, got {self.column}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file, "line": self.line}
        if self.column is not None:
            data["column"] = self.column
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Location:
        return cls(
            file=str(data["file"]),
            line=int(data["line"]),
            column=int(data["column"]) if data.get("column") is not None else None,
        )


@dataclass
class Note:
    """Compiler note attached to a parent diagnostic."""

    message: str
    location: Optional[Location] = None
    kind: Optional[str] = None

    def __post_init__(self) -> None:
        _require_non_empty("message", self.message)

    def to_dict(self) -> dict[str, Any]:
        return _omit_empty(
            {
                "message": self.message,
                "location": self.location.to_dict() if self.location else None,
                "kind": self.kind,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Note:
        loc = data.get("location")
        return cls(
            message=str(data["message"]),
            location=Location.from_dict(loc) if loc else None,
            kind=str(data["kind"]) if data.get("kind") else None,
        )


@dataclass
class TemplateFrame:
    """One frame from a template-instantiation backtrace."""

    message: str
    location: Optional[Location] = None

    def __post_init__(self) -> None:
        _require_non_empty("message", self.message)

    def to_dict(self) -> dict[str, Any]:
        return _omit_empty(
            {
                "message": self.message,
                "location": self.location.to_dict() if self.location else None,
            }
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TemplateFrame:
        loc = data.get("location")
        return cls(
            message=str(data["message"]),
            location=Location.from_dict(loc) if loc else None,
        )


@dataclass
class Diagnostic:
    """One normalized compiler or linker diagnostic.

    Notes and template frames stay attached to this object rather than
    becoming independent messages.
    """

    id: str
    severity: Severity
    message: str
    kind: str = DiagnosticKind.UNKNOWN
    code: Optional[str] = None
    location: Optional[Location] = None
    symbol: Optional[str] = None
    translation_unit: Optional[str] = None
    compiler_invocation: Optional[str] = None
    notes: list[Note] = field(default_factory=list)
    template_instantiation_frames: list[TemplateFrame] = field(default_factory=list)
    role: Role = Role.UNKNOWN
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _require_non_empty("id", self.id)
        _require_non_empty("message", self.message)
        self.severity = _enum_value(Severity, self.severity, "severity")  # type: ignore[assignment]
        self.role = _enum_value(Role, self.role, "role")  # type: ignore[assignment]
        if not self.kind:
            self.kind = DiagnosticKind.UNKNOWN
        _require_confidence(self.confidence)

    def to_dict(self, *, compact: bool = False) -> dict[str, Any]:
        """Serialize this diagnostic.

        Args:
            compact: If True, omit internal fields (notes, TU, invocation).
                Compact form is what appears under ``roots`` / ``unclassified``.
        """
        data: dict[str, Any] = {
            "id": self.id,
            "severity": self.severity.value,
            "kind": self.kind,
            "message": self.message,
            "location": self.location.to_dict() if self.location else None,
            "symbol": self.symbol,
        }
        if compact:
            return _omit_empty(data)
        data.update(
            {
                "code": self.code,
                "translation_unit": self.translation_unit,
                "compiler_invocation": self.compiler_invocation,
                "notes": [note.to_dict() for note in self.notes],
                "template_instantiation_frames": [
                    frame.to_dict() for frame in self.template_instantiation_frames
                ],
                "role": self.role.value,
                "confidence": self.confidence,
            }
        )
        return _omit_empty(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Diagnostic:
        loc = data.get("location")
        notes = data.get("notes") or []
        frames = data.get("template_instantiation_frames") or []
        return cls(
            id=str(data["id"]),
            severity=Severity(data["severity"]),
            message=str(data["message"]),
            kind=str(data.get("kind") or DiagnosticKind.UNKNOWN),
            code=str(data["code"]) if data.get("code") else None,
            location=Location.from_dict(loc) if loc else None,
            symbol=str(data["symbol"]) if data.get("symbol") else None,
            translation_unit=str(data["translation_unit"]) if data.get("translation_unit") else None,
            compiler_invocation=(
                str(data["compiler_invocation"]) if data.get("compiler_invocation") else None
            ),
            notes=[Note.from_dict(item) for item in notes],
            template_instantiation_frames=[TemplateFrame.from_dict(item) for item in frames],
            role=Role(data["role"]) if data.get("role") else Role.UNKNOWN,
            confidence=float(data["confidence"]) if data.get("confidence") is not None else 1.0,
        )


@dataclass
class DiagnosticGroup:
    """Cluster of related diagnostics, typically one logical fault.

    Compact JSON flattens ``representative`` plus group statistics so that
    agent-visible roots match plan.md §4.
    """

    representative: Diagnostic
    members: list[Diagnostic] = field(default_factory=list)
    affected_translation_units: int = 1
    collapsed_diagnostics: int = 0
    evidence: list[str] = field(default_factory=list)
    confidence: Optional[float] = None
    role: Role = Role.ROOT

    def __post_init__(self) -> None:
        self.role = _enum_value(Role, self.role, "role")  # type: ignore[assignment]
        if self.affected_translation_units < 0:
            raise ValueError("affected_translation_units must be >= 0")
        if self.collapsed_diagnostics < 0:
            raise ValueError("collapsed_diagnostics must be >= 0")
        if self.confidence is None:
            self.confidence = self.representative.confidence
        _require_confidence(self.confidence)

    @property
    def id(self) -> str:
        return self.representative.id

    def to_dict(self, *, compact: bool = True) -> dict[str, Any]:
        data = self.representative.to_dict(compact=True)
        data["confidence"] = self.confidence
        data["affected_translation_units"] = self.affected_translation_units
        data["collapsed_diagnostics"] = self.collapsed_diagnostics
        if self.evidence:
            data["evidence"] = list(self.evidence)
        if not compact:
            data["role"] = self.role.value
            data["members"] = [member.to_dict() for member in self.members]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DiagnosticGroup:
        members_data = data.get("members") or []
        representative = Diagnostic.from_dict(data)
        return cls(
            representative=representative,
            members=[Diagnostic.from_dict(item) for item in members_data],
            affected_translation_units=int(data.get("affected_translation_units") or 1),
            collapsed_diagnostics=int(data.get("collapsed_diagnostics") or 0),
            evidence=[str(item) for item in (data.get("evidence") or [])],
            confidence=float(data["confidence"]) if data.get("confidence") is not None else None,
            role=Role(data["role"]) if data.get("role") else Role.ROOT,
        )


@dataclass
class DependencyEdge:
    """Directed edge: ``to`` is a probable consequence of ``from``.

    Every edge is auditable via reason, confidence, and rule identifier.
    """

    from_id: str
    to_id: str
    reason: str
    rule_id: str
    confidence: float
    type: EdgeType

    def __post_init__(self) -> None:
        _require_non_empty("from_id", self.from_id)
        _require_non_empty("to_id", self.to_id)
        if self.from_id == self.to_id:
            raise ValueError("dependency edge cannot be reflexive")
        _require_non_empty("reason", self.reason)
        _require_non_empty("rule_id", self.rule_id)
        _require_confidence(self.confidence)
        self.type = _enum_value(EdgeType, self.type, "type")  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "reason": self.reason,
            "confidence": self.confidence,
            "rule_id": self.rule_id,
            "type": self.type.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DependencyEdge:
        from_id = data.get("from", data.get("from_id"))
        to_id = data.get("to", data.get("to_id"))
        return cls(
            from_id=str(from_id),
            to_id=str(to_id),
            reason=str(data["reason"]),
            rule_id=str(data["rule_id"]),
            confidence=float(data["confidence"]),
            type=EdgeType(data["type"]),
        )


@dataclass
class SuppressedCounts:
    """How many diagnostics were hidden from the agent-visible result."""

    cascaded_diagnostics: int = 0
    build_system_messages: int = 0

    def __post_init__(self) -> None:
        if self.cascaded_diagnostics < 0 or self.build_system_messages < 0:
            raise ValueError("suppressed counts must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "cascaded_diagnostics": self.cascaded_diagnostics,
            "build_system_messages": self.build_system_messages,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SuppressedCounts:
        return cls(
            cascaded_diagnostics=int(data.get("cascaded_diagnostics") or 0),
            build_system_messages=int(data.get("build_system_messages") or 0),
        )


@dataclass
class RawRef:
    """Addressable pointer to the complete captured build output."""

    ref: str
    bytes: int

    def __post_init__(self) -> None:
        _require_non_empty("ref", self.ref)
        if self.bytes < 0:
            raise ValueError("bytes must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "bytes": self.bytes}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RawRef:
        return cls(ref=str(data["ref"]), bytes=int(data["bytes"]))


@dataclass
class BuildResult:
    """Compact structured result of a wrapped build.

    Compact JSON matches plan.md §4. Dependency edges and full diagnostic
    members are included only when ``verbose=True``.
    """

    status: RunStatus
    command: str
    exit_code: int
    raw: RawRef
    roots: list[DiagnosticGroup] = field(default_factory=list)
    unclassified: list[Diagnostic] = field(default_factory=list)
    suppressed: SuppressedCounts = field(default_factory=SuppressedCounts)
    edges: list[DependencyEdge] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.status = _enum_value(RunStatus, self.status, "status")  # type: ignore[assignment]
        _require_non_empty("command", self.command)
        self._validate_ids()

    def _validate_ids(self) -> None:
        seen: set[str] = set()

        def _add(diag_id: str) -> None:
            if diag_id in seen:
                raise ValueError(f"duplicate diagnostic id {diag_id!r}")
            seen.add(diag_id)

        for group in self.roots:
            _add(group.id)
            for member in group.members:
                _add(member.id)
        for diagnostic in self.unclassified:
            _add(diagnostic.id)
            if diagnostic.role is Role.ROOT:
                raise ValueError(
                    f"unclassified diagnostic {diagnostic.id!r} cannot have role root"
                )
        for edge in self.edges:
            if edge.from_id not in seen or edge.to_id not in seen:
                raise ValueError(
                    f"edge {edge.from_id}->{edge.to_id} references an unknown diagnostic"
                )

    def to_dict(self, *, verbose: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status.value,
            "command": self.command,
            "exit_code": self.exit_code,
            "roots": [group.to_dict(compact=not verbose) for group in self.roots],
            "unclassified": [
                diagnostic.to_dict(compact=not verbose) for diagnostic in self.unclassified
            ],
            "suppressed": self.suppressed.to_dict(),
            "raw": self.raw.to_dict(),
        }
        if verbose:
            data["edges"] = [edge.to_dict() for edge in self.edges]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BuildResult:
        return cls(
            status=RunStatus(data["status"]),
            command=str(data["command"]),
            exit_code=int(data["exit_code"]),
            raw=RawRef.from_dict(data["raw"]),
            roots=[DiagnosticGroup.from_dict(item) for item in (data.get("roots") or [])],
            unclassified=[
                Diagnostic.from_dict(item) for item in (data.get("unclassified") or [])
            ],
            suppressed=SuppressedCounts.from_dict(data.get("suppressed") or {}),
            edges=[DependencyEdge.from_dict(item) for item in (data.get("edges") or [])],
        )

    @classmethod
    def passed(cls, command: str, exit_code: int, raw: RawRef) -> BuildResult:
        """Successful build: no roots, nothing unclassified."""
        if exit_code != 0:
            raise ValueError("passed result requires exit_code 0")
        return cls(
            status=RunStatus.PASSED,
            command=command,
            exit_code=exit_code,
            raw=raw,
        )


def retain_unclassified(
    diagnostics: Sequence[Diagnostic],
) -> list[Diagnostic]:
    """Return diagnostics the reducer cannot confidently classify.

    Unknown diagnostics must never be dropped.
    """
    return [item for item in diagnostics if item.role is Role.UNKNOWN]
