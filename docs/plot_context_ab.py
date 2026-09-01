#!/usr/bin/env python3
"""Two full-context stacks: with vs without diagrun_build.

Each block is one context piece (system, schemas, user, then each tool
result). Height ∝ Unicode \\w+ tokens.

Tool results use the last payload per callId so DSH spill/pruner
replacements (stock 50 KB inline, then 4096+1024 char prune) are what
the model sees — not the pre-bound raw dump.

Sessions filled after the realistic (stock-bounding) no-tool arm.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OUT = Path(__file__).with_name("context-with-vs-without.png")
SESSIONS = Path.home() / ".dsh" / "sessions"
WITH_SID = "9f94cfd6-c94b-45ab-943f-c14f49d7a4b2"
WITHOUT_SID = "eec2accc-f4f1-4050-ba38-7351a5988986"

C = {
    "system": "#E8EAED",
    "schema": "#F1F3F4",
    "user": "#BBDEFB",
    "policy": "#D1C4E9",
    "compact": "#C5EDE3",
    "log": "#FFF3C4",
    "dump": "#FFE8A3",
    "read": "#D6E8F7",
    "edit": "#E8E0F4",
    "sandbox": "#FFD6CC",
    "other": "#E6E9EE",
    "bg": "#FCFDFE",
    "edge": "#90A4AE",
    "text": "#37474F",
    "muted": "#78909C",
}
INK = C["text"]
WORD_RE = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> int:
    """Count Unicode word tokens. Works for prose and JSON keys/values."""
    return len(WORD_RE.findall(text or ""))


def _load(path: Path) -> list[dict]:
    raw = subprocess.check_output(["zstd", "-d", "-c", str(path)])
    events = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _message_text(content: object) -> str:
    """Pull visible text from a DSH message content tree. Do not walk unrelated keys."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                inner = block.get("content")
                if inner is not None and inner is not block:
                    parts.append(_message_text(inner))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return _message_text(content.get("content") or content.get("text") or [])
    return ""


def _session_path(cwd_slug: str, sid: str) -> Path:
    return SESSIONS / cwd_slug / f"session-{sid}" / "session.jsonl.zstd"


def _classify_tool(name: str, text: str) -> str:
    n = name.lower()
    tl = text.lower()
    if n == "diagrun_build":
        return "compact"
    if n == "diagrun_show":
        return "compact"
    if n == "diagrun_get_raw":
        return "log"
    if n == "read":
        return "read"
    if n in ("write", "edit"):
        return "sandbox" if "sandbox" in tl else "edit"
    if n == "bash":
        if '"run_id"' in text and '"roots"' in text and '"status"' in text:
            return "compact"
        if "sandbox" in tl or "refusing" in tl or "approval" in tl:
            return "sandbox"
        if any(
            tok in text
            for tok in (
                "g++",
                "make:",
                "error:",
                "[stderr]",
                "cmake",
                "gmake",
                "bisheng",
                "output truncated",
                "tool result middle pruned",
            )
        ):
            return "dump"
        return "other"
    if "sandbox" in tl:
        return "sandbox"
    return "other"


def _context_blocks(path: Path) -> tuple[list[tuple[str, int, str]], str]:
    """Ordered context pieces: prefix, then each tool result."""
    events = _load(path)
    blocks: list[tuple[str, int, str]] = []
    n_tools = 0
    system_text = ""
    tools: list = []
    for ev in events:
        if ev.get("type") != "request/header":
            continue
        header = (ev.get("data") or {}).get("header") or {}
        system_text = header.get("system") or ""
        tools = header.get("tools") or []
        n_tools = len(tools)
        break

    blocks.append(("system prompt", words(system_text), C["system"]))
    diagrun = any((t.get("name") or "").startswith("diagrun") for t in tools if isinstance(t, dict))
    schema_label = f"tool schemas ({n_tools} tools" + (", incl. diagrun_*)" if diagrun else ", no diagrun_*)")
    blocks.append((schema_label, words(json.dumps(tools, ensure_ascii=False)), C["schema"]))

    saw_task = False
    saw_policy = False
    for ev in events:
        if ev.get("type") != "user/message":
            continue
        data = ev.get("data") or {}
        text = _message_text(data.get("content"))
        if not text.strip():
            continue
        source = data.get("source") or {}
        kind = source.get("kind") or ""
        if not saw_task and (kind == "user" or "fails to compile" in text.lower()):
            blocks.append(("user prompt", words(text), C["user"]))
            saw_task = True
            continue
        if (not saw_policy) and (source.get("form") == "snapshot" or "runtime context" in text.lower()):
            blocks.append(("runtime policy", words(text), C["policy"]))
            saw_policy = True

    calls: dict[str, str] = {}
    # Last payload per callId = DSH surface after spill/pruner replacements.
    order: list[str] = []
    latest: dict[str, tuple[str, str]] = {}
    for ev in events:
        data = ev.get("data") or {}
        if ev.get("type") == "tool/call":
            cid = str(data.get("callId") or "")
            if cid:
                calls[cid] = str(data.get("name") or "")
            continue
        if ev.get("type") != "tool/result":
            continue
        src = (data.get("message") or {}).get("source") or {}
        cid = str(src.get("callId") or data.get("callId") or "")
        name = calls.get(cid, "")
        text = _message_text((data.get("message") or {}).get("content"))
        if cid not in latest:
            order.append(cid)
        latest[cid] = (name, text)

    for tool_n, cid in enumerate(order, start=1):
        name, text = latest[cid]
        cat = _classify_tool(name, text)
        label = f"{tool_n}. {name or cat}"
        color = {
            "compact": C["compact"],
            "log": C["log"],
            "dump": C["dump"],
            "read": C["read"],
            "edit": C["edit"],
            "sandbox": C["sandbox"],
            "other": C["other"],
        }[cat]
        blocks.append((label, words(text), color))

    sid = path.parent.name.replace("session-", "")[:8]
    return blocks, sid


