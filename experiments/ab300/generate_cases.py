#!/usr/bin/env python3
"""Generate 300 synthetic C++ trees for the DSH A/B harness.

Grid: 10 kinds × 5 size classes × 6 variants. Each case directory is a
vanilla Makefile app plus one recorded injection. Vanilla `make && ./app`
exits with GOLD_RETURN_CODE (42).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = Path(__file__).resolve().parent / "cases"
GOLD_RETURN_CODE = 42

KINDS: Tuple[str, ...] = (
    "syntax_cascade",
    "missing_member",
    "undeclared_identifier",
    "wrong_function_signature",
    "missing_include",
    "redeclaration",
    "template_instantiation",
    "concept_failure",
    "linker_undefined_symbol",
    "shared_header_fanout",
)

SIZE_CLASSES: Tuple[Tuple[str, int], ...] = (
    ("1", 1),
    ("4", 4),
    ("16", 16),
    ("64", 64),
    ("verbose", 2),
)

# 6 variants: cpp vs header, start vs end of TU, seed 0/1.
VARIANTS: Tuple[Dict[str, Any], ...] = (
    {"id": 0, "site": "cpp", "pos": "start", "seed": 0},
    {"id": 1, "site": "cpp", "pos": "end", "seed": 0},
    {"id": 2, "site": "header", "pos": "start", "seed": 0},
    {"id": 3, "site": "header", "pos": "end", "seed": 0},
    {"id": 4, "site": "cpp", "pos": "start", "seed": 1},
    {"id": 5, "site": "header", "pos": "end", "seed": 1},
)

PROPAGATION: Dict[str, str] = {
    "syntax_cascade": "broken initializer + follow-on member access",
    "missing_member": "single-root missing member",
    "undeclared_identifier": "single-root undeclared identifier",
    "wrong_function_signature": "call/definition arity mismatch",
    "missing_include": "missing header include",
    "redeclaration": "redefinition of the same name",
    "template_instantiation": "template notes vs root",
    "concept_failure": "unsatisfied C++20 concept",
    "linker_undefined_symbol": "compile OK, link fails",
    "shared_header_fanout": "fault in shared header, N TUs include it",
}

MAKEFILE = """\
CXX ?= g++
CXXFLAGS ?= -fdiagnostics-color=never -fno-diagnostics-show-caret -std=c++20 -Iinclude -Wno-unused-function -Wno-unused-variable
MAKEFLAGS += -k

