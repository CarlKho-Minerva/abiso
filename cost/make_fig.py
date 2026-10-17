"""Fig 4: cost per 1,000 posts. Reads cost/results.json; writes cost/fig4_cost.{png,svg}.

One measure (USD per 1,000 posts, PHP printed beside it), linear axis from zero,
one hue; the Mac bar is hatched because it is an estimate, not a measurement.
Thin whiskers show the range: Gemini and PC over 3 runs of the 20 posts,
Mac over the published power range.

    uv run --no-project --with matplotlib python cost/make_fig.py
"""
from __future__ import annotations

import json
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import common  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
BLUE = "#2a78d6"  # palette slot 1 (validated: dataviz reference palette)


def main() -> None:
    r = json.loads((common.HERE / "results.json").read_text())
    fx = r["rates"]["usd_php"]["value"]
    g, pc, mac = r["gemini"], r["pc"], r["mac"]
    rows = [
        ("Gemini 3.8 Flash API, default\nthinking (price to 2026-12-31)", g["usd_per_1000"], g["usd_per_1000_range_over_reps"], False),
    ] + ([
        ("Gemini 3.8 Flash, low thinking\n(accuracy not yet scored)", g["thinking_low"]["usd_per_1000"],
         [g["thinking_low"]["usd_per_1000"]] * 2, False),
    ] if "thinking_low" in g else []) + [
        ("Gemma 4 31B on Carl's PC\nat Meralco rate", pc["cost_per_1000_headline"]["meralco"]["usd"],
         [pc["cost_per_1000_range_over_reps"]["low"]["meralco"]["usd"], pc["cost_per_1000_range_over_reps"]["high"]["meralco"]["usd"]], False),
        ("Gemma 4 31B on Carl's PC\nat PG&E rate (SF)", pc["cost_per_1000_headline"]["pge_etouc_offpeak"]["usd"],
         [pc["cost_per_1000_range_over_reps"]["low"]["pge_etouc_offpeak"]["usd"], pc["cost_per_1000_range_over_reps"]["high"]["pge_etouc_offpeak"]["usd"]], False),
        ("Gemma 4 31B on M5 Max Mac\nat Meralco rate (ESTIMATE)", mac["cost_per_1000_mid"]["meralco"]["usd"],
         [mac["cost_per_1000_low"]["meralco"]["usd"], mac["cost_per_1000_high"]["meralco"]["usd"]], True),
    ]
    plt.rcParams.update({"font.family": "Arial", "font.size": 11, "text.parse_math": False, "text.color": INK,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK})
    fig, ax = plt.subplots(figsize=(10, 6.2), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ys = list(range(len(rows)))[::-1]
    xmax = max(max(rng) for _, _, rng, _ in rows)
    for y, (name, val, rng, est) in zip(ys, rows):
        ax.barh(y, val, height=0.42, color=BLUE if not est else SURFACE, edgecolor=BLUE,
                hatch="////" if est else None, linewidth=1.2 if est else 0, zorder=3)
        ax.plot(rng, [y, y], color=INK2, linewidth=1, zorder=4)
        for x in rng:
            ax.plot([x, x], [y - 0.08, y + 0.08], color=INK2, linewidth=1, zorder=4)
        ax.text(max(val, rng[1]) + xmax * 0.015, y, (f"${val:,.2f}  ·  ₱{val * fx:,.0f}" if val * fx >= 10 else f"${val:,.3f}  ·  ₱{val * fx:,.2f}"),
                va="center", ha="left", fontsize=12, fontweight="bold", color=INK)
    ax.set_yticks(ys, [n for n, *_ in rows])
    ax.set_xlim(0, xmax * 1.32)
    ax.set_xlabel("US dollars per 1,000 posts (pesos beside each bar)")
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(axis="y", length=0)

    php_pc = pc["cost_per_1000_headline"]["meralco"]["php"]
    fig.suptitle(f"Compiling 1,000 official posts: ₱{g['php_per_1000']:.0f} on the Gemini API, "
                 f"about ₱{php_pc:.0f} of electricity on Carl's PC", x=0.02, ha="left", fontsize=14, fontweight="bold")
    wb, w0 = pc["gpu_w_batch"]["mean"], pc["gpu_w_baseline"]["mean"]
    share = pc["wh_per_post_gpu_token_share"]
    cap = (f"PC: RTX 5090 drew {wb:.0f} W (mean of 3 runs) while compiling, vs {w0:.0f} W before/after; the vLLM server is shared with "
           f"other jobs, so energy is charged by our share of tokens served: {share['mean']:.2f} Wh/post GPU "
           f"(range {share['min']:.2f}–{share['max']:.2f}), plus 100 W platform and 90% PSU efficiency (assumptions). "
           f"nvidia-smi every 0.5 s over SSH, 20 SYNTHETIC posts (4 each: city, MECO, MCWD, DepEd, PAGASA) x 3 runs. Whiskers = range over 3 runs (Mac: published power range; low-thinking Gemini: 1 run). "
           f"Rates: Meralco ₱{r['rates']['meralco_residential']['value']}/kWh (Sep 2026), PG&E E-TOU-C winter off-peak "
           f"${r['rates']['pge_etouc_winter_offpeak']['value']}/kWh, ₱{fx}/$ (2026-10-02). Electricity only: no hardware cost. "
           f"Gemini price doubles on 2027-01-01. Mac is an estimate from published speed and power, not run.")
    fig.text(0.02, 0.015, textwrap.fill(cap, 175), ha="left", va="bottom", fontsize=8.2, color=INK2)
    fig.subplots_adjust(left=0.26, right=0.97, top=0.9, bottom=0.27)
    for ext in ("png", "svg"):
        fig.savefig(common.HERE / f"fig4_cost.{ext}", facecolor=SURFACE)
    print(common.HERE / "fig4_cost.png")


if __name__ == "__main__":
    main()
