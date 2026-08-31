"""Shared message normalization and kind/symbol extraction."""

from __future__ import annotations

import re
from typing import Optional

from diagrun.diagnostics.model import DiagnosticKind

_QUOTE_TRANS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "`": "'",
    }
)

_MEMBER = re.compile(
    r"(?:struct |class |union )?(?P<type>[\w:]+).*(?:has no member named|no member named)\s+'?(?P<member>[\w~]+)'?",
    re.IGNORECASE,
)
_MEMBER_ALT = re.compile(
    r"no member named\s+'?(?P<member>[\w~]+)'?\s+in\s+'?(?:struct |class )?(?P<type>[\w:]+)'?",
    re.IGNORECASE,
)
_UNDECLARED = re.compile(
    r"'?(?P<sym>[\w:]+)'?\s+(?:was not declared|undeclared identifier)",
    re.IGNORECASE,
)
_TOO_FEW = re.compile(
    r"(?:too few|too many) arguments to function\s+'?(?P<sig>[^']+)'?",
    re.IGNORECASE,
)
_FUNC_NAME = re.compile(r"(?P<name>[\w:]+)\s*\(")
_UNDEF = re.compile(r"undefined reference to\s+'?(?P<sym>[^']+)'?")
_MISSING_FILE = re.compile(r"^(?P<file>\S+): No such file or directory")
_REQUEST_MEMBER = re.compile(
    r"request for member\s+'?(?P<member>[\w~]+)'?",
    re.IGNORECASE,
)
_REDECLARATION = re.compile(
    r"redeclaration of\s+'(?P<decl>[^']+)'|redefinition of\s+'(?P<decl2>[^']+)'",
    re.IGNORECASE,
)


def normalize_message(message: str) -> str:
    """Collapse quotes and whitespace so fingerprints stay stable."""
    return " ".join(message.translate(_QUOTE_TRANS).split())


def classify_kind(message: str, *, has_instantiation: bool = False) -> str:
    """Map a compiler message to a DiagnosticKind string."""
    text = normalize_message(message)
    lower = text.lower()
    if "unsatisfied constraints" in lower or "constraint not satisfied" in lower:
        return DiagnosticKind.CONCEPT_FAILURE
    if "no such file or directory" in lower or "file not found" in lower:
        return DiagnosticKind.MISSING_INCLUDE
    if "undefined reference" in lower or "undefined symbol" in lower:
        return DiagnosticKind.LINKER_UNDEFINED_SYMBOL
    if "no member named" in lower or "has no member named" in lower:
        return DiagnosticKind.MISSING_MEMBER
    if "was not declared" in lower or "undeclared identifier" in lower:
        return DiagnosticKind.UNDECLARED_IDENTIFIER
    if "redeclaration of" in lower or "redefinition of" in lower:
        return DiagnosticKind.REDECLARATION
    if "too few arguments" in lower or "too many arguments" in lower:
        return DiagnosticKind.WRONG_FUNCTION_SIGNATURE
    if (
        "no matching function" in lower
        or "cannot convert" in lower
        or "invalid conversion from" in lower
    ):
        return DiagnosticKind.WRONG_FUNCTION_SIGNATURE
    if has_instantiation or "in instantiation of" in lower:
        return DiagnosticKind.TEMPLATE_INSTANTIATION
    if "expected " in lower and ("before" in lower or "primary-expression" in lower):
        return DiagnosticKind.SYNTAX_CASCADE
    if "request for member" in lower:
        return DiagnosticKind.MISSING_MEMBER
    return DiagnosticKind.UNKNOWN


def extract_symbol(message: str) -> Optional[str]:
    """Best-effort symbol from a diagnostic message."""
    text = normalize_message(message)
    match = _MEMBER.search(text) or _MEMBER_ALT.search(text)
    if match:
        return f"{match.group('type')}::{match.group('member')}"
    match = _UNDECLARED.search(text)
    if match:
        return match.group("sym")
    match = _TOO_FEW.search(text)
    if match:
        inner = _FUNC_NAME.search(match.group("sig"))
        if inner:
            return inner.group("name")
    match = _UNDEF.search(text)
    if match:
        return match.group("sym").rstrip("'")
    match = _MISSING_FILE.search(text)
    if match:
        return match.group("file")
    match = _REQUEST_MEMBER.search(text)
    if match:
        return match.group("member")
    match = _REDECLARATION.search(text)
    if match:
        decl = match.group("decl") or match.group("decl2") or ""
        tokens = decl.split()
        if tokens:
            return tokens[-1]
    return None