def _draw_stack(ax, blocks: list[tuple[str, int, str]], ymax: float, title: str, ylabel: bool) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, ymax)
    ax.set_xticks([])
    ax.spines["top"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="y", labelsize=8, colors=C["muted"], length=3)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{int(v):,}"))
    if ylabel:
        ax.set_ylabel("words", fontsize=9, color=C["muted"])
    else:
        ax.tick_params(axis="y", labelleft=False)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=10, color=INK)
    ax.set_facecolor("white")

    y = ymax
    for label, n, color in blocks:
        if n <= 0:
            continue
        y -= n
        ax.bar(
            0.5,
            n,
            bottom=y,
            width=0.72,
            color=color,
            edgecolor=C["edge"],
            linewidth=0.35 if n >= ymax * 0.008 else 0.15,
            align="center",
        )
        if n >= ymax * 0.045:
            ax.text(
                0.5,
                y + n / 2,
                f"{label}\n{n:,} words",
                ha="center",
                va="center",
                fontsize=7.5,
                color=INK,
                linespacing=1.15,
            )

    total = sum(n for _, n, _ in blocks)
    ax.text(0.5, -0.04 * ymax, f"{total:,} words total", ha="center", va="top", fontsize=9, color=C["muted"], clip_on=False)


def main() -> None:
    left, sid_l = _context_blocks(_session_path("--tmp-dsh-with-tool--", WITH_SID))
    right, sid_r = _context_blocks(_session_path("--tmp-dsh-no-tool--", WITHOUT_SID))
    ymax = float(max(sum(n for _, n, _ in left), sum(n for _, n, _ in right)))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.facecolor": C["bg"],
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "text.color": INK,
        }
    )
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(11.5, 11.0), dpi=160, sharey=True)
    fig.subplots_adjust(left=0.10, right=0.78, top=0.88, bottom=0.08, wspace=0.08)

    fig.suptitle(
        "Model-visible context  ·  DSH stock bounding  ·  LMCache host_stub",
        fontsize=15,
        fontweight="bold",
        y=0.97,
    )
    fig.text(
        0.44,
        0.925,
        "Stock dsh-base: 64 KB bash tail, 50 KB spill, 8k-char prune (4k head + 1k tail). "
        "Bash timeout 10 min so the rebuild can finish. Assistant thinking omitted.",
        ha="center",
        fontsize=9,
        color=C["muted"],
    )

    _draw_stack(ax_l, left, ymax, "With  diagrun_build", ylabel=True)
    _draw_stack(ax_r, right, ymax, "Without  diagrun_build", ylabel=False)

    legend_items = [
        Patch(facecolor=C["system"], edgecolor=C["edge"], label="system prompt"),
        Patch(facecolor=C["schema"], edgecolor=C["edge"], label="tool schemas"),
        Patch(facecolor=C["user"], edgecolor=C["edge"], label="user prompt"),
        Patch(facecolor=C["policy"], edgecolor=C["edge"], label="runtime policy"),
        Patch(facecolor=C["compact"], edgecolor=C["edge"], label="diagrun_build (roots)"),
        Patch(facecolor=C["log"], edgecolor=C["edge"], label="diagrun_get_raw"),
        Patch(facecolor=C["dump"], edgecolor=C["edge"], label="bash (DSH-bounded)"),
        Patch(facecolor=C["read"], edgecolor=C["edge"], label="read"),
        Patch(facecolor=C["edit"], edgecolor=C["edge"], label="edit / write"),
        Patch(facecolor=C["sandbox"], edgecolor=C["edge"], label="sandbox error"),
        Patch(facecolor=C["other"], edgecolor=C["edge"], label="other"),
    ]
    fig.legend(
        handles=legend_items,
        loc="center left",
        bbox_to_anchor=(0.79, 0.5),
        frameon=False,
        fontsize=8,
    )
    fig.text(
        0.44,
        0.02,
        f"Sessions {sid_l} (with) and {sid_r} (without). "
        "Last tool result per callId (post-spill/pruner). Words = Unicode \\w+.",
        ha="center",
        fontsize=7.5,
        color=C["muted"],
    )
    fig.savefig(OUT, dpi=160)
    print(f"wrote {OUT}")
    print("with", [(lab, n) for lab, n, _ in left], "total", sum(n for _, n, _ in left))
    print("without", [(lab, n) for lab, n, _ in right], "total", sum(n for _, n, _ in right))


if __name__ == "__main__":
    main()
