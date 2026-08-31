#!/usr/bin/env python3
"""Copy vanilla LMCache csrc files, inject one compile error, restore.

Mutations are search/replace on utils.cpp / utils.h. Vanilla copies live under
/tmp/lmcache-vanilla-csrc. Writes go through docker exec (tree is root-owned).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TypedDict

CONTAINER = "vllm-ascend-dsv4-lmcache"
CSRC = "/workspace/LMCache-Ascend/csrc"
HOST_CSRC = Path(
    "/home/m00926961/work/lmcache-ascend-scripts-master/LMCache-Ascend/csrc"
)
VANILLA = Path("/tmp/lmcache-vanilla-csrc")
TRACKED = ("utils.cpp", "utils.h")


class Case(TypedDict):
    id: str
    title: str
    propagation: str
    file: str
    old: str
    new: str


CASES: list[Case] = [
    {
        "id": "syntax_cascade",
        "title": "syntax (parse recovery)",
        "propagation": "broken initializer plus a follow-on member access",
        "file": "utils.cpp",
        "old": (
            "kvcache_ops::AscendType get_dtype_from_torch(at::ScalarType scalarType) {\n"
            "  if (scalarType == at::ScalarType::Float) {"
        ),
        "new": (
            "kvcache_ops::AscendType get_dtype_from_torch(at::ScalarType scalarType) {\n"
            "  int x = ;\n"
            "  (void)x.missing;\n"
            "  if (scalarType == at::ScalarType::Float) {"
        ),
    },
    {
        "id": "missing_member",
        "title": "missing member",
        "propagation": "one semantic error, no parse cascade",
        "file": "utils.cpp",
        "old": "  config.page_buffer_size = page_buffer_size;",
        "new": "  config.not_a_real_field = page_buffer_size;",
    },
    {
        "id": "type_mismatch",
        "title": "invalid conversion",
        "propagation": "wrong return type vs declaration",
        "file": "utils.cpp",
        "old": "    return kvcache_ops::AscendType::FP32;",
        "new": "    return true;",
    },
    {
        "id": "undeclared",
        "title": "undeclared identifier",
        "propagation": "call to a name that does not exist",
        "file": "utils.cpp",
        "old": (
            "kvcache_ops::AscendType get_dtype_from_torch(at::ScalarType scalarType) {\n"
            "  if (scalarType == at::ScalarType::Float) {"
        ),
        "new": (
            "kvcache_ops::AscendType get_dtype_from_torch(at::ScalarType scalarType) {\n"
            "  return not_declared(scalarType);\n"
            "  if (scalarType == at::ScalarType::Float) {"
        ),
    },
]


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", CONTAINER, *args],
        check=check,
        text=True,
        capture_output=True,
    )


def backup_vanilla() -> None:
    """Snapshot tracked files. Never replace a good snapshot with an empty one."""
    VANILLA.mkdir(parents=True, exist_ok=True)
    for name in TRACKED:
        dest = VANILLA / name
        if dest.exists() and dest.stat().st_size > 1000:
            continue
        text = _docker("cat", f"{CSRC}/{name}").stdout
        if len(text) < 1000:
            raise RuntimeError(f"refusing to snapshot empty {name} ({len(text)} bytes)")
        dest.write_text(text)


def restore_vanilla() -> None:
    """Always restore tracked files from the vanilla copies."""
    for name in TRACKED:
        src = VANILLA / name
        if not src.exists() or src.stat().st_size < 1000:
            raise FileNotFoundError(f"vanilla {name} missing or too small")
        subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "tee", f"{CSRC}/{name}"],
            input=src.read_text(),
            check=True,
            text=True,
            capture_output=True,
        )
        _docker("chmod", "644", f"{CSRC}/{name}")
    _restore_dir_mode()


def inject(case: Case) -> None:
    restore_vanilla()
    path = VANILLA / case["file"]
    # read current (vanilla) from container after restore
    text = _docker("cat", f"{CSRC}/{case['file']}").stdout
    if case["old"] not in text:
        raise ValueError(f"{case['id']}: needle not found in {case['file']}")
    patched = text.replace(case["old"], case["new"], 1)
    subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "tee", f"{CSRC}/{case['file']}"],
        input=patched,
        check=True,
        text=True,
        capture_output=True,
    )
    _docker("chmod", "666", f"{CSRC}/{case['file']}")
    _writable_dir()


def _writable_dir() -> None:
    _docker("chmod", "0777", CSRC)


def _restore_dir_mode() -> None:
    _docker("chmod", "0755", CSRC)
