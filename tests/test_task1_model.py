"""Comprehensive tests for plan.md task 1: normalized diagnostic types."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diagrun import (
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
from diagrun.diagnostics.model import raw_ref, retain_unclassified


def _error(
    diagnostic_id: str,
    message: str = "no member named 'foo' in 'Widget'",
    **kwargs: object,
) -> Diagnostic:
    defaults: dict[str, object] = {
        "id": diagnostic_id,
        "severity": Severity.ERROR,
        "message": message,
        "kind": DiagnosticKind.MISSING_MEMBER,
        "location": Location(file="include/widget.hpp", line=51, column=17),
        "symbol": "Widget::foo",
        "role": Role.UNKNOWN,
    }
    defaults.update(kwargs)
    return Diagnostic(**defaults)  # type: ignore[arg-type]


def _root_group() -> DiagnosticGroup:
    member = _error(
        "D18",
        translation_unit="src/bar.cpp",
        role=Role.DUPLICATE_MANIFESTATION,
    )
    return DiagnosticGroup(
        representative=_error("D17", confidence=0.97, role=Role.ROOT),
        members=[member],
        affected_translation_units=7,
        collapsed_diagnostics=23,
        evidence=["same source location across translation units"],
        confidence=0.97,
        role=Role.ROOT,
    )


class LocationTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        loc = Location(file="src/a.cpp", line=3, column=8)
        self.assertEqual(Location.from_dict(loc.to_dict()).to_dict(), loc.to_dict())

    def test_omits_missing_column(self) -> None:
        payload = Location(file="src/a.cpp", line=3).to_dict()
        self.assertEqual(payload, {"file": "src/a.cpp", "line": 3})

    def test_column_zero_allowed(self) -> None:
        loc = Location(file="src/a.cpp", line=1, column=0)
        self.assertEqual(loc.column, 0)

    def test_rejects_whitespace_file(self) -> None:
        with self.assertRaises(ValueError):
            Location(file="   ", line=1)

    def test_rejects_negative_column(self) -> None:
        with self.assertRaises(ValueError):
            Location(file="src/a.cpp", line=1, column=-1)

    def test_rejects_negative_line(self) -> None:
        with self.assertRaises(ValueError):
            Location(file="src/a.cpp", line=-4)

    def test_from_dict_coerces_numeric_strings(self) -> None:
        loc = Location.from_dict({"file": "a.cpp", "line": "12", "column": "4"})
        self.assertEqual((loc.line, loc.column), (12, 4))


class NoteAndFrameTests(unittest.TestCase):
    def test_note_roundtrip_with_location(self) -> None:
        note = Note(
            message="candidate function not viable",
            location=Location(file="include/w.hpp", line=9, column=1),
            kind="candidate",
        )
        self.assertEqual(Note.from_dict(note.to_dict()).to_dict(), note.to_dict())

    def test_note_omits_empty_optional_fields(self) -> None:
        payload = Note(message="in instantiation of Widget").to_dict()
        self.assertEqual(payload, {"message": "in instantiation of Widget"})

    def test_note_rejects_empty_message(self) -> None:
        with self.assertRaises(ValueError):
            Note(message="")

    def test_template_frame_roundtrip(self) -> None:
        frame = TemplateFrame(
            message="in instantiation of function template 'use'",
            location=Location(file="src/foo.cpp", line=10, column=1),
        )
        self.assertEqual(TemplateFrame.from_dict(frame.to_dict()).to_dict(), frame.to_dict())

    def test_template_frame_rejects_whitespace_message(self) -> None:
        with self.assertRaises(ValueError):
            TemplateFrame(message="\t")


class RawRefTests(unittest.TestCase):
    def test_raw_ref_format(self) -> None:
        self.assertEqual(raw_ref("01JXYZ"), "diag://run/01JXYZ/raw")

    def test_raw_ref_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            raw_ref("")

    def test_zero_bytes_allowed(self) -> None:
        ref = RawRef(ref=raw_ref("empty"), bytes=0)
        self.assertEqual(ref.to_dict()["bytes"], 0)

    def test_rejects_negative_bytes(self) -> None:
        with self.assertRaises(ValueError):
            RawRef(ref=raw_ref("x"), bytes=-1)

    def test_rejects_empty_ref(self) -> None:
        with self.assertRaises(ValueError):
            RawRef(ref="", bytes=0)

    def test_roundtrip(self) -> None:
        ref = RawRef(ref=raw_ref("abc"), bytes=42)
        self.assertEqual(RawRef.from_dict(ref.to_dict()).to_dict(), ref.to_dict())


class DiagnosticTests(unittest.TestCase):
    def test_defaults(self) -> None:
        diagnostic = Diagnostic(id="D1", severity=Severity.ERROR, message="x")
        self.assertEqual(diagnostic.kind, DiagnosticKind.UNKNOWN)
        self.assertEqual(diagnostic.role, Role.UNKNOWN)
        self.assertEqual(diagnostic.confidence, 1.0)
        self.assertEqual(diagnostic.notes, [])
        self.assertEqual(diagnostic.template_instantiation_frames, [])

    def test_string_enum_coercion(self) -> None:
        diagnostic = Diagnostic(id="D1", severity="error", message="x", role="dependent")
        self.assertEqual(diagnostic.severity, Severity.ERROR)
        self.assertEqual(diagnostic.role, Role.DEPENDENT)

    def test_empty_kind_becomes_unknown(self) -> None:
        diagnostic = Diagnostic(id="D1", severity=Severity.ERROR, message="x", kind="")
        self.assertEqual(diagnostic.kind, DiagnosticKind.UNKNOWN)

    def test_confidence_boundaries(self) -> None:
        low = Diagnostic(id="D1", severity=Severity.ERROR, message="x", confidence=0.0)
        high = Diagnostic(id="D2", severity=Severity.ERROR, message="x", confidence=1.0)
        self.assertEqual(low.confidence, 0.0)
        self.assertEqual(high.confidence, 1.0)

    def test_rejects_negative_confidence(self) -> None:
        with self.assertRaises(ValueError):
            Diagnostic(id="D1", severity=Severity.ERROR, message="x", confidence=-0.01)

    def test_rejects_empty_id_and_message(self) -> None:
        with self.assertRaises(ValueError):
            Diagnostic(id="  ", severity=Severity.ERROR, message="x")
        with self.assertRaises(ValueError):
            Diagnostic(id="D1", severity=Severity.ERROR, message="")

    def test_compact_omits_internal_fields(self) -> None:
        diagnostic = _error(
            "D1",
            notes=[Note(message="candidate ignored")],
            template_instantiation_frames=[TemplateFrame(message="in instantiation of X")],
            translation_unit="src/a.cpp",
            compiler_invocation="clang++ -c src/a.cpp",
            code="err_no_member",
            role=Role.ROOT,
            confidence=0.9,
        )
        compact = diagnostic.to_dict(compact=True)
        for key in (
            "notes",
            "template_instantiation_frames",
            "translation_unit",
            "compiler_invocation",
            "code",
            "role",
            "confidence",
        ):
            self.assertNotIn(key, compact)
        self.assertEqual(compact["id"], "D1")
        self.assertEqual(compact["location"]["file"], "include/widget.hpp")

    def test_full_dict_keeps_notes_and_frames(self) -> None:
        diagnostic = _error(
            "D1",
            notes=[Note(message="candidate function not viable")],
            template_instantiation_frames=[TemplateFrame(message="required from here")],
            role=Role.ROOT,
        )
        payload = diagnostic.to_dict()
        self.assertEqual(payload["notes"][0]["message"], "candidate function not viable")
        self.assertEqual(payload["template_instantiation_frames"][0]["message"], "required from here")
        self.assertEqual(payload["role"], "root")

    def test_from_dict_roundtrip(self) -> None:
        diagnostic = _error(
            "D1",
            notes=[Note(message="note text")],
            translation_unit="src/a.cpp",
            compiler_invocation="g++ -c src/a.cpp",
            code="error: missing member",
            role=Role.ROOT,
            confidence=0.5,
        )
        restored = Diagnostic.from_dict(diagnostic.to_dict())
        self.assertEqual(restored.to_dict(), diagnostic.to_dict())

    def test_from_dict_compact_payload(self) -> None:
        restored = Diagnostic.from_dict(
            {
                "id": "D1",
                "severity": "error",
                "kind": "missing_member",
                "message": "no member named 'foo' in 'Widget'",
                "location": {"file": "include/widget.hpp", "line": 51, "column": 17},
                "symbol": "Widget::foo",
            }
        )
        self.assertEqual(restored.role, Role.UNKNOWN)
        self.assertEqual(restored.confidence, 1.0)
        self.assertEqual(restored.notes, [])

    def test_custom_kind_passthrough(self) -> None:
        diagnostic = Diagnostic(
            id="D1",
            severity=Severity.ERROR,
            message="weird",
            kind="vendor_specific_kind",
        )
        self.assertEqual(diagnostic.kind, "vendor_specific_kind")


class DiagnosticGroupTests(unittest.TestCase):
    def test_id_comes_from_representative(self) -> None:
        group = _root_group()
        self.assertEqual(group.id, "D17")

    def test_confidence_defaults_to_representative(self) -> None:
        group = DiagnosticGroup(representative=_error("D1", confidence=0.42, role=Role.ROOT))
        self.assertEqual(group.confidence, 0.42)

    def test_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            DiagnosticGroup(
                representative=_error("D1", role=Role.ROOT),
                affected_translation_units=-1,
            )
        with self.assertRaises(ValueError):
            DiagnosticGroup(
                representative=_error("D1", role=Role.ROOT),
                collapsed_diagnostics=-1,
            )

    def test_compact_flattens_representative_plus_stats(self) -> None:
        payload = _root_group().to_dict(compact=True)
        self.assertEqual(payload["id"], "D17")
        self.assertEqual(payload["affected_translation_units"], 7)
        self.assertEqual(payload["collapsed_diagnostics"], 23)
        self.assertEqual(payload["confidence"], 0.97)
        self.assertNotIn("members", payload)
        self.assertNotIn("role", payload)
        self.assertNotIn("notes", payload)

    def test_verbose_includes_members(self) -> None:
        payload = _root_group().to_dict(compact=False)
        self.assertEqual(payload["members"][0]["id"], "D18")
        self.assertEqual(payload["role"], "root")

    def test_from_dict_roundtrip_verbose(self) -> None:
        group = _root_group()
        restored = DiagnosticGroup.from_dict(group.to_dict(compact=False))
        self.assertEqual(restored.id, "D17")
        self.assertEqual(restored.members[0].id, "D18")
        self.assertEqual(restored.affected_translation_units, 7)

    def test_zero_translation_units_allowed(self) -> None:
        group = DiagnosticGroup(
            representative=_error("D1", role=Role.ROOT),
            affected_translation_units=0,
            collapsed_diagnostics=0,
        )
        self.assertEqual(group.affected_translation_units, 0)


class DependencyEdgeTests(unittest.TestCase):
    def _edge(self, **kwargs: object) -> DependencyEdge:
        defaults: dict[str, object] = {
            "from_id": "D1",
            "to_id": "D2",
            "reason": "in instantiation of Widget::foo",
            "rule_id": "compiler.note.instantiation",
            "confidence": 0.95,
            "type": EdgeType.COMPILER_EXPLICIT,
        }
        defaults.update(kwargs)
        return DependencyEdge(**defaults)  # type: ignore[arg-type]

    def test_roundtrip_accepts_from_and_from_id(self) -> None:
        edge = self._edge()
        payload = edge.to_dict()
        self.assertEqual(payload["from"], "D1")
        self.assertNotIn("from_id", payload)
        restored = DependencyEdge.from_dict(payload)
        self.assertEqual(restored.from_id, "D1")
        aliased = DependencyEdge.from_dict(
            {
                "from_id": "D1",
                "to_id": "D2",
                "reason": "x",
                "rule_id": "r",
                "confidence": 1.0,
                "type": "build",
            }
        )
        self.assertEqual(aliased.type, EdgeType.BUILD)

    def test_rejects_empty_fields(self) -> None:
        for field in ("from_id", "to_id", "reason", "rule_id"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self._edge(**{field: "  "})

    def test_rejects_invalid_type(self) -> None:
        with self.assertRaises(ValueError):
            self._edge(type="not_an_edge")

    def test_string_type_coercion(self) -> None:
        edge = self._edge(type="parse_recovery")
        self.assertEqual(edge.type, EdgeType.PARSE_RECOVERY)

    def test_all_plan_edge_types_exist(self) -> None:
        self.assertEqual(
            {item.value for item in EdgeType},
            {"compiler_explicit", "build", "repeated_origin", "parse_recovery"},
        )


class SuppressedCountsTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        counts = SuppressedCounts(cascaded_diagnostics=4, build_system_messages=11)
        self.assertEqual(SuppressedCounts.from_dict(counts.to_dict()).to_dict(), counts.to_dict())

    def test_defaults_zero(self) -> None:
        self.assertEqual(SuppressedCounts().to_dict(), {
            "cascaded_diagnostics": 0,
            "build_system_messages": 0,
        })

    def test_rejects_negatives(self) -> None:
        with self.assertRaises(ValueError):
            SuppressedCounts(cascaded_diagnostics=-1)
        with self.assertRaises(ValueError):
            SuppressedCounts(build_system_messages=-1)

    def test_from_dict_missing_keys(self) -> None:
        self.assertEqual(SuppressedCounts.from_dict({}).cascaded_diagnostics, 0)


class BuildResultTests(unittest.TestCase):
    def test_empty_command_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BuildResult(
                status=RunStatus.FAILED,
                command=" ",
                exit_code=1,
                raw=RawRef(ref=raw_ref("x"), bytes=1),
            )

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            BuildResult(
                status="exploded",  # type: ignore[arg-type]
                command="cmake --build build",
                exit_code=1,
                raw=RawRef(ref=raw_ref("x"), bytes=1),
            )

    def test_multiple_independent_roots_keep_distinct_ids(self) -> None:
        second = DiagnosticGroup(
            representative=_error(
                "D2",
                message="use of undeclared identifier 'MAX_SIZE'",
                kind=DiagnosticKind.UNDECLARED_IDENTIFIER,
                location=Location(file="src/config.cpp", line=93, column=12),
                symbol="MAX_SIZE",
                role=Role.ROOT,
            ),
            collapsed_diagnostics=4,
            role=Role.ROOT,
        )
        result = BuildResult(
            status=RunStatus.FAILED,
            command="cmake --build build",
            exit_code=2,
            roots=[_root_group(), second],
            raw=RawRef(ref=raw_ref("01JXYZ"), bytes=100),
        )
        ids = [group["id"] for group in result.to_dict()["roots"]]
        self.assertEqual(ids, ["D17", "D2"])

    def test_unclassified_id_must_not_collide_with_member_id(self) -> None:
        """Member ids are real diagnostics; colliding unclassified ids are unsafe."""
        with self.assertRaises(ValueError):
            BuildResult(
                status=RunStatus.FAILED,
                command="cmake --build build",
                exit_code=1,
                roots=[_root_group()],
                unclassified=[_error("D18", message="other", role=Role.UNKNOWN)],
                raw=RawRef(ref=raw_ref("x"), bytes=1),
            )

    def test_json_is_stable_and_encodable(self) -> None:
        result = BuildResult(
            status=RunStatus.FAILED,
            command="cmake --build build -j8",
            exit_code=2,
            roots=[_root_group()],
            unclassified=[_error("D99", message="something novel", location=None, symbol=None)],
            suppressed=SuppressedCounts(cascaded_diagnostics=23, build_system_messages=11),
            raw=RawRef(ref=raw_ref("01JXYZ"), bytes=83142),
        )
        encoded = json.dumps(result.to_dict(), sort_keys=True)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["status"], "failed")
        self.assertEqual(decoded["unclassified"][0]["id"], "D99")
        self.assertNotIn("edges", decoded)

    def test_passed_result_has_empty_roots_and_unclassified(self) -> None:
        result = BuildResult.passed(
            command="cmake --build build",
            exit_code=0,
            raw=RawRef(ref=raw_ref("ok"), bytes=12),
        )
        payload = result.to_dict()
        self.assertEqual(payload["roots"], [])
        self.assertEqual(payload["unclassified"], [])
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["exit_code"], 0)

    def test_retain_unclassified_keeps_only_unknown_role(self) -> None:
        unknown = _error("D1", role=Role.UNKNOWN)
        dependent = _error("D2", role=Role.DEPENDENT)
        self.assertEqual(retain_unclassified([unknown, dependent]), [unknown])
        self.assertEqual(retain_unclassified([]), [])

    def test_compact_schema_keys_match_plan_section_4(self) -> None:
        result = BuildResult(
            status=RunStatus.FAILED,
            command="cmake --build build -j8",
            exit_code=2,
            roots=[_root_group()],
            raw=RawRef(ref=raw_ref("01JXYZ"), bytes=83142),
        )
        self.assertEqual(
            set(result.to_dict()),
            {"status", "command", "exit_code", "roots", "unclassified", "suppressed", "raw"},
        )
        self.assertEqual(
            set(result.to_dict()["roots"][0])
            & {
                "id",
                "severity",
                "kind",
                "message",
                "location",
                "symbol",
                "confidence",
                "affected_translation_units",
                "collapsed_diagnostics",
                "evidence",
            },
            {
                "id",
                "severity",
                "kind",
                "message",
                "location",
                "symbol",
                "confidence",
                "affected_translation_units",
                "collapsed_diagnostics",
                "evidence",
            },
        )


class EnumContractTests(unittest.TestCase):
    def test_roles_match_plan(self) -> None:
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

    def test_run_status_values(self) -> None:
        self.assertEqual({item.value for item in RunStatus}, {"passed", "failed"})

    def test_severity_values(self) -> None:
        self.assertEqual(
            {item.value for item in Severity},
            {"fatal", "error", "warning", "note", "remark"},
        )

    def test_fixture_kinds_are_named(self) -> None:
        for kind in (
            "missing_include",
            "missing_member",
            "undeclared_identifier",
            "wrong_function_signature",
            "template_instantiation",
            "concept_failure",
            "syntax_cascade",
            "linker_undefined_symbol",
            "generated_header_missing",
            "unknown",
        ):
            self.assertTrue(hasattr(DiagnosticKind, kind.upper()) or kind == "unknown")
        self.assertEqual(DiagnosticKind.MISSING_INCLUDE, "missing_include")
        self.assertEqual(DiagnosticKind.LINKER_UNDEFINED_SYMBOL, "linker_undefined_symbol")

    def test_package_exports_core_types(self) -> None:
        import diagrun

        for name in (
            "Diagnostic",
            "DiagnosticGroup",
            "DependencyEdge",
            "BuildResult",
        ):
            self.assertIn(name, diagrun.__all__)


if __name__ == "__main__":
    unittest.main()
