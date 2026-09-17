"""Inject codegen-neutral diagnostic flags into compiler invocations.

Default is on. Strategies by tool:

* raw gcc/clang: splice flags into argv
* Make: wrap ``CC``/``CXX`` (leaves makefile ``CXXFLAGS`` intact)
* CMake configure: ``CMAKE_*_COMPILER_LAUNCHER`` so Ninja/Make compiles get flags
* CMake --build / Ninja: PATH compiler shims + Clang ``CCC_OVERRIDE_OPTIONS``

Flags only change diagnostic output, not generated code.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

DIAGNOSTIC_FLAGS = (
    "-fdiagnostics-format=json",
    "-fdiagnostics-color=never",
)

_COMPILER_EXACT = {"gcc", "g++", "cc", "c++", "clang", "clang++"}
_MAKE_NAMES = {"make", "gmake", "mingw32-make"}
_NINJA_NAMES = {"ninja", "ninja-build"}
_CMAKE_NAMES = {"cmake", "cmake3"}
_SHIM_NAMES = ("gcc", "g++", "cc", "c++", "clang", "clang++")


@dataclass
class InjectResult:
    """Rewritten command and environment after diagnostic injection."""

    argv: list[str]
    env: dict[str, str]
    enabled: bool
    methods: list[str] = field(default_factory=list)
    flags: tuple[str, ...] = DIAGNOSTIC_FLAGS


def tool_name(argv0: str) -> str:
    """Return the basename of a command, without ``.exe``."""
    return _basename(argv0)


def _basename(argv0: str) -> str:
    name = Path(argv0).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def is_compiler_name(name: str) -> bool:
    """Return True if *name* looks like gcc/clang or a prefixed cross compiler."""
    if name in _COMPILER_EXACT:
        return True
    if re.match(r"^(gcc|g\+\+|clang|clang\+\+)-\d+", name):
        return True
    for suffix in ("-gcc", "-g++", "-clang", "-clang++", "-cc", "-c++"):
        if name.endswith(suffix):
            return True
    return False


def is_make_name(name: str) -> bool:
    return name in _MAKE_NAMES


def is_ninja_name(name: str) -> bool:
    return name in _NINJA_NAMES


def is_cmake_name(name: str) -> bool:
    return name in _CMAKE_NAMES


def apply_inject(
    argv: Sequence[str],
    env: Optional[Mapping[str, str]] = None,
    *,
    enabled: bool = True,
    wrap_dir: Optional[Path] = None,
) -> InjectResult:
    """Rewrite argv/env so child compiles emit structured diagnostics."""
    base_env = dict(os.environ if env is None else env)
    if not argv:
        raise ValueError("command must be non-empty")
    if not enabled:
        return InjectResult(argv=list(argv), env=base_env, enabled=False, methods=[], flags=())

    result_env = dict(base_env)
    result_env["DIAGRUN_DIAG_FLAGS"] = " ".join(DIAGNOSTIC_FLAGS)
    result_env["DIAGRUN_INJECT_DIAGNOSTICS"] = "1"
    methods: list[str] = []
    new_argv = list(argv)
    name = _basename(argv[0])
    had_path = "PATH" in base_env
    lookup_env = dict(result_env)
    if not had_path:
        lookup_env["PATH"] = os.environ.get("PATH", os.defpath)

    if wrap_dir is not None:
        wrap_dir.mkdir(parents=True, exist_ok=True)
        _install_shims(wrap_dir, lookup_env)
        if had_path:
            _prepend_path(result_env, wrap_dir)
            methods.append("path.wrappers")
        launcher = wrap_dir / "compiler_launch"
        _write_launcher(launcher)
    else:
        launcher = None

    cmake_build = is_cmake_name(name) and "--build" in argv[1:]
    # Do not export CCC_OVERRIDE_OPTIONS for cmake --build: AscendC/bisheng
    # is Clang-based and may reject -fdiagnostics-format=json.
    if not cmake_build:
        _append_clang_override(result_env)
        methods.append("env.CCC_OVERRIDE_OPTIONS")

    if is_compiler_name(name):
        new_argv = [argv[0], *DIAGNOSTIC_FLAGS, *argv[1:]]
        methods.append("argv.compiler")
    elif is_make_name(name):
        if wrap_dir is not None:
            if (wrap_dir / "g++").is_file():
                result_env["CXX"] = str(wrap_dir / "g++")
                methods.append("env.CXX")
            if (wrap_dir / "gcc").is_file():
                result_env["CC"] = str(wrap_dir / "gcc")
                methods.append("env.CC")
    elif is_cmake_name(name):
        if "--build" in argv[1:]:
            methods.append("cmake.build")
        else:
            if launcher is not None:
                new_argv = _cmake_configure_argv(new_argv, launcher)
                methods.append("cmake.compiler_launcher")
    elif is_ninja_name(name):
        methods.append("ninja")
    else:
        if wrap_dir is not None:
            if "CC" not in result_env and (wrap_dir / "gcc").is_file():
                result_env["CC"] = str(wrap_dir / "gcc")
            if "CXX" not in result_env and (wrap_dir / "g++").is_file():
                result_env["CXX"] = str(wrap_dir / "g++")
            methods.append("env.generic")

    return InjectResult(
        argv=new_argv,
        env=result_env,
        enabled=True,
        methods=methods,
        flags=DIAGNOSTIC_FLAGS,
    )


def _prepend_path(env: dict[str, str], directory: Path) -> None:
    current = env.get("PATH", os.defpath)
    env["PATH"] = str(directory) + os.pathsep + current


def _append_clang_override(env: dict[str, str]) -> None:
    extra = " ".join("+" + flag for flag in DIAGNOSTIC_FLAGS)
    existing = env.get("CCC_OVERRIDE_OPTIONS", "").strip()
    env["CCC_OVERRIDE_OPTIONS"] = (existing + " " + extra).strip()


def _install_shims(wrap_dir: Path, env: Mapping[str, str]) -> None:
    search_env = dict(env)
    # Resolve real compilers from the original PATH, not wrap_dir.
    path_parts = [part for part in search_env.get("PATH", os.defpath).split(os.pathsep) if part]
    path_parts = [part for part in path_parts if Path(part).resolve() != wrap_dir.resolve()]
    search_env["PATH"] = os.pathsep.join(path_parts)
    for name in _SHIM_NAMES:
        real = shutil.which(name, path=search_env["PATH"])
        if real is None:
            continue
        _write_shim(wrap_dir / name, real)


def _write_shim(path: Path, real_compiler: str) -> None:
    path.write_text(
        "#!{python}\n"
        "import os\n"
        "import sys\n"
        "REAL = {real!r}\n"
        "flags = os.environ.get('DIAGRUN_DIAG_FLAGS', '').split()\n"
        "os.execvp(REAL, [REAL, *flags, *sys.argv[1:]])\n".format(
            python=sys.executable, real=real_compiler
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _write_launcher(path: Path) -> None:
    """CMake ``COMPILER_LAUNCHER``: ``launcher <compiler> <args...>``."""
    path.write_text(
        "#!{python}\n"
        "import os\n"
        "import sys\n"
        "if len(sys.argv) < 2:\n"
        "    sys.stderr.write('diagrun compiler_launch: missing compiler\\n')\n"
        "    sys.exit(2)\n"
        "compiler = sys.argv[1]\n"
        "flags = os.environ.get('DIAGRUN_DIAG_FLAGS', '').split()\n"
        "os.execvp(compiler, [compiler, *flags, *sys.argv[2:]])\n".format(python=sys.executable),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _cmake_configure_argv(argv: list[str], launcher: Path) -> list[str]:
    launch = str(launcher)
    extra = [
        f"-DCMAKE_C_COMPILER_LAUNCHER={launch}",
        f"-DCMAKE_CXX_COMPILER_LAUNCHER={launch}",
    ]
    already = "CMAKE_CXX_COMPILER_LAUNCHER" in " ".join(argv)
    if already:
        return argv
    return [argv[0], *extra, *argv[1:]]
