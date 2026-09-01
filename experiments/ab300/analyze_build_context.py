#!/usr/bin/env python3
"""Re-read DSH sessions and plot build-dump share vs wrap savings.

Writes:
  docs/ab300-fix-and-context.png
  docs/ab300-build-dump-share.png
  docs/ab300-build-savings.png
  docs/ab300-first-payload.png
  experiments/ab300/build_context.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from measure_session import SESSIONS, load_events, split_tool_words, words  # noqa: E402
from plot_ab300 import KINDS_ORDER, SIZE_ORDER, C_WITH, C_WITHOUT, EDGE, INK, MUTED, BG, load_rows, plot as plot_fix  # noqa: E402

SESSIONS_ROOT = SESSIONS
OUT_SHARE = ROOT / "docs" / "ab300-build-dump-share.png"
OUT_SAVE = ROOT / "docs" / "ab300-build-savings.png"
OUT_FIRST = ROOT / "docs" / "ab300-first-payload.png"
OUT_FIX = ROOT / "docs" / "ab300-fix-and-context.png"
CACHE = HERE / "build_context.jsonl"


def _session_path(session_id: str) -> Optional[Path]:
    if not session_id:
        return None
    hits = list(SESSIONS_ROOT.glob(f"**/session-{session_id}/session.jsonl.zstd"))
    return hits[0] if hits else None


def enrich(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        sid = str(row.get("session_id") or "")
        path = _session_path(sid)
        extra: Dict[str, Any] = {
            "compact_words": 0,
            "dump_words": 0,
            "edit_words": 0,
            "read_words": 0,
            "other_tool_words": 0,
            "build_words": 0,
            "n_compact": 0,
            "n_dump": 0,
            "first_build_kind": None,
            "first_build_words": 0,
            "first_build_bytes": 0,
            "prefix_words": max(
                0,
                int(row.get("full_context_words") or 0) - int(row.get("tool_result_words_total") or 0),
            ),
        }
        if path is not None and path.is_file():
            events = load_events(path)
            extra.update(split_tool_words(events))
            full = int(row.get("full_context_words") or 0)
            extra["prefix_words"] = max(0, full - int(row.get("tool_result_words_total") or 0))
        merged = {**row, **extra}
        out.append(merged)
        if i % 50 == 0:
            print(f"  sessions {i}/{len(rows)}", flush=True)
    return out


def _pair(rows: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    by: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r.get("case_id")), {})[str(r.get("arm"))] = r
    pairs = []
    for _cid, arms in by.items():
        if "with" in arms and "without" in arms:
            pairs.append((arms["with"], arms["without"]))
    return pairs


def _med(xs: List[float]) -> float:
    if not xs:
        return float("nan")
    return float(np.median(xs))


def _pct(xs: List[float]) -> float:
    if not xs:
        return float("nan")
    return float(np.median(xs))


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pairs = _pair(rows)
    dump_share_wo: List[float] = []
    compact_share_wi: List[float] = []
    build_save: List[float] = []
    first_save: List[float] = []
    first_wo: List[float] = []
    first_wi: List[float] = []
    by_size: Dict[str, Dict[str, List[float]]] = {s: {"save": [], "dump_share": [], "first_save": []} for s in SIZE_ORDER}
    by_kind: Dict[str, List[float]] = {k: [] for k in KINDS_ORDER}

    for wi, wo in pairs:
        full_wo = float(wo.get("full_context_words") or 0)
        full_wi = float(wi.get("full_context_words") or 0)
        dump_wo = float(wo.get("dump_words") or 0)
        compact_wi = float(wi.get("compact_words") or 0)
        build_wo = float(wo.get("build_words") or dump_wo)
        build_wi = float(wi.get("build_words") or compact_wi)
        if full_wo > 0:
            dump_share_wo.append(100.0 * dump_wo / full_wo)
        if full_wi > 0:
            compact_share_wi.append(100.0 * compact_wi / full_wi)
        if build_wo > 0 and int(wi.get("n_compact") or 0) > 0:
            save = 100.0 * (1.0 - build_wi / build_wo)
            build_save.append(save)
            size = str(wi.get("size_class"))
            kind = str(wi.get("kind"))
            if size in by_size:
                by_size[size]["save"].append(save)
                by_size[size]["dump_share"].append(100.0 * dump_wo / full_wo if full_wo else 0.0)
            if kind in by_kind:
                by_kind[kind].append(save)
        fb_wo = float(wo.get("first_build_words") or 0)
        fb_wi = float(wi.get("first_build_words") or 0)
        if fb_wo > 0 and fb_wi > 0:
            first_wo.append(fb_wo)
            first_wi.append(fb_wi)
            fs = 100.0 * (1.0 - fb_wi / fb_wo)
            first_save.append(fs)
            size = str(wi.get("size_class"))
            if size in by_size:
                by_size[size]["first_save"].append(fs)

    return {
        "n_pairs": len(pairs),
        "median_dump_share_pct": _med(dump_share_wo),
        "median_compact_share_pct": _med(compact_share_wi),
        "median_build_save_pct": _med(build_save),
        "mean_build_save_pct": float(np.mean(build_save)) if build_save else float("nan"),
        "median_first_save_pct": _med(first_save),
        "median_first_wo": _med(first_wo),
        "median_first_wi": _med(first_wi),
        "pairs_build_smaller": sum(1 for v in build_save if v > 0),
        "n_build_save": len(build_save),
        "by_size": {s: {k: _med(vs) for k, vs in d.items()} for s, d in by_size.items()},
        "by_kind": {k: _med(vs) for k, vs in by_kind.items()},
        "build_save_list": build_save,
        "dump_share_list": dump_share_wo,
        "first_save_list": first_save,
    }


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.facecolor": BG,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "text.color": INK,
        }
    )


def plot_dump_share(rows: List[Dict[str, Any]], summary: Dict[str, Any], out: Path) -> None:
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.2), dpi=160)
    fig.suptitle("Share of full context that is the build payload", fontsize=14, fontweight="bold")
    fig.text(
        0.5,
        0.02,
        "Unicode \\w+ words · last tool result per callId · dump = non-compact bash · compact = diagrun roots JSON · "
        f"n={summary['n_pairs']} pairs · source: experiments/ab300/results.jsonl + DSH sessions",
        ha="center",
        fontsize=8,
        color=MUTED,
    )

    ax = axes[0]
    sizes = [s for s in SIZE_ORDER if any(str(r.get("size_class")) == s for r in rows)]
    x = np.arange(len(sizes))
    w = 0.38
    dump_pct = []
    compact_pct = []
    for s in sizes:
        wo = [100.0 * (r.get("dump_words") or 0) / r["full_context_words"]
              for r in rows if r.get("arm") == "without" and str(r.get("size_class")) == s and (r.get("full_context_words") or 0) > 0]
        wi = [100.0 * (r.get("compact_words") or 0) / r["full_context_words"]
              for r in rows if r.get("arm") == "with" and str(r.get("size_class")) == s and (r.get("full_context_words") or 0) > 0]
        dump_pct.append(_med(wo))
        compact_pct.append(_med(wi))
    ax.bar(x - w / 2, compact_pct, w, color=C_WITH, edgecolor=EDGE, label="wrap · compact JSON")
    ax.bar(x + w / 2, dump_pct, w, color=C_WITHOUT, edgecolor=EDGE, label="stock · bash dump")
    ax.set_xticks(x)
    ax.set_xticklabels(sizes)
    ax.set_ylabel("% of full-context words")
    ax.set_title("Median share of full context", fontsize=11, fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax = axes[1]
    # stacked composition median: prefix / build / other tools
    labels = ["wrap", "stock bash"]
    prefix, build, other = [], [], []
    for arm, build_key in (("with", "compact_words"), ("without", "dump_words")):
        rs = [r for r in rows if r.get("arm") == arm and (r.get("full_context_words") or 0) > 0]
        p = _med([100.0 * (r.get("prefix_words") or 0) / r["full_context_words"] for r in rs])
        b = _med([100.0 * (r.get(build_key) or 0) / r["full_context_words"] for r in rs])
        o = [max(0.0, 100.0 - p - b)]
        prefix.append(p)
        build.append(b)
        other.append(o[0])
    x = np.arange(2)
    ax.bar(x, prefix, 0.55, color="#90A4AE", label="prefix (system+schemas+user)")
    ax.bar(x, build, 0.55, bottom=prefix, color="#E76F51", label="build payload")
    bottom2 = [a + b for a, b in zip(prefix, build)]
    ax.bar(x, other, 0.55, bottom=bottom2, color="#F4A261", label="other tools (edit/read/…)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("% of full-context words (median)")
    ax.set_title("What the model actually reads", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")


def plot_savings(rows: List[Dict[str, Any]], summary: Dict[str, Any], out: Path) -> None:
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.2), dpi=160)
    fig.suptitle("Build-context savings  ·  wrap vs stock dump", fontsize=14, fontweight="bold")
    fig.text(
        0.5,
        0.02,
        "Savings = 1 − (wrap build words / stock dump words) on paired cases · "
        "build words = compact JSON (wrap) or non-compact bash (stock) · prefix excluded",
        ha="center",
        fontsize=8,
        color=MUTED,
    )

    ax = axes[0]
    sizes = SIZE_ORDER
    x = np.arange(len(sizes))
    vals = [summary["by_size"].get(s, {}).get("save", float("nan")) for s in sizes]
    first = [summary["by_size"].get(s, {}).get("first_save", float("nan")) for s in sizes]
    w = 0.38
    ax.bar(x - w / 2, np.nan_to_num(vals), w, color=C_WITH, edgecolor=EDGE, label="all rebuilds in session")
    ax.bar(x + w / 2, np.nan_to_num(first), w, color="#264653", edgecolor=EDGE, label="first compile only")
    ax.axhline(0, color=EDGE, linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(sizes)
    ax.set_ylabel("median savings (%)")
    ax.set_title("Savings by program size", fontsize=11, fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax = axes[1]
    kinds = [k for k in KINDS_ORDER if not np.isnan(summary["by_kind"].get(k, float("nan")))]
    x = np.arange(len(kinds))
    vals = [summary["by_kind"][k] for k in kinds]
    ax.barh(x, np.nan_to_num(vals), color=C_WITH, edgecolor=EDGE, height=0.7)
    ax.axvline(0, color=EDGE, linewidth=0.8)
    ax.set_yticks(x)
    ax.set_yticklabels([k.replace("_", " ") for k in kinds], fontsize=8)
    ax.set_xlabel("median savings (%)")
    ax.set_title("Savings by fault kind", fontsize=11, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.invert_yaxis()

    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")


def plot_first_payload(rows: List[Dict[str, Any]], summary: Dict[str, Any], out: Path) -> None:
    _style()
    fig, axes = plt.subplots(1, 2, figsize=(12.2, 5.2), dpi=160)
    fig.suptitle("First compile payload  ·  compact JSON vs bash dump", fontsize=14, fontweight="bold")
    fig.text(
        0.5,
        0.02,
        "First build tool result only (not later rebuilds) · words = Unicode \\w+",
        ha="center",
        fontsize=8,
        color=MUTED,
    )

    ax = axes[0]
    sizes = SIZE_ORDER
    x = np.arange(len(sizes))
    w = 0.38
    wi_w, wo_w = [], []
    for s in sizes:
        a = [float(r.get("first_build_words") or 0) for r in rows if r.get("arm") == "with" and str(r.get("size_class")) == s]
        b = [float(r.get("first_build_words") or 0) for r in rows if r.get("arm") == "without" and str(r.get("size_class")) == s]
        wi_w.append(_med(a))
        wo_w.append(_med(b))
    ax.bar(x - w / 2, wi_w, w, color=C_WITH, edgecolor=EDGE, label="wrap")
    ax.bar(x + w / 2, wo_w, w, color=C_WITHOUT, edgecolor=EDGE, label="stock bash")
    ax.set_xticks(x)
    ax.set_xticklabels(sizes)
    ax.set_ylabel("median words")
    ax.set_title("First build result by size", fontsize=11, fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax = axes[1]
    saves = summary["first_save_list"]
    ax.hist(saves, bins=24, color=C_WITH, edgecolor=EDGE, range=(-50, 100))
    ax.axvline(summary["median_first_save_pct"], color=INK, linewidth=1.6, label=f"median {summary['median_first_save_pct']:.0f}%")
    ax.set_xlabel("first-payload savings (%)")
    ax.set_ylabel("paired cases")
    ax.set_title("Paired first-payload savings", fontsize=11, fontweight="bold")
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=HERE / "results.jsonl")
    parser.add_argument("--skip-sessions", action="store_true")
    args = parser.parse_args()
    rows = load_rows(args.results)
    print(f"loaded {len(rows)} rows", flush=True)
    if not args.skip_sessions:
        print("reading DSH sessions…", flush=True)
        rows = enrich(rows)
        CACHE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"wrote {CACHE}", flush=True)
    elif CACHE.is_file():
        rows = load_rows(CACHE)
        print(f"loaded cache {CACHE} ({len(rows)})", flush=True)
    summary = summarize(rows)
    print(json.dumps({k: v for k, v in summary.items() if not k.endswith("_list") and k not in ("by_size", "by_kind")}, indent=2))
    print("by_size", json.dumps(summary["by_size"], indent=2))
    print("by_kind", json.dumps(summary["by_kind"], indent=2))
    plot_fix(rows, OUT_FIX)
    plot_dump_share(rows, summary, OUT_SHARE)
    plot_savings(rows, summary, OUT_SAVE)
    plot_first_payload(rows, summary, OUT_FIRST)


if __name__ == "__main__":
    main()
