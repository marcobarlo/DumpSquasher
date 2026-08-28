"""Independent stdout/stderr capture with a sequenced event log."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Literal

StreamName = Literal["stdout", "stderr"]


@dataclass(frozen=True)
class StreamEvent:
    """One contiguous chunk read from a single stream."""

    seq: int
    stream: StreamName
    t_ns: int
    offset: int
    size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "stream": self.stream,
            "t_ns": self.t_ns,
            "offset": self.offset,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StreamEvent:
        stream = data["stream"]
        if stream not in ("stdout", "stderr"):
            raise ValueError(f"invalid stream {stream!r}")
        return cls(
            seq=int(data["seq"]),
            stream=stream,  # type: ignore[arg-type]
            t_ns=int(data["t_ns"]),
            offset=int(data["offset"]),
            size=int(data["size"]),
        )


def load_events(path: Path) -> list[StreamEvent]:
    """Load events.jsonl in sequence order."""
    if not path.is_file():
        return []
    events: list[StreamEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            events.append(StreamEvent.from_dict(json.loads(line)))
    events.sort(key=lambda item: item.seq)
    return events


def reconstruct_raw(
    stdout: bytes,
    stderr: bytes,
    events: Iterable[StreamEvent],
) -> bytes:
    """Rebuild interleaved output from independent streams and the event log."""
    event_list = list(events)
    if not event_list:
        return stdout + stderr
    parts: list[bytes] = []
    for event in event_list:
        blob = stdout if event.stream == "stdout" else stderr
        parts.append(blob[event.offset : event.offset + event.size])
    return b"".join(parts)


class CaptureSink:
    """Write stdout.bin, stderr.bin, and events.jsonl under a run directory."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self._stdout = (run_dir / "stdout.bin").open("wb")
        self._stderr = (run_dir / "stderr.bin").open("wb")
        self._events = (run_dir / "events.jsonl").open("w", encoding="utf-8")
        self._offsets = {"stdout": 0, "stderr": 0}
        self._seq = 0
        self.event_count = 0

    def write(self, stream: StreamName, data: bytes, t_ns: int) -> None:
        if not data:
            return
        dest: BinaryIO = self._stdout if stream == "stdout" else self._stderr
        dest.write(data)
        dest.flush()
        event = StreamEvent(
            seq=self._seq,
            stream=stream,
            t_ns=t_ns,
            offset=self._offsets[stream],
            size=len(data),
        )
        self._events.write(json.dumps(event.to_dict(), separators=(",", ":")) + "\n")
        self._events.flush()
        self._offsets[stream] += len(data)
        self._seq += 1
        self.event_count += 1

    @property
    def stdout_bytes(self) -> int:
        return self._offsets["stdout"]

    @property
    def stderr_bytes(self) -> int:
        return self._offsets["stderr"]

    def close(self) -> None:
        for handle in (self._stdout, self._stderr, self._events):
            handle.close()

    def __enter__(self) -> CaptureSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
