"""PATH shims so bare ``cmake --build`` (pip/setup.py) runs through diagrun.

LMCache-Ascend install is a compound shell command::

    . env && cmake -S ... && cmake --build ... && cmake --install ...

DSH only wraps a *pure* ``cmake --build``. A ``cmake`` shim earlier on PATH
intercepts the ``--build`` hop, runs it through ``diagrun --format json``,
and passes configure / ``--install`` through unchanged.

Recursion is blocked with ``DIAGRUN_ACTIVE=1`` in the diagrun child env.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional, Sequence

SHIM_NAMES = ("cmake", "cmake3")
ACTIVE_ENV = "DIAGRUN_ACTIVE"
DISABLE_ENV = "DIAGRUN_DISABLE"


def is_disabled(env: Optional[dict[str, str]] = None) -> bool:
    """Return True when wrapping should be skipped."""
    source = os.environ if env is None else env
    if source.get(ACTIVE_ENV) == "1":
        return True
    raw = (source.get(DISABLE_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def is_cmake_build_argv(argv: Sequence[str]) -> bool:
    """True when this invocation is ``cmake --build ...`` (not configure/install)."""
    return "--build" in argv


def real_tool(name: str, *, search_path: Optional[str] = None) -> Optional[str]:
    """Resolve *name* from PATH, ignoring diagrun shim directories."""
    path = search_path if search_path is not None else os.environ.get("PATH", os.defpath)
    parts = []
    for item in path.split(os.pathsep):
        if not item:
            continue
        if Path(item).name == "wrappers":
            continue
        marker = Path(item) / ".diagrun-pathwrap"
        if marker.is_file():
            continue
        parts.append(item)
    return shutil.which(name, path=os.pathsep.join(parts) if parts else path)


def install_wrappers(directory: Path, *, search_path: Optional[str] = None) -> list[Path]:
    """Write ``cmake`` / ``cmake3`` shims into *directory*. Return written paths."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".diagrun-pathwrap").write_text("1\n", encoding="utf-8")
    written: list[Path] = []
    for name in SHIM_NAMES:
        real = real_tool(name, search_path=search_path)
        if real is None:
            continue
        path = directory / name
        _write_cmake_shim(path, real)
        written.append(path)
    if not written:
        raise FileNotFoundError("no cmake on PATH to wrap")
    return written


def _write_cmake_shim(path: Path, real_cmake: str) -> None:
    path.write_text(
        "#!{python}\n"
        "import os\n"
        "import sys\n"
        "REAL = {real!r}\n"
        "ACTIVE = {active!r}\n"
        "DISABLE = {disable!r}\n"
        "argv = sys.argv[1:]\n"
        "disabled = os.environ.get(ACTIVE) == '1'\n"
        "raw = (os.environ.get(DISABLE) or '').strip().lower()\n"
        "disabled = disabled or raw in ('1', 'true', 'yes', 'on')\n"
        "if disabled or '--build' not in argv:\n"
        "    os.execv(REAL, [REAL, *argv])\n"
        "os.execv({python!r}, [{python!r}, '-m', 'diagrun', '--format', 'json', '--', REAL, *argv])\n".format(
            python=sys.executable,
            real=real_cmake,
            active=ACTIVE_ENV,
            disable=DISABLE_ENV,
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def default_wrapper_dir() -> Path:
    """System dir when root, else XDG/local share."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return Path("/opt/diagrun/bin")
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "diagrun" / "bin"
    return Path.home() / ".local" / "share" / "diagrun" / "bin"
