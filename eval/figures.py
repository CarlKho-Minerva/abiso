"""Fig 1 (spec section 8): does it turn real posts into correct events?

    uv run python -m eval.figures            # reads eval/results.json

Dot plot, one column per field, one dot per system with its bootstrap 95% CI.
Colour follows the system (fixed slots, never re-assigned by rank) and every
system also has its own marker shape, so identity never rests on colour alone.
Palette: the dataviz skill's reference categorical slots 1-3 (blue, orange,
aqua), which pass its all-pairs colour-vision checks; aqua is below 3:1 on the
surface, so a table view is written next to the figure (fig1_table.csv).
Barangay Jaccard is not a proportion correct, so it stays in results.json only.
A results file marked "synthetic": true gets SYNTHETIC stamped across the plot.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS = REPO / "eval" / "results.json"
DEFAULT_OUTDIR = REPO / "eval" / "figures"

# Fixed entity -> colour/marker. A new system takes the next free slot; past
# three the palette no longer validates all-pairs, so we refuse.
SLOTS = [("#2a78d6", "o"), ("#eb6834", "s"), ("#1baf7a", "^")]
KNOWN = {"gemini": 0, "gemma": 1, "gemma-lora": 2}
LABELS = {"gemini": "Gemini, prompt only", "gemma": "Gemma 4 31B, prompt only",
          "gemma-lora": "Gemma 4 31B, LoRA fine-tuned"}

FIELD_ORDER = [("type", "Type"), ("barangays_exact", "Barangays\n(exact set)"),
               ("start_exact", "Start\n(exact)"), ("start_1h", "Start\n(±1 h)"),
               ("end_exact", "End\n(exact)"), ("end_1h", "End\n(±1 h)"), ("status", "Status")]

INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"


def assign_slots(systems: list[str]) -> dict[str, int]:
    out, used = {}, set()
    for s in systems:
        if s in KNOWN:
            out[s] = KNOWN[s]
            used.add(KNOWN[s])
    free = [i for i in range(len(SLOTS)) if i not in used]
    for s in systems:
        if s not in out:
            if not free:
                raise ValueError("more than 3 systems: the palette is only validated for 3; facet instead")
            out[s] = free.pop(0)
    return out


def caption(res: dict) -> str:
    any_sys = next(iter(res["systems"].values()))
    n_type = any_sys["fields"]["type"]["n"]
    n_dis = any_sys["fields"]["start_exact"]["n"]
    n_stat = any_sys["fields"]["status"]["n"]
    return (f"n = {n_type} held-out posts for type; place and time fields on the {n_dis} labelled as a "
            f"disruption; status on {n_stat}. Bars: 95% bootstrap CI ({res['n_boot']:,} resamples). "
            f"With n = {n_type}, CIs span roughly ±13 points: compare systems with the paired "
            "differences in results.json, not by whether bars overlap.")


def fig1(res: dict, outdir: Path) -> list[Path]:
    systems = list(res["systems"])
    slots = assign_slots(systems)
    fields = [(k, lab) for k, lab in FIELD_ORDER if k in next(iter(res["systems"].values()))["fields"]]
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10,
                         "svg.fonttype": "none", "axes.edgecolor": GRID})
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    width = 0.22
    offsets = {s: (i - (len(systems) - 1) / 2) * width for i, s in enumerate(systems)}
    for s in systems:
        color, marker = SLOTS[slots[s]]
        for x, (f, _) in enumerate(fields):
            cell = res["systems"][s]["fields"][f]
            if cell["point"] is None:
                continue
            y = 100 * cell["point"]
            lo, hi = (100 * v for v in cell["ci95"])
            ax.errorbar(x + offsets[s], y, yerr=[[y - lo], [hi - y]], fmt="none",
                        ecolor=color, elinewidth=2, capsize=0, zorder=2)
            ax.plot(x + offsets[s], y, marker=marker, markersize=9 if marker == "^" else 8, color=color,
                    markeredgecolor=SURFACE, markeredgewidth=2, linestyle="none", zorder=3,
                    label=LABELS.get(s, s) if x == 0 else None)
    ax.set_xticks(range(len(fields)), [lab for _, lab in fields], color=INK2)
    ax.set_ylim(-3, 103)   # room so a 0% or 100% dot is not cut by the frame
    ax.set_yticks(range(0, 101, 20))
    ax.tick_params(axis="y", colors=INK2)
    ax.tick_params(length=0)
    ax.set_ylabel(f"% correct on {res['n_posts']} held-out posts", color=INK)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(side == "bottom")
    fig.text(0.01, 0.975, "Does Abiso turn real posts into correct events?", color=INK,
             fontsize=12, fontweight="bold", ha="left", va="top")
    handles, labels = ax.get_legend_handles_labels()
    leg = fig.legend(handles, labels, loc="upper left", frameon=False, ncol=len(systems),
                     bbox_to_anchor=(0.005, 0.925), handletextpad=0.3, columnspacing=1.6, fontsize=9)
    for t in leg.get_texts():
        t.set_color(INK2)
    if res.get("synthetic"):
        ax.text(0.5, 0.5, "SYNTHETIC: NOT A RESULT", transform=ax.transAxes, ha="center",
                va="center", fontsize=28, color="#c0bfba", alpha=0.6, rotation=20, zorder=0)
    fig.text(0.01, 0.01, caption(res), color=INK2, fontsize=7.5, wrap=True, ha="left", va="bottom")
    fig.tight_layout(rect=(0, 0.08, 1, 0.86))
    outdir.mkdir(parents=True, exist_ok=True)
    paths = [outdir / "fig1_accuracy.svg", outdir / "fig1_accuracy.png"]
    for p in paths:
        fig.savefig(p, facecolor=SURFACE)
    plt.close(fig)

    table = outdir / "fig1_table.csv"
    with table.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["system", "field", "n", "pct_correct", "ci95_low", "ci95_high"])
        for s in systems:
            for f, _ in fields:
                c = res["systems"][s]["fields"][f]
                if c["point"] is None:
                    continue
                w.writerow([s, f, c["n"], round(100 * c["point"], 1),
                            round(100 * c["ci95"][0], 1), round(100 * c["ci95"][1], 1)])
    return paths + [table]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render Fig 1 from eval/results.json")
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    a = ap.parse_args(argv)
    if not a.results.exists():
        print(f"ERROR: {a.results} not found: run eval.score first", file=sys.stderr)
        return 2
    for p in fig1(json.loads(a.results.read_text()), a.outdir):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
