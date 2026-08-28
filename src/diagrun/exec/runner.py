"""Process execution with independent stdout/stderr capture."""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import BinaryIO, Mapping, Optional, Sequence

from diagrun.config import DiagrunConfig
from diagrun.exec.capture import CaptureSink, StreamName
from diagrun.exec.inject import apply_inject
from diagrun.store.runs import RunStore

CHUNK_SIZE = 65536


@dataclass(frozen=True)
class CapturedRun:
    """Result of executing a command through the capture wrapper."""

    id: str
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    started_at_ns: int
    finished_at_ns: int
    stdout_bytes: int
    stderr_bytes: int
    event_count: int
    inject_enabled: bool = True
    inject_methods: tuple[str, ...] = ()
    inject_flags: tuple[str, ...] = ()
    collapse_parse_recovery: bool = False
    executed: tuple[str, ...] = ()


def run_command(
    argv: Sequence[str],
    store: RunStore,
    *,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    passthrough: bool = True,
    stdout: Optional[BinaryIO] = None,
    stderr: Optional[BinaryIO] = None,
    config: Optional[DiagrunConfig] = None,
) -> CapturedRun:
    """Run argv, tee streams into the store, and preserve the original exit code.

    stdout and stderr are captured independently. Arrival order is recorded in
    ``events.jsonl`` so interleaved output can be reconstructed.

    Pipes are used rather than a PTY so the two streams stay separable. The
    child therefore sees ``isatty() == False``.
    """
    if not argv:
        raise ValueError("command must be non-empty")
    workdir = cwd or os.getcwd()
    settings = config if config is not None else DiagrunConfig.resolve(env=env)
    child_env = dict(os.environ if env is None else env)
    run_id, run_dir = store.allocate()
    started_at_ns = time.time_ns()
    injected = apply_inject(
        argv,
        child_env,
        enabled=settings.inject_diagnostics,
        wrap_dir=run_dir / "wrappers",
    )
    child_env = injected.env
    executed = injected.argv

    live_out: Optional[BinaryIO]
    live_err: Optional[BinaryIO]
    if stdout is not None:
        live_out = stdout
    elif passthrough:
        live_out = sys.stdout.buffer
    else:
        live_out = None
    if stderr is not None:
        live_err = stderr
    elif passthrough:
        live_err = sys.stderr.buffer
    else:
        live_err = None

    with CaptureSink(run_dir) as sink:
        exit_code = _execute(executed, workdir, child_env, sink, live_out, live_err)
        stdout_bytes = sink.stdout_bytes
        stderr_bytes = sink.stderr_bytes
        event_count = sink.event_count

    finished_at_ns = time.time_ns()
    captured = CapturedRun(
        id=run_id,
        command=tuple(argv),
        cwd=workdir,
        exit_code=exit_code,
        started_at_ns=started_at_ns,
        finished_at_ns=finished_at_ns,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        event_count=event_count,
        inject_enabled=injected.enabled,
        inject_methods=tuple(injected.methods),
        inject_flags=tuple(injected.flags),
        collapse_parse_recovery=settings.collapse_parse_recovery,
        executed=tuple(executed),
    )
    store.finalize(captured)
    return captured


def _execute(
    argv: Sequence[str],
    workdir: str,
    env: Optional[Mapping[str, str]],
    sink: CaptureSink,
    live_out: Optional[BinaryIO],
    live_err: Optional[BinaryIO],
) -> int:
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=workdir,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
        )
    except FileNotFoundError:
        return 127
    except PermissionError:
        return 126

    assert proc.stdout is not None
    assert proc.stderr is not None
    fd_meta: dict[int, tuple[StreamName, Optional[BinaryIO]]] = {
        proc.stdout.fileno(): ("stdout", live_out),
        proc.stderr.fileno(): ("stderr", live_err),
    }
    open_fds = set(fd_meta)

    try:
        while open_fds:
            ready, _, _ = select.select(list(open_fds), [], [], 0.1)
            if not ready:
                if proc.poll() is not None:
                    _drain(open_fds, fd_meta, sink)
                    break
                continue
            for fd in ready:
                _read_fd(fd, open_fds, fd_meta, sink)
        proc.stdout.close()
        proc.stderr.close()
        return proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _drain(
    open_fds: set[int],
    fd_meta: dict[int, tuple[StreamName, Optional[BinaryIO]]],
    sink: CaptureSink,
) -> None:
    while open_fds:
        ready, _, _ = select.select(list(open_fds), [], [], 0)
        if not ready:
            # Blocking read remaining bytes after the child has exited.
            for fd in list(open_fds):
                _read_fd(fd, open_fds, fd_meta, sink, block=True)
            continue
        for fd in ready:
            _read_fd(fd, open_fds, fd_meta, sink)


def _read_fd(
    fd: int,
    open_fds: set[int],
    fd_meta: dict[int, tuple[StreamName, Optional[BinaryIO]]],
    sink: CaptureSink,
    block: bool = False,
) -> None:
    if fd not in open_fds:
        return
    stream, live = fd_meta[fd]
    if block:
        chunks: list[bytes] = []
        while True:
            data = os.read(fd, CHUNK_SIZE)
            if not data:
                break
            chunks.append(data)
        data = b"".join(chunks)
    else:
        data = os.read(fd, CHUNK_SIZE)
    if not data:
        open_fds.discard(fd)
        return
    sink.write(stream, data, time.time_ns())
    if live is not None:
        live.write(data)
        live.flush()
