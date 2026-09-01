#!/usr/bin/env python3
"""Plot 300-case A/B fix rate and model-visible context.

Writes docs/ab300-fix-and-context.png. Does not overwrite
docs/context-with-vs-without.png.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results.jsonl"
OUT = ROOT / "docs" / "ab300-fix-and-context.png"

C_WITH = "#2A9D8F"
C_WITHOUT = "#E9C46A"
INK = "#37474F"
MUTED = "#78909C"
EDGE = "#90A4AE"
BG = "#FCFDFE"

KINDS_ORDER = [
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
]
SIZE_ORDER = ["1", "4", "16", "64", "verbose"]


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _rate(rows: List[Dict[str, Any]], key: str, value: str, arm: str) -> Tuple[float, int, int]:
    subset = [r for r in rows if r.get("arm") == arm and str(r.get(key)) == value]
    n = len(subset)
    if n == 0:
        return float("nan"), 0, 0
    wins = sum(1 for r in subset if r.get("fixed"))
    return wins / n, wins, n


def _words_by_arm(rows: List[Dict[str, Any]]) -> Dict[str, List[float]]:
    out: Dict[str, List[float]] = {"with": [], "without": []}
    for r in rows:
        arm = r.get("arm")
        w = r.get("full_context_words")
        if arm in out and isinstance(w, (int, float)):
            out[arm].append(float(w))
    return out


def plot(rows: List[Dict[str, Any]], out: Path) -> None:
    n_with = sum(1 for r in rows if r.get("arm") == "with")
    n_without = sum(1 for r in rows if r.get("arm") == "without")
    fix_with = sum(1 for r in rows if r.get("arm") == "with" and r.get("fixed"))
    fix_without = sum(1 for r in rows if r.get("arm") == "without" and r.get("fixed"))

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.facecolor": BG,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "text.color": INK,
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 9.2), dpi=160)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.10, wspace=0.28, hspace=0.42)
    fig.suptitle("300-case DSH A/B  ·  bash wrap vs stock bash", fontsize=15, fontweight="bold", y=0.98)
    fig.text(
        0.5,
        0.93,
        f"with n={n_with}  fix={fix_with}/{n_with or 1} ({(fix_with / n_with * 100) if n_with else 0:.0f}%)   ·   "
        f"without n={n_without}  fix={fix_without}/{n_without or 1} ({(fix_without / n_without * 100) if n_without else 0:.0f}%)"
        "    ·    words = Unicode \\w+ on last tool result per callId + system/schemas/user",
        ha="center",
        fontsize=8.5,
        color=MUTED,
    )

    ax = axes[0, 0]
    kinds = [k for k in KINDS_ORDER if any(str(r.get("kind")) == k for r in rows)] or KINDS_ORDER
    x = np.arange(len(kinds))
    w = 0.38
    a_vals = [_rate(rows, "kind", k, "with")[0] * 100 for k in kinds]
    b_vals = [_rate(rows, "kind", k, "without")[0] * 100 for k in kinds]
    ax.bar(x - w / 2, np.nan_to_num(a_vals), w, color=C_WITH, edgecolor=EDGE, label="with wrap")
    ax.bar(x + w / 2, np.nan_to_num(b_vals), w, color=C_WITHOUT, edgecolor=EDGE, label="without (bash)")
    ax.set_ylim(0, 105)
    ax.set_ylabel("fix rate (%)")
    ax.set_title("Fix rate by kind", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([k.replace("_", "\n") for k in kinds], fontsize=6.5)
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax = axes[0, 1]
    sizes = [s for s in SIZE_ORDER if any(str(r.get("size_class")) == s for r in rows)] or SIZE_ORDER
    x = np.arange(len(sizes))
    a_vals = [_rate(rows, "size_class", s, "with")[0] * 100 for s in sizes]
    b_vals = [_rate(rows, "size_class", s, "without")[0] * 100 for s in sizes]
    ax.bar(x - w / 2, np.nan_to_num(a_vals), w, color=C_WITH, edgecolor=EDGE, label="with")
    ax.bar(x + w / 2, np.nan_to_num(b_vals), w, color=C_WITHOUT, edgecolor=EDGE, label="without")
    ax.set_ylim(0, 105)
    ax.set_ylabel("fix rate (%)")
    ax.set_title("Fix rate by size", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(sizes, fontsize=9)
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax = axes[1, 0]
    dist = _words_by_arm(rows)
    data = [dist["with"] or [0], dist["without"] or [0]]
    bp = ax.boxplot(
        data,
        tick_labels=["with", "without"],
        patch_artist=True,
        widths=0.55,
        medianprops={"color": INK, "linewidth": 1.6},
        flierprops={"marker": "o", "markersize": 3, "alpha": 0.5},
    )
    for patch, color in zip(bp["boxes"], (C_WITH, C_WITHOUT)):
        patch.set_facecolor(color)
        patch.set_edgecolor(EDGE)
    ax.set_ylabel("full-context words")
    ax.set_title("Context-word distribution", fontsize=12, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{int(v):,}"))

    ax = axes[1, 1]
    med_a: List[float] = []
    med_b: List[float] = []
    for k in kinds:
        wa = [
            float(r["full_context_words"])
            for r in rows
            if r.get("arm") == "with" and r.get("kind") == k and isinstance(r.get("full_context_words"), (int, float))
        ]
        wb = [
            float(r["full_context_words"])
            for r in rows
            if r.get("arm") == "without" and r.get("kind") == k and isinstance(r.get("full_context_words"), (int, float))
        ]
        med_a.append(float(np.median(wa)) if wa else 0.0)
        med_b.append(float(np.median(wb)) if wb else 0.0)
    x = np.arange(len(kinds))
    ax.bar(x - w / 2, med_a, w, color=C_WITH, edgecolor=EDGE, label="with")
    ax.bar(x + w / 2, med_b, w, color=C_WITHOUT, edgecolor=EDGE, label="without")
    ax.set_ylabel("median words")
    ax.set_title("Median context words by kind", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([k.replace("_", "\n") for k in kinds], fontsize=6.5)
    ax.legend(frameon=False, fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: f"{int(v):,}"))

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=RESULTS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    rows = load_rows(args.results)
    if not rows:
        print(f"no rows in {args.results}; writing empty figure")
    plot(rows, args.out)


if __name__ == "__main__":
    main()
