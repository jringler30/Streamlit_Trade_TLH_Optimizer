"""
tax_alpha_chart.py
──────────────────
Grouped bar chart: Tax Alpha by strategy for each market regime.
Highlighted bar = strategy selected on the comparative analysis slide.
Background bars = alternative strategies tested, shown for context.

Overlay: Sharpe Ratio as diamond markers on a secondary axis, showing
why the selected strategy was chosen despite not always having the
highest tax alpha — it delivers the best risk-adjusted performance.

Output: tax_alpha_chart.png  (drop into slide directly)
"""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import numpy as np
from pathlib import Path

HERE = Path(__file__).resolve().parent
CSV  = HERE.parent / "comparative_analysis_results.csv"
OUT  = HERE / "tax_alpha_chart.png"

df = pd.read_csv(CSV)

# ── Period lengths for annualizing tax alpha ──────────────────────────────────
PERIOD_YEARS = {
    "Bear (2007–2008)":     2,
    "Baseline (2010–2019)": 10,
    "Bull (2023–2024)":     2,
}

REGIMES = [
    {
        "label":      "Bear Market\n2007–2008",
        "market":     "Bear (2007–2008)",
        "portfolio":  "3-ETF (SPY/TLT/GLD)",
        "highlight":  "Abs Threshold 20% | TLH=10%",
        "background": [
            "Quarterly Rebal | TLH=10%",
            "Monthly Rebal | TLH=10%",
            "Rel Threshold 50% | TLH=10%",
        ],
        "color":      "#C0392B",
        "color_bg":   "#E8A09A",
        "color_dot":  "#7B241C",
    },
    {
        "label":      "Baseline Market\n2010–2019",
        "market":     "Baseline (2010–2019)",
        "portfolio":  "3-ETF (SPY/TLT/GLD)",
        "highlight":  "Monthly Rebal | TLH=10%",
        "background": [
            "Quarterly Rebal | TLH=10%",
            "Yearly Rebal | TLH=10%",
            "Abs Threshold 20% | TLH=10%",
        ],
        "color":      "#1A5276",
        "color_bg":   "#7FB3D3",
        "color_dot":  "#0E2D4A",
    },
    {
        "label":      "Bull Market\n2023–2024",
        "market":     "Bull (2023–2024)",
        "portfolio":  "100/0 (TA ETF)",
        "highlight":  "Rel Threshold 25% | TLH=10%",
        "background": [
            "Abs Threshold 10% | TLH=10%",
            "Monthly Rebal | TLH=10%",
            "Quarterly Rebal | TLH=10%",
        ],
        "color":      "#1E8449",
        "color_bg":   "#82C89E",
        "color_dot":  "#145A32",
    },
]

SHORT = {
    "Abs Threshold 20% | TLH=10%": "Abs 20%\n+ TLH",
    "Abs Threshold 10% | TLH=10%": "Abs 10%\n+ TLH",
    "Quarterly Rebal | TLH=10%":   "Qtrly Rebal\n+ TLH",
    "Monthly Rebal | TLH=10%":     "Monthly\nRebal + TLH",
    "Yearly Rebal | TLH=10%":      "Yearly\nRebal + TLH",
    "Rel Threshold 25% | TLH=10%": "Rel 25%\n+ TLH",
    "Rel Threshold 50% | TLH=10%": "Rel 50%\n+ TLH",
}

# ── Shared axis ranges (consistent across all panels, annualized) ─────────────
TAX_ALPHA_MIN, TAX_ALPHA_MAX = -500,   11_000
SHARPE_MIN,    SHARPE_MAX    =  0.0,    2.2

# ── Figure: 3 panels ──────────────────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(15, 6.5))
fig.patch.set_facecolor("white")
fig.subplots_adjust(wspace=0.42, left=0.06, right=0.97, top=0.80, bottom=0.24)