SRCS := $(wildcard src/*.cpp)
OBJS := $(patsubst src/%.cpp,build/%.o,$(SRCS))

.PHONY: all run
all: app

build/%.o: src/%.cpp include/app.hpp
	@mkdir -p build
	$(CXX) $(CXXFLAGS) -c $< -o $@

app: $(OBJS)
	$(CXX) $(OBJS) -o app

run: app
	./app
"""

APP_HPP = """\
#pragma once

struct Widget {
    int bar;
    int get_bar() const { return bar; }
};

int compute(int x);

inline int use_widget() {
    Widget w{};
    w.bar = 0;
    // HEADER_HOOK
    return w.get_bar();
}
"""


def case_id(kind: str, size_class: str, variant: int) -> str:
    """Return a stable case id."""
    return f"{kind}__{size_class}__v{variant}"


def _dummy_functions(seed: int, nbytes: int) -> str:
    """Emit ~nbytes of parseable dummy C++ (not comments)."""
    lines: List[str] = [
        f"namespace pad{seed} {{",
        "inline int touch(int x) { return x; }",
    ]
    i = 0
    size = sum(len(line) + 1 for line in lines)
    while size < nbytes:
        line = f"inline int d{i}() {{ return touch({i}); }}"
        lines.append(line)
        size += len(line) + 1
        i += 1
    lines.append("}")
    return "\n".join(lines) + "\n"


def _source_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".cpp", ".hpp", ""}:
            if path.name == "Makefile" or path.suffix in {".cpp", ".hpp"}:
                total += path.stat().st_size
    return total


def write_vanilla(dest: Path, n_tus: int, *, verbose: bool, seed: int) -> None:
    """Write a compiling tree. `./app` exits with GOLD_RETURN_CODE."""
    dest = dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "include").mkdir(parents=True)
    (dest / "src").mkdir()
    (dest / "include" / "app.hpp").write_text(APP_HPP, encoding="utf-8")
    extra = max(0, n_tus - 1)
    decls = "\n".join(f"int contrib_{i:03d}();" for i in range(extra))
    adds = "\n".join(f"    acc += contrib_{i:03d}();" for i in range(extra))
    pad_bytes = 0
    if verbose:
        pad_bytes = 90_000 if seed == 0 else 130_000
        per = max(8_000, pad_bytes // max(1, n_tus))
    pad_main = _dummy_functions(seed, per) if verbose else ""
    main = (
        '#include "app.hpp"\n\n'
        f"{decls}\n\n" if decls else '#include "app.hpp"\n\n'
    )
    main += (
        "int compute(int x) {\n"
        "    return x;\n"
        "}\n\n"
        f"{pad_main}"
        "int main() {\n"
        f"    int acc = {GOLD_RETURN_CODE};\n"
        "    acc += compute(use_widget());\n"
        f"{(adds + chr(10)) if adds else ''}"
        "    // CPP_HOOK\n"
        "    return acc;\n"
        "}\n"
    )
    (dest / "src" / "main.cpp").write_text(main, encoding="utf-8")
    for i in range(extra):
        body_pad = _dummy_functions(seed * 1000 + i, per) if verbose else ""
        (dest / "src" / f"tu_{i:03d}.cpp").write_text(
            '#include "app.hpp"\n\n'
            f"{body_pad}"
            f"int contrib_{i:03d}() {{\n"
            "    return use_widget();\n"
            "}\n",
            encoding="utf-8",
        )
    (dest / "Makefile").write_text(MAKEFILE, encoding="utf-8")


def _replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise ValueError(f"{path}: needle not found:\n{old!r}")
    if text.count(old) != 1:
        raise ValueError(f"{path}: needle not unique ({text.count(old)} hits)")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _cpp(root: Path) -> Path:
    return root / "src" / "main.cpp"


def _hpp(root: Path) -> Path:
    return root / "include" / "app.hpp"


def _insert(root: Path, site: str, pos: str, snippet: str) -> Dict[str, str]:
    """Insert snippet at cpp/header start or end hook. Return inject record."""
    if site == "cpp":
        path = _cpp(root)
        if pos == "start":
            old = '#include "app.hpp"\n'
            new = '#include "app.hpp"\n' + snippet
        else:
            old = "    // CPP_HOOK\n"
            new = snippet + "    // CPP_HOOK\n"
    else:
        path = _hpp(root)
        if pos == "start":
            old = "#pragma once\n"
            new = "#pragma once\n" + snippet
        else:
            old = "    // HEADER_HOOK\n"
            new = snippet + "    // HEADER_HOOK\n"
    _replace_once(path, old, new)
    rel = str(path.relative_to(root)).replace("\\", "/")
    return {"file": rel, "old": old, "new": new}


def _inject_syntax_cascade(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    name = f"x{seed}"
    if site == "cpp":
        bad = f"    int {name} = ;\n    (void){name}.missing;\n"
    else:
        bad = f"    int {name} = ;\n    (void){name}.missing;\n"
    return _insert(root, site, pos, bad)


def _inject_missing_member(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    field = f"foo{seed}"
    if site == "cpp" and pos == "start":
        snippet = f"static int k_mm{seed} = Widget{{}}.{field};\n"
    elif site == "cpp":
        snippet = f"    acc += Widget{{}}.{field};\n"
    elif pos == "start":
        snippet = f"inline int k_mm{seed} = Widget{{}}.{field};\n"
    else:
        snippet = f"    return w.{field};\n"
    return _insert(root, site, pos, snippet)


def _inject_undeclared(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    fn = f"not_declared_{seed}"
    if site == "cpp" and pos == "start":
        snippet = f"static int k_ud{seed} = {fn}();\n"
    elif site == "cpp":
        snippet = f"    acc += {fn}();\n"
    elif pos == "start":
        snippet = f"inline int k_ud{seed} = {fn}();\n"
    else:
        snippet = f"    return {fn}();\n"
    return _insert(root, site, pos, snippet)


def _inject_wrong_sig(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    extra = 2 + seed
    if site == "cpp" and pos == "start":
        snippet = f"static int k_ws{seed} = compute(0, {extra});\n"
    elif site == "cpp":
        snippet = f"    acc += compute(0, {extra});\n"
    elif pos == "start":
        snippet = f"inline int k_ws{seed} = compute(0, {extra});\n"
    else:
        snippet = f"    return compute(0, {extra});\n"
    return _insert(root, site, pos, snippet)


def _inject_missing_include(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    name = f"missing_{seed}.hpp"
    snippet = f'#include "{name}"\n'
    return _insert(root, site, pos, snippet)


def _inject_redeclaration(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    ident = f"dup_{seed}"
    if site == "header" and pos == "start":
        snippet = f"int {ident} = 0;\nint {ident} = 1;\n"
    elif site == "header":
        snippet = f"    int {ident} = 0;\n    int {ident} = 1;\n"
    elif pos == "start":
        snippet = f"int {ident} = 0;\nint {ident} = 1;\n"
    else:
        snippet = f"    int {ident} = 0;\n    int {ident} = 1;\n"
    return _insert(root, site, pos, snippet)


def _inject_template(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    name = f"ident_{seed}"
    block = (
        f"template <typename T>\n"
        f"T {name}(T v) {{ return v.missing; }}\n"
        f"static int k_ti{seed} = {name}(1);\n"
    )
    if site == "cpp" and pos == "end":
        block = (
            f"    auto _ti{seed} = [](auto v) {{ return v.missing; }};\n"
            f"    acc += _ti{seed}(1);\n"
        )
    elif site == "header" and pos == "end":
        block = (
            f"    auto _ti{seed} = [](auto v) {{ return v.missing; }};\n"
            f"    return _ti{seed}(1);\n"
        )
    return _insert(root, site, pos, block)


def _inject_concept(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    name = f"twice_{seed}"
    path = _cpp(root) if site == "cpp" else _hpp(root)
    inc_old = '#include "app.hpp"\n' if site == "cpp" else "#pragma once\n"
    inc_new = inc_old + "#include <concepts>\n"
    if pos == "start":
        snippet = (
            f"template <std::integral T>\n"
            f"T {name}(T v) {{ return v + v; }}\n"
            f"static int k_cf{seed} = {name}(1.5);\n"
        )
        new = inc_new + snippet
        _replace_once(path, inc_old, new)
        rel = str(path.relative_to(root)).replace("\\", "/")
        return {"file": rel, "old": inc_old, "new": new}
    if site == "cpp":
        snippet = (
            f"    auto {name} = [](std::integral auto v) {{ return v + v; }};\n"
            f"    acc += {name}(1.5);\n"
        )
        hook = "    // CPP_HOOK\n"
    else:
        snippet = (
            f"    auto {name} = [](std::integral auto v) {{ return v + v; }};\n"
            f"    return {name}(1.5);\n"
        )
        hook = "    // HEADER_HOOK\n"
    _replace_once(path, inc_old, inc_new)
    rec = _insert(root, site, pos, snippet)
    rec["old"] = inc_old + hook
    rec["new"] = inc_new + snippet + hook
    return rec


def _inject_linker(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    fn = f"never_defined_{seed}"
    if site == "header" and pos == "start":
        snippet = f"int {fn}();\ninline int k_ld{seed} = {fn}();\n"
    elif site == "header":
        snippet = f"    return {fn}();\n"
        rec = _insert(root, site, pos, snippet)
        path = _hpp(root)
        decl_old = "int compute(int x);\n"
        decl_new = f"int compute(int x);\nint {fn}();\n"
        _replace_once(path, decl_old, decl_new)
        rec["old"] = decl_old + "    // HEADER_HOOK\n"
        rec["new"] = decl_new + snippet + "    // HEADER_HOOK\n"
        return rec
    elif pos == "start":
        snippet = f"int {fn}();\nstatic int k_ld{seed} = {fn}();\n"
    else:
        snippet = f"    acc += {fn}();\n"
        rec = _insert(root, site, pos, snippet)
        path = _cpp(root)
        old = '#include "app.hpp"\n'
        new = f'#include "app.hpp"\nint {fn}();\n'
        _replace_once(path, old, new)
        rec["old"] = old + "    // CPP_HOOK\n"
        rec["new"] = new + snippet + "    // CPP_HOOK\n"
        return rec
    return _insert(root, site, pos, snippet)


def _inject_fanout(root: Path, site: str, pos: str, seed: int) -> Dict[str, str]:
    """Always a shared-header fault; site/pos still vary where it sits."""
    field = f"fanout_{seed}"
    if pos == "start":
        snippet = f"inline int k_fo{seed} = Widget{{}}.{field};\n"
        return _insert(root, "header", "start", snippet)
    snippet = f"    return w.{field};\n"
    return _insert(root, "header", "end", snippet)


INJECTORS: Dict[str, Callable[[Path, str, str, int], Dict[str, str]]] = {
    "syntax_cascade": _inject_syntax_cascade,
    "missing_member": _inject_missing_member,
    "undeclared_identifier": _inject_undeclared,
    "wrong_function_signature": _inject_wrong_sig,
    "missing_include": _inject_missing_include,
    "redeclaration": _inject_redeclaration,
    "template_instantiation": _inject_template,
    "concept_failure": _inject_concept,
    "linker_undefined_symbol": _inject_linker,
    "shared_header_fanout": _inject_fanout,
}


def apply_inject(root: Path, kind: str, site: str, pos: str, seed: int) -> Dict[str, str]:
    """Apply one kind-specific injection. Return {file, old, new}."""
    try:
        return INJECTORS[kind](root, site, pos, seed)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"{kind} site={site} pos={pos} seed={seed}: {exc}") from exc


def iter_grid() -> List[Dict[str, Any]]:
    """Return the 300-case spec list."""
    specs: List[Dict[str, Any]] = []
    for kind in KINDS:
        for size_class, n_tus in SIZE_CLASSES:
            for variant in VARIANTS:
                specs.append(
                    {
                        "id": case_id(kind, size_class, int(variant["id"])),
                        "kind": kind,
                        "size_class": size_class,
                        "n_tus": n_tus,
                        "verbose": size_class == "verbose",
                        "site": variant["site"],
                        "pos": variant["pos"],
                        "seed": int(variant["seed"]),
                        "variant": int(variant["id"]),
                    }
                )
    return specs


def write_case(spec: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    """Materialize one case directory and fixture.json."""
    dest = out_dir / spec["id"]
    write_vanilla(
        dest,
        int(spec["n_tus"]),
        verbose=bool(spec["verbose"]),
        seed=int(spec["seed"]),
    )
    inject = apply_inject(
        dest,
        spec["kind"],
        spec["site"],
        spec["pos"],
        int(spec["seed"]),
    )
    fixture = {
        "id": spec["id"],
        "kind": spec["kind"],
        "size_class": spec["size_class"],
        "n_tus": spec["n_tus"],
        "source_bytes": _source_bytes(dest),
        "propagation": PROPAGATION[spec["kind"]],
        "gold_return_code": GOLD_RETURN_CODE,
        "inject": inject,
        "site": spec["site"],
        "pos": spec["pos"],
        "seed": spec["seed"],
        "variant": spec["variant"],
    }
    (dest / "fixture.json").write_text(
        json.dumps(fixture, indent=2) + "\n",
        encoding="utf-8",
    )
    return fixture


def generate(out_dir: Path) -> List[Dict[str, Any]]:
    """Write all 300 cases and an index.jsonl."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures: List[Dict[str, Any]] = []
    specs = iter_grid()
    if len(specs) != 300:
        raise RuntimeError(f"expected 300 specs, got {len(specs)}")
    for spec in specs:
        fixtures.append(write_case(spec, out_dir))
    index = out_dir / "index.jsonl"
    with index.open("w", encoding="utf-8") as fh:
        for row in fixtures:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return fixtures


def _run_make(tree: Path, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-j8"],
        cwd=str(tree),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def verify_vanilla(tmp: Path) -> None:
    """Compile one vanilla tree per size class and check ./app."""
    for size_class, n_tus in SIZE_CLASSES:
        dest = tmp / f"vanilla_{size_class}"
        write_vanilla(dest, n_tus, verbose=size_class == "verbose", seed=0)
        proc = _run_make(dest, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(
                f"vanilla {size_class} make failed:\n{proc.stderr[-2000:]}\n{proc.stdout[-2000:]}"
            )
        app = subprocess.run(
            [str(dest / "app")],
            check=False,
            capture_output=True,
        )
        if app.returncode != GOLD_RETURN_CODE:
            raise RuntimeError(
                f"vanilla {size_class} ./app exited {app.returncode}, want {GOLD_RETURN_CODE}"
            )


def verify_injected(out_dir: Path, *, limit: Optional[int] = None) -> None:
    """Assert `make` fails for each generated case."""
    index = out_dir / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit is not None:
        rows = rows[:limit]
    failed_pass: List[str] = []
    for i, row in enumerate(rows, start=1):
        tree = out_dir / row["id"]
        try:
            proc = _run_make(tree, timeout=180)
        except subprocess.TimeoutExpired:
            failed_pass.append(f"{row['id']}: make timed out")
            continue
        if proc.returncode == 0:
            failed_pass.append(f"{row['id']}: make unexpectedly succeeded")
        if i % 25 == 0:
            print(f"verify {i}/{len(rows)}", flush=True)
    if failed_pass:
        raise RuntimeError("inject verify failed:\n" + "\n".join(failed_pass))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--verify", action="store_true", help="vanilla gold + make-fail")
    parser.add_argument("--verify-limit", type=int, default=None)
    parser.add_argument("--skip-generate", action="store_true")
    args = parser.parse_args()
    if not args.skip_generate:
        fixtures = generate(args.out)
        print(f"wrote {len(fixtures)} cases under {args.out}", flush=True)
    if args.verify:
        tmp = Path("/tmp/ab300-vanilla-check")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        print("verifying vanilla gold...", flush=True)
        verify_vanilla(tmp)
        print("vanilla gold ok", flush=True)
        print("verifying injected make-fail...", flush=True)
        verify_injected(args.out, limit=args.verify_limit)
        print("inject make-fail ok", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
