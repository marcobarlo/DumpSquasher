"""Tests for the normalized diagnostic model (plan.md §4, §11, §30.1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
    raw_ref,
    retain_unclassified,
)


PLAN_SCHEMA_EXAMPLE = {
    "status": "failed",
    "command": "cmake --build build -j8",
    "exit_code": 2,
    "roots": [
        {
            "id": "D17",
            "severity": "error",
            "kind": "missing_member",
            "message": "no member named 'foo' in 'Widget'",
            "location": {
                "file": "include/widget.hpp",
                "line": 51,
                "column": 17,
            },
            "symbol": "Widget::foo",
            "confidence": 0.97,
            "affected_translation_units": 7,
            "collapsed_diagnostics": 23,
            "evidence": [
                "same source location across translation units",
                "dependent template-instantiation diagnostics",
            ],
        }
    ],
    "unclassified": [],
    "suppressed": {
        "cascaded_diagnostics": 23,
        "build_system_messages": 11,
    },
    "raw": {
        "ref": "diag://run/01JXYZ/raw",
        "bytes": 83142,
    },
}


def _sample_root() -> DiagnosticGroup:
    return DiagnosticGroup(
        representative=Diagnostic(
            id="D17",
            severity=Severity.ERROR,
            kind=DiagnosticKind.MISSING_MEMBER,
            message="no member named 'foo' in 'Widget'",
            location=Location(file="include/widget.hpp", line=51, column=17),
            symbol="Widget::foo",
            confidence=0.97,
            role=Role.ROOT,
            notes=[
                Note(
                    message="in instantiation of 'Widget::bar'",
                    location=Location(file="src/foo.cpp", line=10, column=1),
                    kind="instantiation",
                )
            ],
            template_instantiation_frames=[
                TemplateFrame(
                    message="in instantiation of function template 'use'",
                    location=Location(file="src/foo.cpp", line=10, column=1),
                )
            ],
        ),
        members=[
            Diagnostic(
                id="D18",
                severity=Severity.ERROR,
                kind=DiagnosticKind.MISSING_MEMBER,
                message="no member named 'foo' in 'Widget'",
                location=Location(file="include/widget.hpp", line=51, column=17),
                translation_unit="src/bar.cpp",
                role=Role.DUPLICATE_MANIFESTATION,
            )
        ],
        affected_translation_units=7,
        collapsed_diagnostics=23,
        evidence=[
            "same source location across translation units",
            "dependent template-instantiation diagnostics",
        ],
        confidence=0.97,
        role=Role.ROOT,
    )


class LocationTests(unittest.TestCase):
    def test_rejects_empty_file(self) -> None:
        with self.assertRaises(ValueError):
            Location(file="", line=1)

    def test_rejects_non_positive_line(self) -> None:
        with self.assertRaises(ValueError):
            Location(file="a.cpp", line=0)


class DiagnosticTests(unittest.TestCase):
    def test_rejects_invalid_severity(self) -> None:
        with self.assertRaises(ValueError):
            Diagnostic(id="D1", severity="oops", message="x")  # type: ignore[arg-type]

    def test_rejects_confidence_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            Diagnostic(id="D1", severity=Severity.ERROR, message="x", confidence=1.2)

    def test_notes_stay_on_parent(self) -> None:
        diagnostic = Diagnostic(
            id="D1",
            severity=Severity.ERROR,
            message="call to 'f' is ambiguous",
            notes=[Note(message="candidate function not viable")],
        )
        payload = diagnostic.to_dict()
        self.assertEqual(len(payload["notes"]), 1)
        self.assertEqual(payload["notes"][0]["message"], "candidate function not viable")


class DependencyEdgeTests(unittest.TestCase):
    def test_json_uses_from_and_to(self) -> None:
        edge = DependencyEdge(
            from_id="D1",
            to_id="D2",
            reason="in instantiation of Widget::foo",
            rule_id="compiler.note.instantiation",
            confidence=0.95,
            type=EdgeType.COMPILER_EXPLICIT,
        )
        self.assertEqual(edge.to_dict()["from"], "D1")
        self.assertEqual(edge.to_dict()["to"], "D2")
        roundtrip = DependencyEdge.from_dict(edge.to_dict())
        self.assertEqual(roundtrip.from_id, "D1")
        self.assertEqual(roundtrip.to_id, "D2")

    def test_rejects_reflexive_edge(self) -> None:
        with self.assertRaises(ValueError):
            DependencyEdge(
                from_id="D1",
                to_id="D1",
                reason="self",
                rule_id="x",
                confidence=1.0,
                type=EdgeType.BUILD,
            )


class BuildResultSchemaTests(unittest.TestCase):
    def test_compact_json_matches_plan_schema(self) -> None:
        result = BuildResult(
            status=RunStatus.FAILED,
            command="cmake --build build -j8",
            exit_code=2,
            roots=[_sample_root()],
            unclassified=[],
            suppressed=SuppressedCounts(
                cascaded_diagnostics=23,
                build_system_messages=11,
            ),
            raw=RawRef(ref=raw_ref("01JXYZ"), bytes=83142),
            edges=[
                DependencyEdge(
                    from_id="D17",
                    to_id="D18",
                    reason="same source location across translation units",
                    rule_id="cluster.same_location",
                    confidence=0.99,
                    type=EdgeType.REPEATED_ORIGIN,
                )
            ],
        )
        payload = result.to_dict()
        self.assertEqual(payload, PLAN_SCHEMA_EXAMPLE)
        self.assertNotIn("edges", payload)
        self.assertNotIn("members", payload["roots"][0])
        self.assertNotIn("notes", payload["roots"][0])

    def test_compact_roundtrip(self) -> None:
        loaded = BuildResult.from_dict(PLAN_SCHEMA_EXAMPLE)
        self.assertEqual(loaded.to_dict(), PLAN_SCHEMA_EXAMPLE)
        self.assertEqual(loaded.roots[0].id, "D17")
        self.assertEqual(loaded.roots[0].affected_translation_units, 7)
        encoded = json.dumps(loaded.to_dict())
        self.assertEqual(json.loads(encoded), PLAN_SCHEMA_EXAMPLE)

    def test_verbose_keeps_edges_and_members(self) -> None:
        result = BuildResult(
            status=RunStatus.FAILED,
            command="cmake --build build -j8",
            exit_code=2,
            roots=[_sample_root()],
            raw=RawRef(ref=raw_ref("01JXYZ"), bytes=83142),
            edges=[
                DependencyEdge(
                    from_id="D17",
                    to_id="D18",
                    reason="same source location",
                    rule_id="cluster.same_location",
                    confidence=0.99,
                    type=EdgeType.REPEATED_ORIGIN,
                )
            ],
        )
        payload = result.to_dict(verbose=True)
        self.assertEqual(len(payload["edges"]), 1)
        self.assertEqual(payload["roots"][0]["members"][0]["id"], "D18")
        roundtrip = BuildResult.from_dict(payload)
        self.assertEqual(roundtrip.edges[0].rule_id, "cluster.same_location")
        self.assertEqual(roundtrip.roots[0].members[0].id, "D18")

    def test_unclassified_are_retained(self) -> None:
        unknown = Diagnostic(
            id="D99",
            severity=Severity.ERROR,
            message="something novel",
            role=Role.UNKNOWN,
        )
        result = BuildResult(
            status=RunStatus.FAILED,
            command="cmake --build build",
            exit_code=1,
            unclassified=[unknown],
            raw=RawRef(ref=raw_ref("abc"), bytes=10),
        )
        self.assertEqual(result.to_dict()["unclassified"][0]["id"], "D99")
        self.assertEqual(retain_unclassified([unknown]), [unknown])

    def test_unclassified_cannot_be_marked_root(self) -> None:
        with self.assertRaises(ValueError):
            BuildResult(
                status=RunStatus.FAILED,
                command="cmake --build build",
                exit_code=1,
                unclassified=[
                    Diagnostic(
                        id="D1",
                        severity=Severity.ERROR,
                        message="x",
                        role=Role.ROOT,
                    )
                ],
                raw=RawRef(ref=raw_ref("abc"), bytes=1),
            )

    def test_duplicate_ids_rejected(self) -> None:
        root = _sample_root()
        with self.assertRaises(ValueError):
            BuildResult(
                status=RunStatus.FAILED,
                command="cmake --build build",
                exit_code=1,
                roots=[root],
                unclassified=[
                    Diagnostic(
                        id="D17",
                        severity=Severity.ERROR,
                        message="x",
                        role=Role.UNKNOWN,
                    )
                ],
                raw=RawRef(ref=raw_ref("abc"), bytes=1),
            )

    def test_edge_must_reference_known_ids(self) -> None:
        with self.assertRaises(ValueError):
            BuildResult(
                status=RunStatus.FAILED,
                command="cmake --build build",
                exit_code=1,
                roots=[_sample_root()],
                raw=RawRef(ref=raw_ref("abc"), bytes=1),
                edges=[
                    DependencyEdge(
                        from_id="D17",
                        to_id="D999",
                        reason="nope",
                        rule_id="x",
                        confidence=1.0,
                        type=EdgeType.BUILD,
                    )
                ],
            )

    def test_passed_factory(self) -> None:
        result = BuildResult.passed(
            command="cmake --build build",
            exit_code=0,
            raw=RawRef(ref=raw_ref("ok"), bytes=12),
        )
        self.assertEqual(result.status, RunStatus.PASSED)
        self.assertEqual(result.roots, [])
        with self.assertRaises(ValueError):
            BuildResult.passed(
                command="cmake --build build",
                exit_code=1,
                raw=RawRef(ref=raw_ref("ok"), bytes=12),
            )

    def test_role_values_match_plan(self) -> None:
        self.assertEqual(
            {item.value for item in Role},
            {
                "root",
                "dependent",
                "build_consequence",
                "duplicate_manifestation",
                "unknown",
            },
        )


if __name__ == "__main__":
    unittest.main()