for ax, regime in zip(axes, REGIMES):
    sub = df[
        (df["Market State"] == regime["market"]) &
        (df["Portfolio"]    == regime["portfolio"]) &
        (df["TLH Status"]   == "On (10%)")
    ].set_index("Strategy Label")

    years      = PERIOD_YEARS[regime["market"]]
    strategies = [regime["highlight"]] + regime["background"]
    tax_vals   = [sub.loc[s, "Tax Alpha 2"] / years  for s in strategies]
    sharpe_vals= [sub.loc[s, "Sharpe Ratio"]          for s in strategies]

    colors     = [regime["color"]]    + [regime["color_bg"]] * 3
    edgecolors = [regime["color"]]    + ["none"] * 3
    linewidths = [2.0]                + [0] * 3

    x = np.arange(len(strategies))

    # ── Bars (Tax Alpha, primary axis) ────────────────────────────────────────
    bars = ax.bar(x, tax_vals, color=colors, edgecolor=edgecolors,
                  linewidth=linewidths, width=0.55, zorder=3)

    # Tax alpha labels above/below bars
    y_max = max(tax_vals) if max(tax_vals) > 0 else 1000
    for bar, val in zip(bars, tax_vals):
        label = "~$0" if abs(val) < 50 else f"${val:+,.0f}"
        offset = y_max * 0.04
        ypos   = bar.get_height() + offset if val >= 0 else bar.get_height() - offset * 2
        ax.text(bar.get_x() + bar.get_width() / 2, ypos,
                label, ha="center", va="bottom", fontsize=8,
                fontweight="bold" if val == tax_vals[0] else "normal",
                color=regime["color"] if val == tax_vals[0] else "#666666")

    # ── Sharpe dots (secondary axis) ──────────────────────────────────────────
    ax2 = ax.twinx()
    dot_sizes   = [120 if i == 0 else 60 for i in range(len(strategies))]
    dot_colors  = [regime["color_dot"] if i == 0 else "#AAAAAA"
                   for i in range(len(strategies))]
    dot_zorders = [6 if i == 0 else 5 for i in range(len(strategies))]

    for xi, (sv, ds, dc, dz) in enumerate(
            zip(sharpe_vals, dot_sizes, dot_colors, dot_zorders)):
        ax2.scatter(xi, sv, s=ds, color=dc, marker="D",
                    zorder=dz, linewidths=0)
        # Sharpe label — only annotate highlighted and most contrasting bg bar
        if xi == 0:
            ax2.annotate(f"Sharpe: {sv:.2f}",
                         xy=(xi, sv), xytext=(xi + 0.05, sv + 0.04),
                         fontsize=8, fontweight="bold", color=regime["color_dot"],
                         ha="left")
        else:
            ax2.annotate(f"{sv:.2f}",
                         xy=(xi, sv), xytext=(xi, sv + 0.03),
                         fontsize=7, color="#888888", ha="center")

    # Connect dots with a light line
    ax2.plot(x, sharpe_vals, color="#CCCCCC", linewidth=1,
             linestyle="--", zorder=4)

    # Secondary axis styling — fixed range shared across all panels
    ax2.set_ylabel("Sharpe Ratio", fontsize=8.5, color="#555555", labelpad=6)
    ax2.tick_params(axis="y", labelsize=7.5, colors="#777777")
    ax2.spines["right"].set_color("#DDDDDD")
    ax2.spines["top"].set_visible(False)
    ax2.set_ylim(SHARPE_MIN, SHARPE_MAX)

    # ── X-axis labels ─────────────────────────────────────────────────────────
    short_names = [SHORT.get(s, s) for s in strategies]
    # Mark selected strategy label with a star
    short_names[0] = short_names[0] + "\n★"
    ax.set_xticks(x)
    ax.set_xticklabels(short_names, fontsize=7.5, ha="center",
                       color="#222222")

    # ── Primary axis styling ──────────────────────────────────────────────────
    ax.set_title(regime["label"], fontsize=12, fontweight="bold",
                 color=regime["color"], pad=10)
    ax.axhline(0, color="#AAAAAA", linewidth=0.8, zorder=2)
    ax.set_facecolor("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#DDDDDD")
    ax.spines["bottom"].set_color("#DDDDDD")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.tick_params(axis="y", labelsize=8, colors="#555555")
    ax.tick_params(axis="x", length=0)
    ax.grid(axis="y", color="#EEEEEE", linewidth=0.7, zorder=1)
    ax.set_ylim(TAX_ALPHA_MIN, TAX_ALPHA_MAX)
    ax.set_ylabel("Tax Alpha ($ / Year)", fontsize=9, color="#555555")

# ── Title ─────────────────────────────────────────────────────────────────────
fig.suptitle("Annualized Tax Alpha vs. Sharpe Ratio by Strategy Across Market Regimes",
             fontsize=14, fontweight="bold", y=0.97, color="#1A1A1A")

# ── Legend ────────────────────────────────────────────────────────────────────
bar_selected = mpatches.Patch(color="#555555",
    label="★  Selected strategy")
bar_alt      = mpatches.Patch(facecolor="#CCCCCC",
    label="    Alternative strategies")
dot_selected = mlines.Line2D([], [], color="#333333", marker="D",
    linestyle="None", markersize=8, label="◆  Sharpe Ratio (selected)")
dot_alt      = mlines.Line2D([], [], color="#AAAAAA", marker="D",
    linestyle="None", markersize=6, label="◆  Sharpe Ratio (alternatives)")

fig.legend(handles=[bar_selected, bar_alt, dot_selected, dot_alt],
           loc="lower center", ncol=4, fontsize=8,
           frameon=False, bbox_to_anchor=(0.5, 0.01))

# ── Footnote ──────────────────────────────────────────────────────────────────
fig.text(0.5, 0.068,
         "Bars = Tax Alpha per year (total ÷ period length: Bear 2yr, Baseline 10yr, Bull 2yr)  |  "
         "Diamonds = Sharpe Ratio  |  ★ Selected for best composite score  |  "
         "$1M starting capital  |  35% ST / 20% LT tax rates",
         ha="center", fontsize=7, color="#888888", style="italic")

plt.savefig(OUT, dpi=180, bbox_inches="tight", facecolor="white")
print(f"Saved → {OUT}")
