"""
tlh_vs_no_tlh.py
─────────────────────────────────────────────────────────────────────────────
Standalone comparison of TLH vs No-TLH across two valuation bases:

  1. Pre-Liquidation  — portfolio NAV while still running (no forced sale)
  2. Post-Liquidation — hypothetical after-tax value if everything sold today

For each matched pair (same Portfolio / Market State / Rebal Type / Rebal Value)
the script computes:

  Pre-Liq Tax Alpha   = TLH Final NAV        − No-TLH Final NAV
  Post-Liq Tax Alpha  = TLH Liquidation NAV  − No-TLH Liquidation NAV

Does NOT touch comparative_analysis_results.csv or the main notebook.
Outputs  →  Backtest/tlh_no_tlh_comparison.csv
"""

from pathlib import Path
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
HERE    = Path(__file__).resolve().parent
CSV_IN  = HERE / "comparative_analysis_results.csv"
CSV_OUT = HERE / "tlh_no_tlh_comparison.csv"

# ── Load ──────────────────────────────────────────────────────────────────────
df = pd.read_csv(CSV_IN)

# Separate TLH on / off
off = df[df["TLH Status"] == "Off"].copy()
on  = df[df["TLH Status"] == "On (10%)"].copy()

# ── Merge on scenario keys ────────────────────────────────────────────────────
KEYS = ["Portfolio", "Market State", "Rebal Type", "Rebal Value"]

merged = off.merge(
    on,
    on=KEYS,
    suffixes=("_NoTLH", "_TLH"),
)

# ── Compute both tax alphas ───────────────────────────────────────────────────
merged["Pre_Liq_TaxAlpha"]  = merged["Final NAV_TLH"]       - merged["Final NAV_NoTLH"]
merged["Post_Liq_TaxAlpha"] = merged["Liquidation NAV_TLH"] - merged["Liquidation NAV_NoTLH"]

# ── Build clean output table ──────────────────────────────────────────────────
out = merged[KEYS + [
    "Strategy Label_NoTLH",
    "Final NAV_NoTLH",       "Liquidation NAV_NoTLH",
    "Final NAV_TLH",         "Liquidation NAV_TLH",
    "Pre_Liq_TaxAlpha",      "Post_Liq_TaxAlpha",
    "TLH Event Count_TLH",
    "Execution Costs_NoTLH", "Execution Costs_TLH",
    "Liquidation Tax_NoTLH", "Liquidation Tax_TLH",
]].copy()

out = out.rename(columns={
    "Strategy Label_NoTLH":   "Strategy",
    "Final NAV_NoTLH":        "NoTLH_Final_NAV",
    "Final NAV_TLH":          "TLH_Final_NAV",
    "Liquidation NAV_NoTLH":  "NoTLH_Liq_NAV",
    "Liquidation NAV_TLH":    "TLH_Liq_NAV",
    "TLH Event Count_TLH":    "TLH_Events",
    "Execution Costs_NoTLH":  "ExecCost_NoTLH",
    "Execution Costs_TLH":    "ExecCost_TLH",
    "Liquidation Tax_NoTLH":  "LiqTax_NoTLH",
    "Liquidation Tax_TLH":    "LiqTax_TLH",
})

out = out.sort_values(["Market State", "Portfolio", "Rebal Type", "Rebal Value"])
out.to_csv(CSV_OUT, index=False)
print(f"Saved {len(out)} rows → {CSV_OUT.name}")

# ── Pretty print summary ──────────────────────────────────────────────────────
print()
print("=" * 80)
print("  PRE-LIQUIDATION vs POST-LIQUIDATION TAX ALPHA SUMMARY")
print("=" * 80)

for market in out["Market State"].unique():
    print(f"\n── {market} ──")
    sub = out[out["Market State"] == market][[
        "Portfolio", "Strategy",
        "Pre_Liq_TaxAlpha", "Post_Liq_TaxAlpha"
    ]].copy()
    sub["Pre_Liq_TaxAlpha"]  = sub["Pre_Liq_TaxAlpha"].map("${:+,.0f}".format)
    sub["Post_Liq_TaxAlpha"] = sub["Post_Liq_TaxAlpha"].map("${:+,.0f}".format)
    sub.columns = ["Portfolio", "Strategy", "Pre-Liq Tax Alpha", "Post-Liq Tax Alpha"]
    print(sub.to_string(index=False))

print()
print("Done.")
