"""diagrun: compact, structured explanations of independent C++ build failures."""

from diagrun.diagnostics.model import (
    BuildResult,
    DependencyEdge,
    Diagnostic,
    DiagnosticGroup,
    DiagnosticKind,
    EdgeType,
    Location,
    Note,
    RawRef,
    Role,
    RunStatus,
    Severity,
    SuppressedCounts,
    TemplateFrame,
)

__all__ = [
    "BuildResult",
    "DependencyEdge",
    "Diagnostic",
    "DiagnosticGroup",
    "DiagnosticKind",
    "EdgeType",
    "Location",
    "Note",
    "RawRef",
    "Role",
    "RunStatus",
    "Severity",
    "SuppressedCounts",
    "TemplateFrame",
]

__version__ = "0.1.0"
