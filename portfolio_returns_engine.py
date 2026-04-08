#!/usr/bin/env python3
"""
Portfolio Returns Calculation Engine — Streamlit App
====================================================
Run:  streamlit run portfolio_returns_engine.py

Dependencies: streamlit, pandas, numpy, gdown, scipy

CHANGELOG:
  - Original: buy-and-hold engine with daily series
  - V2: Added daily rebalancing strategy
  - V3: Unified rebalancing engine: Daily / Weekly / Monthly / Quarterly
  - V4 (CURRENT):
      * Threshold (drift-band) rebalancing with absolute/relative drift modes
      * Per-asset tolerance bands with advanced per-ticker overrides
      * Full / Partial rebalance action modes
      * Calendar + Threshold combination with event logging
      * Cooldown option for threshold triggers
      * Enhanced metrics: Skewness, Kurtosis, Avg Drawdown, Tracking Error, Info Ratio
      * Drift diagnostics section with per-ticker histograms
      * Universal page-level tax parameters
      * Internal event log DataFrame for future CSV export
"""

import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from typing import List, Dict, Tuple, Any, Optional, Set
import warnings

# ── Engine imports (pure Python, no Streamlit dependency) ─────────────────────
from engine import (
    validate_weights,
    prepare_price_data,
    get_ticker_prices,
    calculate_portfolio_returns,
    build_daily_series,
    build_prices_wide,
    _get_rebalance_dates,
    build_rebalanced_series,
    compute_weights,
    compute_drift,
    find_threshold_triggers,
    apply_rebalance_full,
    apply_rebalance_partial,
    build_threshold_rebalanced_series,
    compute_strategy_metrics,
)

#MSBA v1 tax-aware simulation section.
try:
    from optimizer_msba_v1_engine import run_optimizer_simulation
    _OPTIMIZER_AVAILABLE = True
except ImportError:
    _OPTIMIZER_AVAILABLE = False

st.set_page_config(
    page_title="Portfolio Returns Calculator",
    page_icon="\U0001f4ca",
    layout="wide",
)

# app falls back to default Streamlit styling.
try:
    from ui_style import inject_site_css, render_hero
    inject_site_css()
    _STYLE_LOADED = True
except ImportError:
    _STYLE_LOADED = False

import gdown
import io
from pathlib import Path


# ================================================================
#  EXCEL EXPORT HELPERS
# ================================================================

def to_excel_bytes(dfs: Dict[str, pd.DataFrame]) -> bytes:
    """
    Serialize one or more DataFrames to an Excel workbook in memory.
    Keys in dfs become sheet names. Returns raw bytes for st.download_button.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet, df in dfs.items():
            df.to_excel(writer, sheet_name=sheet[:31], index=False)
    return buf.getvalue()


def excel_download_button(df: pd.DataFrame, filename: str, label: str = "Download Excel",
                          sheet_name: str = "Data", extra_sheets: Optional[Dict[str, pd.DataFrame]] = None):
    """Render a Streamlit download button for an Excel file."""
    sheets = {sheet_name: df}
    if extra_sheets:
        sheets.update(extra_sheets)
    st.download_button(
        label=f"⬇ {label}",
        data=to_excel_bytes(sheets),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# Price data is cached to /tmp so Streamlit Cloud doesn't re-download on every
# rerun. The parquet format keeps load times fast
DATA_PATH = Path("/tmp/price_data.parquet")
FILE_ID = "1pMQ817V05j4RK0vqJcVkBMmOBK5zRrug"
GDRIVE_URL = f"https://drive.google.com/uc?id={FILE_ID}"


# @st.cache_data ensures this only runs once per Streamlit session. The
# download is skipped entirely if the file already exists on disk from a
# previous run. On Streamlit Cloud, /tmp persists across reruns within the
# same container but is wiped on cold restarts.
@st.cache_data(show_spinner=True)
def ensure_data():
    if not DATA_PATH.exists():
        out = gdown.download(GDRIVE_URL, str(DATA_PATH), quiet=False, fuzzy=True)
        if out is None or not DATA_PATH.exists():
            st.error("Google Drive download failed (permissions/quota/bad link).")
            st.stop()


ensure_data()



# All simulation, rebalancing, and metrics functions are imported from
# the engine/ package at the top of this file. See:
#   engine/core.py        — validate_weights, calculate_portfolio_returns, build_*
#   engine/rebalancing.py — build_rebalanced_series, build_threshold_rebalanced_series, etc.
#   engine/metrics.py     — compute_strategy_metrics

# Chart column-name sanitizer (Altair/Vega cannot parse &, $, (), :)
# ================================================================

def _safe_chart_cols(chart_df):
    """
    Streamlit's native charts use Vega-Lite under the hood, which chokes on
    special characters in column names. This sanitizer strips problematic chars
    so column names like "Buy & Hold" or "Gain ($)" render correctly.
    This was a recurring issue in earlier versions that caused blank charts.
    """
    out = chart_df.copy()
    out.columns = [
        c.replace(" ", "_")
         .replace("&", "and")
         .replace("(", "")
         .replace(")", "")
         .replace("$", "USD")
         .replace(":", "")
         .replace("/", "_")
        for c in out.columns
    ]
    return out


# ================================================================
#  LOAD DATA
# ================================================================

# Only request the columns we actually need — this significantly reduces memory
# and load time since the full parquet may have many more columns.
_REQUIRED_COLS = ["TRADINGITEMID", "TICKERSYMBOL", "PRICEDATE", "PRICECLOSE", "PRICEMID", "TRADINGITEMSTATUSID"]


@st.cache_data(show_spinner=True)
def load_data():
    ensure_data()
    if not DATA_PATH.exists():
        st.error(f"Dataset not found: {DATA_PATH}")
        st.stop()
    # Sanity check: a valid parquet file should be >10MB. Smaller means
    # the download was likely interrupted or the file is corrupt.
    if DATA_PATH.stat().st_size < 10 * 1024 * 1024:
        st.error("Downloaded file looks too small (likely corrupt). Reboot app to retry.")
        st.stop()
    try:
        df = pd.read_parquet(DATA_PATH, columns=_REQUIRED_COLS)
    except Exception as e:
        st.error(f"Failed to read parquet: {e}")
        st.stop()
    df = prepare_price_data(df, price_field="PRICECLOSE")
    return df


df = load_data()
available_tickers = sorted(df["TICKERSYMBOL"].astype(str).str.strip().str.upper().unique())


# ================================================================
#  LOAD PROXY LOOKUP TABLE
# ================================================================

@st.cache_data(show_spinner=False)
def load_proxy_lookup():
    """Load TLH proxy pairs from proxy_lookup.csv (automatic, no user input needed)."""
    try:
        proxy_path = Path(__file__).resolve().parent / "proxy_lookup.csv"
        if not proxy_path.exists():
            return None
        proxy_df = pd.read_csv(proxy_path)
        # Ensure required columns exist
        required = ["symbol", "lookup_symbol", "order"]
        if not all(col in proxy_df.columns for col in required):
            return None
        # Normalize to uppercase
        proxy_df["symbol"] = proxy_df["symbol"].astype(str).str.strip().str.upper()
        proxy_df["lookup_symbol"] = proxy_df["lookup_symbol"].astype(str).str.strip().str.upper()
        proxy_df["order"] = pd.to_numeric(proxy_df["order"], errors="coerce").astype("Int64")
        # Drop rows with missing critical fields
        proxy_df = proxy_df.dropna(subset=["symbol", "lookup_symbol", "order"])
        return proxy_df
    except Exception as e:
        st.warning(f"Could not load proxy_lookup.csv: {e}")
        return None


proxy_lookup_full = load_proxy_lookup()


# ================================================================
#  SIDEBAR -- USER INPUTS
# ================================================================

st.sidebar.title("\u2699\ufe0f Portfolio Settings")

st.sidebar.markdown("### Holdings")
st.sidebar.caption("Add tickers and their portfolio weights (must sum to ~1.0).")

num_holdings = st.sidebar.number_input(
    "Number of holdings", min_value=1, max_value=20, value=3, step=1
)

ticker_inputs = []
weight_inputs = []

# Sensible defaults: a classic 3-fund portfolio (US equity/bonds/growth)
defaults = [
    ("SPY", 0.50), ("AGG", 0.30), ("QQQ", 0.20),
    ("AAPL", 0.00), ("BND", 0.00),
]

for i in range(int(num_holdings)):
    cols = st.sidebar.columns([2, 1])
    default_tk = defaults[i][0] if i < len(defaults) else ""
    default_wt = defaults[i][1] if i < len(defaults) else 0.0
    default_idx = available_tickers.index(default_tk) if default_tk in available_tickers else 0
    tk = cols[0].selectbox(
        f"Ticker {i+1}", options=available_tickers,
        index=default_idx, key=f"tk_{i}",
    )
    wt = cols[1].number_input(
        f"Weight", min_value=0.0, max_value=1.0, value=default_wt,
        step=0.05, key=f"wt_{i}", format="%.2f",
    )
    ticker_inputs.append(tk)
    weight_inputs.append(wt)

st.sidebar.markdown("---")
st.sidebar.markdown("### Parameters")

date_cols = st.sidebar.columns(2)
df_dates = pd.to_datetime(df["PRICEDATE"], errors="coerce").dropna()
min_date = df_dates.min().date()
max_date = df_dates.max().date()
default_end = max_date
default_start = max(min_date, (max_date - timedelta(days=365)))

# TASK 2: Show the valid date range so users know what data is available.
# The date inputs already clamp to min_value/max_value, but surfacing the
# range explicitly prevents confusion when users type arbitrary dates.
st.sidebar.caption(
    f"📅 Data available: **{min_date}** → **{max_date}**"
)

start_date = date_cols[0].date_input("Start Date", value=default_start, min_value=min_date, max_value=max_date)
end_date = date_cols[1].date_input("End Date", value=default_end, min_value=min_date, max_value=max_date)

# TASK 2: Warn the user if the selected range is very short (< 30 calendar days)
# — CAGR annualizes aggressively on short windows and can be misleading.
if (end_date - start_date).days < 30:
    st.sidebar.warning(
        "⚠️ Date range is under 30 days. CAGR will be a highly extrapolated "
        "annualized estimate — interpret with caution."
    )
elif start_date >= end_date:
    st.sidebar.error("❌ Start date must be before end date.")

initial_capital = st.sidebar.number_input(
    "Initial Capital ($)", min_value=1_000, max_value=100_000_000,
    value=100_000, step=10_000, format="%d",
)
price_field = "PRICECLOSE"
allow_cash = st.sidebar.checkbox("Whole shares only (cash residual)", value=False)

# TASK 4: Rolling volatility window — user-selectable, used for the rolling
# vol chart beneath the main portfolio value chart (toggle to show/hide).
rolling_vol_window = st.sidebar.number_input(
    "Rolling Vol Window (days)", min_value=5, max_value=252, value=30, step=5,
    help=(
        "Number of trading days used to compute rolling annualized volatility. "
        "30 days = recent risk; 90 days = smoother, more stable estimate."
    ),
)

# ================================================================
# V4: Universal Page-Level Tax Parameters
# ================================================================
# These tax rates are shared across all strategy sections — the TLH engine
# uses them directly, and they're threaded through as placeholders
# for future tax-aware calendar/threshold integration.
st.sidebar.markdown("---")
st.sidebar.markdown("### \U0001f3db\ufe0f Tax Parameters")
st.sidebar.caption("Universal tax rates applied across all strategies.")

global_st_rate = st.sidebar.number_input(
    "Short-Term Tax Rate (%)", min_value=0.0, max_value=60.0,
    value=35.0, step=1.0, format="%.1f", key="global_st_rate"
) / 100.0
global_lt_rate = st.sidebar.number_input(
    "Long-Term Tax Rate (%)", min_value=0.0, max_value=40.0,
    value=20.0, step=1.0, format="%.1f", key="global_lt_rate"
) / 100.0
global_tax_rates = {"st_rate": global_st_rate, "lt_rate": global_lt_rate}

# ================================================================
# Rebalancing controls
# ================================================================
# The rebalancing sidebar uses a cascading enable/disable pattern:
# 1. "Enable Rebalancing Comparison" is the master switch — if off, no
#    rebalancing UI appears and all rebalancing flags are False.
# 2. Under that, calendar and threshold can be independently toggled.
# 3. Calendar frequency and "show all strategies" are disabled when
#    calendar rebalancing is off.
# 4. Threshold controls (drift mode, tolerance, cooldown) only appear
#    when threshold is enabled.
# This prevents invalid state combinations (e.g., threshold cooldown
# configured but threshold itself disabled).
st.sidebar.markdown("---")
st.sidebar.markdown("### Rebalancing")

enable_rebalancing = st.sidebar.checkbox("Enable Rebalancing Comparison", value=True)

if enable_rebalancing:
    enable_calendar_rebal = st.sidebar.checkbox("Enable Calendar Rebalancing", value=True)
else:
    enable_calendar_rebal = False

REBAL_FREQS = ["Daily", "Weekly", "Monthly", "Quarterly", "6 Month", "Annual", "2 Year", "5 Year"]
selected_freq = st.sidebar.selectbox(
    "Rebalance Frequency",
    options=REBAL_FREQS,
    index=2,
    disabled=not (enable_rebalancing and enable_calendar_rebal),
)

show_all_strategies = st.sidebar.checkbox(
    "Show all calendar strategies (slower)",
    value=False,
    disabled=not (enable_rebalancing and enable_calendar_rebal),
    help="Compute & compare Buy-and-Hold + all calendar rebalance frequencies at once.",
)

### THRESHOLD REBALANCE ADDITIONS -- Sidebar Controls ###
if enable_rebalancing:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### \U0001f4cf Threshold Rebalancing")
    enable_threshold_rebal = st.sidebar.checkbox(
        "Enable Threshold (Drift-Band) Rebalancing", value=False,
        help="Trigger rebalance when any asset drifts beyond its tolerance band."
    )
else:
    enable_threshold_rebal = False

if enable_threshold_rebal:
    drift_mode = st.sidebar.selectbox(
        "Drift Mode", ["Absolute", "Relative"],
        help=(
            "Absolute: |w − target| in percentage points. "
            "Relative: |log(w / target)| — symmetric log-ratio (a drift from 10%→5% "
            "has the same magnitude as 5%→10%). Both engines use this formula consistently."
        ),
    )
    rebalance_action = st.sidebar.selectbox(
        "Rebalance Action", ["Full", "Partial"],
        help="Full: rebalance ALL assets to target. Partial: only trade breached assets, scale others.",
    )
    default_tolerance_pct = st.sidebar.number_input(
        "Default Drift Tolerance (%)", min_value=0.5, max_value=50.0,
        value=5.0, step=0.5, format="%.1f", key="thresh_tol",
        help="Default tolerance for all assets. Override per-asset below.",
    )
    cooldown_days = st.sidebar.number_input(
        "Cooldown (trading days)", min_value=0, max_value=60, value=0, step=1,
        help="Suppress additional threshold triggers for N days after a threshold rebalance.",
    )
else:
    drift_mode = "Absolute"
    rebalance_action = "Full"
    default_tolerance_pct = 5.0
    cooldown_days = 0

# Per-asset tolerance overrides (sidebar, so they're captured at compute time)
if enable_threshold_rebal:
    with st.sidebar.expander("📐 Per-Asset Drift Tolerances"):
        st.caption("Override default tolerance per ticker. Re-run to apply.")
        _seen_tickers: set = set()
        for _tk_input in ticker_inputs:
            if _tk_input and _tk_input not in _seen_tickers:
                _seen_tickers.add(_tk_input)
                st.number_input(
                    f"{_tk_input} tolerance (%)", min_value=0.5, max_value=50.0,
                    value=default_tolerance_pct, step=0.5,
                    key=f"tol_sidebar_{_tk_input}", format="%.1f",
                )

# ================================================================
# TAX LOSS HARVESTING (TLH) SIDEBAR
# ================================================================
# The TLH sidebar only appears if the engine module imported successfully.
# When the TLH toggle is off, we still define default values for
# opt_tlh_threshold and opt_div_handling so the rest of the code doesn't
# need to check enable_optimizer before accessing these variables.
# Note: V4 removed the separate optimizer tax rate inputs — they now use
# the universal global_tax_rates defined above.
if _OPTIMIZER_AVAILABLE:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### \U0001f4b0 Tax Loss Harvesting (TLH)")
    enable_optimizer = st.sidebar.toggle("Enable Tax Loss Harvesting (TLH)", value=False)
    if enable_optimizer:
        opt_tlh_threshold = st.sidebar.number_input(
            "TLH Loss Threshold (%)", min_value=0.0, max_value=50.0,
            value=5.0, step=0.5, format="%.1f", key="opt_tlh",
            help="Harvest tax lots that are down by at least this %"
        ) / 100.0
        opt_div_handling = st.sidebar.selectbox(
            "Dividend Handling",
            ["Reinvest dividends", "Keep dividends as cash"],
            key="opt_div",
        )
        # ── Automatically build proxy DataFrame from proxy_lookup.csv ─────────────────
        _proxy_df_ui = None
        _tickers_without_proxies = []

        if proxy_lookup_full is not None and not proxy_lookup_full.empty:
            # Filter proxy_lookup to only the selected tickers
            _matching_proxies = proxy_lookup_full[
                proxy_lookup_full["symbol"].isin(ticker_inputs)
            ].copy()
            if not _matching_proxies.empty:
                # Include lookup_type (required by ProxyResolver in optimizer)
                _proxy_df_ui = _matching_proxies[["symbol", "lookup_type", "lookup_symbol", "order"]]

                # Display proxy info in sidebar
                st.sidebar.markdown("**TLH Proxy Tickers** *(auto-loaded from proxy_lookup.csv)*")
                _proxy_display = []
                for _ticker in ticker_inputs:
                    _ticker_upper = _ticker.upper()
                    _ticker_proxies = _matching_proxies[
                        _matching_proxies["symbol"] == _ticker_upper
                    ].sort_values("order")
                    if not _ticker_proxies.empty:
                        _proxy_list = [f"{row['lookup_symbol']} (#{int(row['order'])})"
                                       for _, row in _ticker_proxies.iterrows()]
                        _proxy_display.append(f"**{_ticker_upper}** → {', '.join(_proxy_list)}")
                    else:
                        _tickers_without_proxies.append(_ticker_upper)

                if _proxy_display:
                    st.sidebar.caption("\n".join(_proxy_display))

                # Show warning for tickers without proxies
                if _tickers_without_proxies:
                    st.sidebar.warning(
                        f"⚠️ **TLH disabled** for {', '.join(_tickers_without_proxies)} "
                        f"(no proxies configured). These tickers will not be tax-loss harvested "
                        f"to avoid 30-day cash drag."
                    )
            else:
                st.sidebar.caption("ℹ️ No proxies found for selected tickers.")
                _tickers_without_proxies = ticker_inputs
        else:
            st.sidebar.caption("ℹ️ Proxies not configured (proxy_lookup.csv missing or no matches).")
            _tickers_without_proxies = ticker_inputs if enable_optimizer else []

        _wash_sale_days_ui = st.sidebar.number_input(
            "Wash-sale window (days)", min_value=0, max_value=60, value=30, step=1, key="opt_wsd",
            help="Calendar days after a TLH loss sale during which buys of the original are redirected to a proxy.",
        )
        _tlh_mode_ui = st.sidebar.selectbox(
            "TLH Threshold Mode",
            ["explicit", "rule_of_thumb"], index=0, key="opt_tlh_mode",
            help="rule_of_thumb: 15% for Daily cadence, 10% otherwise. explicit: uses the threshold above.",
        )
    else:
        opt_tlh_threshold = 0.05
        opt_div_handling = "Reinvest dividends"
        _proxy_df_ui = None
        _wash_sale_days_ui = 30
        _tlh_mode_ui = "explicit"
else:
    enable_optimizer = False
    _proxy_df_ui = None
    _wash_sale_days_ui = 30
    _tlh_mode_ui = "explicit"

# ================================================================
# TRANSACTION COST SIDEBAR
# ================================================================
st.sidebar.markdown("---")
st.sidebar.markdown("### 💸 Transaction Cost Assumptions")
# TASK 3: Updated caption to be non-technical and clear
st.sidebar.caption(
    "These costs are subtracted every time assets are bought or sold. "
    "Lower costs = more of your money stays invested."
)

# TASK 3: Rewrote all three tooltips to be clear, non-technical, and example-driven.
_commission_bps = st.sidebar.number_input(
    "Commission (bps/trade)", min_value=0.0, max_value=50.0, value=5.0, step=0.5,
    help=(
        "The flat fee charged by a broker on each trade, measured in basis points (bps). "
        "1 bps = 0.01% of the trade value.\n\n"
        "Example: A 5 bps commission on a $10,000 trade costs $5.00. "
        "Most modern brokers charge 0–10 bps."
    ),
)
_slippage_bps = st.sidebar.number_input(
    "Slippage (bps)", min_value=0.0, max_value=50.0, value=5.0, step=0.5,
    help=(
        "The difference between the expected trade price and the actual price you get — "
        "large orders move the market slightly against you.\n\n"
        "Example: You want to buy at $100.00 but the order fills at $100.05. "
        "That 5-cent gap on a $10,000 trade = 5 bps = $5.00 lost."
    ),
)
_bid_ask_bps = st.sidebar.number_input(
    "Bid-Ask Spread (bps, one-way)", min_value=0.0, max_value=30.0, value=2.0, step=0.5,
    help=(
        "The gap between the price buyers pay (ask) and sellers receive (bid). "
        "Entering or exiting a position costs half this spread.\n\n"
        "Example: A stock quoted at Bid $99.98 / Ask $100.02 has a 4-bps spread. "
        "Buying then immediately selling costs ~2 bps each way = $2 per $10,000 traded."
    ),
)
_cost_config = {
    "commission_bps": _commission_bps,
    "slippage_bps": _slippage_bps,
    "bid_ask_bps": _bid_ask_bps,
}
_total_cost_rate = (_commission_bps + _slippage_bps + _bid_ask_bps) / 10_000.0

run_btn = st.sidebar.button("\U0001f680 Calculate Returns", use_container_width=True, type="primary")

# Compact fingerprint of the current sidebar state, used to detect stale results.
# Stored when results are computed; compared each rerun to show a warning when
# the user changes settings without pressing Calculate.
import hashlib as _hashlib, json as _json
try:
    _param_sig = _hashlib.md5(_json.dumps({
        "t": sorted(set(str(t) for t in ticker_inputs)),
        "w": [round(float(w), 4) for w in weight_inputs],
        "sd": str(start_date), "ed": str(end_date),
        "cap": int(initial_capital),
        "er": enable_rebalancing, "ec": enable_calendar_rebal,
        "sf": selected_freq, "et": enable_threshold_rebal,
        "eo": enable_optimizer,
    }, sort_keys=True).encode()).hexdigest()[:10]
except (TypeError, ValueError):
    # Sidebar inputs are mocks (test environment) — use a sentinel value.
    _param_sig = "test-env"


# ================================================================
#  MAIN PAGE
# ================================================================

if _STYLE_LOADED:
    render_hero(
        eyebrow="UTexas MSBA // VISE",
        title='\U0001f4ca Portfolio Returns<br><em>Calculator</em>',
        subtitle="Price-based returns engine with tax-aware optimizer.",
        formula='Portfolio Value &nbsp;=&nbsp; <span>(Shares \u00d7 Price)</span> &nbsp;+&nbsp; Cash',
    )
else:
    st.title("\U0001f4ca Portfolio Returns Calculator")
    st.caption("Price-based returns engine")

# ----------------------------------------------------------------
#  COMPUTE — runs only when the button is pressed.
#  All results are stored in st.session_state["_r"] so they survive
#  sidebar interactions without recomputing.
# ----------------------------------------------------------------
if run_btn:
    weight_sum = sum(weight_inputs)
    if weight_sum == 0:
        st.error("All weights are zero. Please assign weights to at least one ticker.")
        st.stop()

    try:
        _summary, _holdings = calculate_portfolio_returns(
            df=df, tickers=ticker_inputs, weights=weight_inputs,
            start_date=str(start_date), end_date=str(end_date),
            initial_capital=float(initial_capital), price_field=price_field,
            allow_cash_residual=allow_cash,
        )
    except ValueError as e:
        st.error(f"**Error:** {e}")
        st.stop()

    _daily = build_daily_series(df, _holdings, float(initial_capital), price_field)
    _tickers_used = _holdings["Ticker"].tolist()

    # ── Portfolio Summary Stats ────────────────────────────────────────────
    _bh_vals = _daily["Portfolio Value"].values
    _bh_dates = _daily.index  # DatetimeIndex used for date-aware CAGR (TASK 1)
    # TASK 1: Pass dates so CAGR uses actual calendar years, not 252-day approximation
    _bh_metrics = compute_strategy_metrics(_bh_vals, float(initial_capital), dates=_bh_dates)
    _n_days = len(_bh_vals)

    # TASK 7: Removed unused _bh_daily_rets (rolling vol is computed live in the
    # display section from _bh_vals, no need to pre-compute here).
    # Removed unused _total_dividends_bh placeholder (dividends extracted from
    # optimizer trades_df in the display section where the data is available).

    # TASK 7: "CAGR" is inherently annualized — dropping "(annualized)" suffix to
    # match the comparison table column header and avoid redundancy.
    # Units are explicit throughout: % for rates, $ for dollar values.
    _summary_stats_rows = [
        ("Period", f"{str(start_date)} → {str(end_date)}"),
        ("Years (actual)", f"{_bh_metrics['years_used']:.2f}"),
        ("Trading Days", f"{_n_days:,}"),
        ("Initial Capital", f"${float(initial_capital):,.0f}"),
        ("Final Value (B&H)", f"${_bh_vals[-1]:,.0f}"),
        ("Total Return", f"{_bh_metrics['total_return']:+.2%}"),
        ("CAGR", f"{_bh_metrics['cagr']:+.2%}"),
        # Ann. Vol: full-period; rolling vol shown as toggle chart below
        ("Ann. Volatility", f"{_bh_metrics['annualized_vol']:.2%}"),
        ("Sharpe Ratio (Rf=0)", f"{_bh_metrics['sharpe']:.3f}"),
        ("Max Drawdown", f"{_bh_metrics['max_drawdown']:.2%}"),
        ("Avg Drawdown", f"{_bh_metrics['avg_drawdown']:.2%}"),
        # Calmar = CAGR / |MaxDD|. Negative CAGR → negative Calmar (do not abs the whole ratio).
        ("Calmar Ratio", f"{_bh_metrics['calmar_ratio']:.3f}" if _bh_metrics['max_drawdown'] != 0 else "—"),
        ("Return Skewness", f"{_bh_metrics['skewness']:.3f}"),
        ("Excess Kurtosis", f"{_bh_metrics['kurtosis']:.3f}"),
    ]
    _summary_df = pd.DataFrame(_summary_stats_rows, columns=["Metric", "Value (Buy & Hold)"])

    # ── Allocation Drift ───────────────────────────────────────────────────
    _final_values = {row["Ticker"]: row["End Value"] for _, row in _holdings.iterrows()}
    _port_end_val = sum(_final_values.values())
    _drift_rows = []
    for _, row in _holdings.iterrows():
        tk = row["Ticker"]
        target_w = row["Weight"]
        end_val = row["End Value"]
        actual_w = end_val / _port_end_val if _port_end_val > 0 else 0.0
        drift_abs = actual_w - target_w
        _drift_rows.append({
            "Ticker": tk,
            "Target Weight": f"{target_w:.1%}",
            "Actual Weight (End)": f"{actual_w:.1%}",
            "Drift (pp)": f"{drift_abs:+.2%}",
            "Drift Direction": "Overweight" if drift_abs > 0.005 else ("Underweight" if drift_abs < -0.005 else "On Target"),
            "End Value ($)": f"${end_val:,.0f}",
        })
    _drift_df = pd.DataFrame(_drift_rows)

    # ── Rebalancing ────────────────────────────────────────────────────────
    _rebal: Dict[str, Any] = {"enabled": enable_rebalancing, "has_strategies": False}

    if enable_rebalancing:
        try:
            _all_start = pd.to_datetime(_holdings["Start Date"]).min()
            _all_end = pd.to_datetime(_holdings["End Date"]).max()
            _prices_wide = build_prices_wide(df, _tickers_used, _all_start, _all_end, price_field)
        except ValueError as e:
            st.error(f"**Error building price matrix:** {e}")
            st.stop()

        _target_weights = {row["Ticker"]: row["Weight"] for _, row in _holdings.iterrows()}
        _tolerances = {tk: default_tolerance_pct / 100.0 for tk in _tickers_used}
        for _tk in _tickers_used:
            _tol_key = f"tol_sidebar_{_tk}"
            if _tol_key in st.session_state:
                _tolerances[_tk] = float(st.session_state[_tol_key]) / 100.0

        _strategy_results: Dict = {}
        _event_logs: Dict = {}
        _drift_histories: Dict = {}

        if enable_calendar_rebal:
            _freqs_to_run = REBAL_FREQS if show_all_strategies else [selected_freq]
            for _freq in _freqs_to_run:
                try:
                    _rd, _rs = build_rebalanced_series(
                        _prices_wide, _target_weights, float(initial_capital), _freq,
                        cost_rate=_total_cost_rate,
                    )
                    _strategy_results[f"Rebal: {_freq}"] = (_rd, _rs)
                except ValueError as e:
                    st.warning(f"\u26a0\ufe0f Could not compute {_freq} rebalancing: {e}")
        else:
            _freqs_to_run = []

        if enable_threshold_rebal:
            try:
                _thresh_rd, _thresh_rs, _thresh_log, _thresh_drift = build_threshold_rebalanced_series(
                    prices_wide=_prices_wide, target_weights=_target_weights,
                    initial_capital=float(initial_capital), tolerances=_tolerances,
                    drift_mode=drift_mode, rebalance_action=rebalance_action,
                    cooldown_days=cooldown_days,
                    calendar_freq=selected_freq if enable_calendar_rebal else None,
                    enable_calendar=enable_calendar_rebal, enable_threshold=True,
                    whole_shares=allow_cash, cost_rate=_total_cost_rate,
                )
                _combo_label = "Threshold" if not enable_calendar_rebal else f"Cal({selected_freq})+Thresh"
                _strategy_results[_combo_label] = (_thresh_rd, _thresh_rs)
                _event_logs[_combo_label] = _thresh_log
                _drift_histories[_combo_label] = _thresh_drift
            except ValueError as e:
                st.warning(f"\u26a0\ufe0f Could not compute threshold rebalancing: {e}")

            # TASK 5: When calendar+threshold are both enabled, also run a
            # threshold-only strategy so it appears as a separate comparison row.
            # This lets users isolate the drift-band effect from the calendar effect.
            if enable_calendar_rebal:
                try:
                    _tonly_rd, _tonly_rs, _tonly_log, _tonly_drift = build_threshold_rebalanced_series(
                        prices_wide=_prices_wide, target_weights=_target_weights,
                        initial_capital=float(initial_capital), tolerances=_tolerances,
                        drift_mode=drift_mode, rebalance_action=rebalance_action,
                        cooldown_days=cooldown_days,
                        calendar_freq=None,
                        enable_calendar=False,  # threshold-only — no calendar component
                        enable_threshold=True,
                        whole_shares=allow_cash, cost_rate=_total_cost_rate,
                    )
                    _strategy_results["Threshold Only"] = (_tonly_rd, _tonly_rs)
                    _event_logs["Threshold Only"] = _tonly_log
                    _drift_histories["Threshold Only"] = _tonly_drift
                except ValueError as e:
                    st.warning(f"\u26a0\ufe0f Could not compute threshold-only strategy: {e}")

        if _strategy_results:
            _comparison_df = pd.DataFrame(index=_prices_wide.index)
            _comparison_df.index.name = "PRICEDATE"
            _comparison_df["Buy & Hold"] = _daily["Portfolio Value"].reindex(_comparison_df.index)
            for _lbl, (_rd2, _rs2) in _strategy_results.items():
                _comparison_df[_lbl] = _rd2["Portfolio Value"].reindex(_comparison_df.index)
            _comparison_df = _comparison_df.dropna()

            _bh_vals_arr = _comparison_df["Buy & Hold"].values
            _comp_dates = _comparison_df.index  # DatetimeIndex for date-aware CAGR
            # TASK 1: Pass dates for accurate calendar-based CAGR
            _bh_m2 = compute_strategy_metrics(_bh_vals_arr, float(initial_capital), benchmark_values=None, dates=_comp_dates)
            _bh_m2.update({"rebalance_count": 0, "turnover_proxy": 0.0})

            _metrics_raw: Dict = {
                "Buy & Hold": {**_bh_m2, "turnover_dollars": 0.0, "est_transaction_cost": 0.0}
            }
            _metrics_rows = [{
                "Strategy": "Buy & Hold",
                "Final Value ($)": f"${_bh_vals_arr[-1]:,.0f}",
                "Total Return": f"{_bh_m2['total_return']:+.2%}",
                "CAGR": f"{_bh_m2['cagr']:+.2%}",
                "Ann. Vol": f"{_bh_m2['annualized_vol']:.2%}",
                "Sharpe": f"{_bh_m2['sharpe']:.3f}",
                "Max DD": f"{_bh_m2['max_drawdown']:.2%}",
                "Avg DD": f"{_bh_m2['avg_drawdown']:.2%}",
                "Skew": f"{_bh_m2['skewness']:.3f}",
                "Kurt": f"{_bh_m2['kurtosis']:.3f}",
                # TASK 7: TE shown as % for consistency with all other rate columns
                "TE (ann.)": "—", "IR": "—",
                "Turnover": "0.00×", "Rebal Events": 0,
                "Turnover ($)": "$0", "Est. Cost ($)": "$0",
            }]

            for _lbl in _strategy_results:
                _rd3, _rs3 = _strategy_results[_lbl]
                _vals3 = _comparison_df[_lbl].values
                # TASK 1: Pass dates to all strategy metric computations
                _m3 = compute_strategy_metrics(_vals3, float(initial_capital), benchmark_values=_bh_vals_arr, dates=_comp_dates)
                _td3 = _rs3.get("total_turnover_dollars", 0.0)
                _ec3 = _td3 * _total_cost_rate
                _metrics_raw[_lbl] = {
                    **_m3,
                    "rebalance_count": _rs3["rebalance_count"],
                    "turnover_proxy": _rs3["turnover_proxy"],
                    "turnover_dollars": _td3,
                    "est_transaction_cost": _ec3,
                }
                _metrics_rows.append({
                    "Strategy": _lbl,
                    "Final Value ($)": f"${_rs3['final_value']:,.0f}",
                    "Total Return": f"{_m3['total_return']:+.2%}",
                    "CAGR": f"{_m3['cagr']:+.2%}",
                    "Ann. Vol": f"{_m3['annualized_vol']:.2%}",
                    "Sharpe": f"{_m3['sharpe']:.3f}",
                    "Max DD": f"{_m3['max_drawdown']:.2%}",
                    "Avg DD": f"{_m3['avg_drawdown']:.2%}",
                    "Skew": f"{_m3['skewness']:.3f}",
                    "Kurt": f"{_m3['kurtosis']:.3f}",
                    # TASK 7: TE was shown as raw decimal (0.1234); now % like all other rates
                    "TE (ann.)": f"{_m3['tracking_error']:.2%}",
                    "IR": f"{_m3['information_ratio']:.3f}",
                    "Turnover": f"{_rs3['turnover_proxy']:.2f}×",
                    "Rebal Events": _rs3["rebalance_count"],
                    "Turnover ($)": f"${_td3:,.0f}",
                    "Est. Cost ($)": f"${_ec3:,.0f}",
                })
            _metrics_df = pd.DataFrame(_metrics_rows)

            # Strategy ranking
            _rank_labels = list(_metrics_raw.keys())
            _rank_dims = {
                "CAGR (↑)": ("cagr", True), "Sharpe (↑)": ("sharpe", True),
                "Max DD (↓)": ("max_drawdown", False), "IR vs B&H (↑)": ("information_ratio", True),
                "Turnover (↓)": ("turnover_proxy", False), "Est. Cost $ (↓)": ("est_transaction_cost", False),
            }
            _rank_data: Dict = {lbl: {} for lbl in _rank_labels}
            for _dim_name, (_dim_key, _hib) in _rank_dims.items():
                _vfd = [(lbl, _metrics_raw[lbl].get(_dim_key, 0.0)) for lbl in _rank_labels]
                _vfd.sort(key=lambda x: x[1], reverse=_hib)
                for _rank, (lbl, _) in enumerate(_vfd, 1):
                    _rank_data[lbl][_dim_name] = _rank
            for lbl in _rank_labels:
                _rank_data[lbl]["Composite Score"] = sum(_rank_data[lbl].values())
            _rank_rows = []
            for lbl in sorted(_rank_labels, key=lambda x: _rank_data[x]["Composite Score"]):
                _rank_rows.append({"Strategy": lbl, **_rank_data[lbl]})
            _rank_df = pd.DataFrame(_rank_rows)

            # Cost breakdown
            _cost_rows = []
            for lbl in _rank_labels:
                _mr = _metrics_raw[lbl]
                _cost_rows.append({
                    "Strategy": lbl,
                    "Rebal Events": int(_mr["rebalance_count"]),
                    "Turnover ($)": f"${_mr['turnover_dollars']:,.0f}",
                    "Turnover Ratio": f"{_mr['turnover_proxy']:.2f}×",
                    "Est. Commission ($)": f"${_mr['turnover_dollars'] * _commission_bps / 10000:,.0f}",
                    "Est. Slippage ($)": f"${_mr['turnover_dollars'] * _slippage_bps / 10000:,.0f}",
                    "Est. Bid-Ask ($)": f"${_mr['turnover_dollars'] * _bid_ask_bps / 10000:,.0f}",
                    "Total Est. Cost ($)": f"${_mr['est_transaction_cost']:,.0f}",
                    "Cost as % of Capital": f"{_mr['est_transaction_cost'] / float(initial_capital):.3%}",
                })
            _cost_df = pd.DataFrame(_cost_rows)

            # Drawdown summary
            _dd_rows = []
            for _col in _comparison_df.columns:
                _vals_dd = _comparison_df[_col].values
                _dates_dd = _comparison_df.index
                _rm_dd = np.maximum.accumulate(_vals_dd)
                _dd = (_vals_dd - _rm_dd) / _rm_dd
                _max_dd_idx = int(np.argmin(_dd))
                _peak_idx = int(np.argmax(_vals_dd[:_max_dd_idx + 1]))
                _peak_val = _vals_dd[_peak_idx]
                _recovery_idx = None
                for j in range(_max_dd_idx, len(_vals_dd)):
                    if _vals_dd[j] >= _peak_val:
                        _recovery_idx = j
                        break
                _dd_duration = _max_dd_idx - _peak_idx
                _recovery_duration = (_recovery_idx - _max_dd_idx) if _recovery_idx is not None else None
                _dd_rows.append({
                    "Strategy": _col,
                    "Max Drawdown": f"{_dd[_max_dd_idx]:.2%}",
                    "Avg Drawdown": f"{np.mean(_dd):.2%}",
                    "Peak Date": _dates_dd[_peak_idx].strftime("%Y-%m-%d"),
                    "Trough Date": _dates_dd[_max_dd_idx].strftime("%Y-%m-%d"),
                    "Recovery Date": _dates_dd[_recovery_idx].strftime("%Y-%m-%d") if _recovery_idx is not None else "Not recovered",
                    "Days to Trough": _dd_duration,
                    "Days to Recover": _recovery_duration if _recovery_duration is not None else "—",
                })
            _dd_df = pd.DataFrame(_dd_rows)

            # Color map
            _freq_color_map_c = {"Daily": "#e8710a", "Weekly": "#34a853", "Monthly": "#9c27b0", "Quarterly": "#ea4335"}
            _all_strat_labels = list(_comparison_df.columns)
            _color_map: Dict = {"Buy & Hold": "#1a73e8"}
            for lbl in _strategy_results:
                if lbl.startswith("Rebal:"):
                    _color_map[lbl] = _freq_color_map_c.get(lbl.replace("Rebal: ", ""), "#666666")
                else:
                    _color_map[lbl] = "#ffab00"

            _primary_label = list(_strategy_results.keys())[0]
            _, _primary_stats = _strategy_results[_primary_label]

            # Event log summary
            _elog_summary_rows: list = []
            _elog_sheets: Dict = {}
            for lbl, _log_df in _event_logs.items():
                if not _log_df.empty and "turnover_dollars" in _log_df.columns:
                    _tot_to = _log_df["turnover_dollars"].sum()
                    _avg_to = _log_df["turnover_dollars"].mean()
                    _elog_summary_rows.append({
                        "Strategy": lbl, "Total Events": len(_log_df),
                        "Total Turnover ($)": f"${_tot_to:,.0f}",
                        "Avg Turnover/Event ($)": f"${_avg_to:,.0f}",
                        "Threshold Events": int((_log_df["reason"] == "threshold").sum()) if "reason" in _log_df.columns else "—",
                        "Calendar Events": int((_log_df["reason"] == "calendar").sum()) if "reason" in _log_df.columns else "—",
                    })
                else:
                    _elog_summary_rows.append({
                        "Strategy": lbl, "Total Events": 0,
                        "Total Turnover ($)": "$0", "Avg Turnover/Event ($)": "$0",
                        "Threshold Events": 0, "Calendar Events": 0,
                    })
                _elog_sheets[lbl[:28]] = _log_df
            _elog_sum_df = pd.DataFrame(_elog_summary_rows) if _elog_summary_rows else pd.DataFrame()

            _rebal = {
                "enabled": True, "has_strategies": True,
                "prices_wide": _prices_wide, "tolerances": _tolerances,
                "drift_mode": drift_mode,
                "strategy_results": _strategy_results,
                "event_logs": _event_logs, "drift_histories": _drift_histories,
                "comparison_df": _comparison_df,
                "bh_vals_arr": _bh_vals_arr, "bh_m2": _bh_m2,
                "metrics_raw": _metrics_raw, "metrics_df": _metrics_df,
                "rank_df": _rank_df, "cost_df": _cost_df, "dd_df": _dd_df,
                "color_map": _color_map, "all_strat_labels": _all_strat_labels,
                "primary_label": _primary_label, "primary_stats": _primary_stats,
                "elog_sum_df": _elog_sum_df, "elog_sheets": _elog_sheets,
                "enable_calendar_rebal": enable_calendar_rebal,
                "enable_threshold_rebal": enable_threshold_rebal,
                "selected_freq": selected_freq,
            }
        else:
            _rebal = {"enabled": True, "has_strategies": False}

    # ── Optimizer ─────────────────────────────────────────────────────────
    _opt: Dict = {}
    if enable_optimizer:
        _div_df_opt = None
        try:
            import os as _os
            _div_path = _os.path.join(_os.path.dirname(__file__), "dividend_data.csv")
            if _os.path.exists(_div_path):
                _div_df_opt = pd.read_csv(_div_path)
                _div_df_opt["PAYDATE"] = pd.to_datetime(_div_df_opt["PAYDATE"], errors="coerce")
                _div_df_opt["EXDATE"] = pd.to_datetime(_div_df_opt["EXDATE"], errors="coerce")
                if "TICKERSYMBOL" not in _div_df_opt.columns:
                    if "TRADINGITEMID" in _div_df_opt.columns and "TRADINGITEMID" in df.columns:
                        _tkmap = (
                            df[["TRADINGITEMID", "TICKERSYMBOL"]].drop_duplicates()
                            .set_index("TRADINGITEMID")["TICKERSYMBOL"].to_dict()
                        )
                        _div_df_opt["TICKERSYMBOL"] = _div_df_opt["TRADINGITEMID"].map(_tkmap)
                        _div_df_opt = _div_df_opt.dropna(subset=["TICKERSYMBOL"])
        except Exception as _div_err:
            _div_df_opt = None
            st.warning(
                f"⚠️ Dividend data could not be loaded ({_div_err}). "
                "Dividends will not be modeled in this run. "
                "Ensure dividend_data.csv exists in the same directory as the app."
            )

        _opt_tax_rates = global_tax_rates
        _opt_reinvest = opt_div_handling == "Reinvest dividends"
        _opt_tickers_list = _holdings["Ticker"].tolist()
        _opt_weights_list = _holdings["Weight"].tolist()
        _opt_rebal_freq = selected_freq if enable_calendar_rebal else "None"

        _static_result = None
        _opt_result_full = None

        with st.spinner("Running TLH simulation..."):
            try:
                _static_result = run_optimizer_simulation(
                    prices_df=df, dividends_df=_div_df_opt,
                    tickers=_opt_tickers_list, weights=_opt_weights_list,
                    start_date=str(start_date), end_date=str(end_date),
                    rebalance_frequency=_opt_rebal_freq,
                    tax_rates=_opt_tax_rates, tlh_threshold=opt_tlh_threshold,
                    reinvest_dividends=_opt_reinvest,
                    initial_capital=float(initial_capital),
                    price_field=price_field, static=True, cost_config=_cost_config,
                    proxy_df=_proxy_df_ui,
                    wash_sale_days=int(_wash_sale_days_ui),
                    tlh_threshold_mode=_tlh_mode_ui,
                    compute_tax_alpha=True,
                )
            except Exception as e:
                st.error(f"TLH simulation failed: {e}")

        with st.spinner("Running Rebalanced + TLH simulation..."):
            try:
                _opt_result_full = run_optimizer_simulation(
                    prices_df=df, dividends_df=_div_df_opt,
                    tickers=_opt_tickers_list, weights=_opt_weights_list,
                    start_date=str(start_date), end_date=str(end_date),
                    rebalance_frequency=_opt_rebal_freq,
                    tax_rates=_opt_tax_rates, tlh_threshold=opt_tlh_threshold,
                    reinvest_dividends=_opt_reinvest,
                    initial_capital=float(initial_capital),
                    price_field=price_field, static=False, cost_config=_cost_config,
                    proxy_df=_proxy_df_ui,
                    wash_sale_days=int(_wash_sale_days_ui),
                    tlh_threshold_mode=_tlh_mode_ui,
                    compute_tax_alpha=True,
                )
            except Exception as e:
                st.error(f"Rebalanced + TLH simulation failed: {e}")

        if _static_result and _opt_result_full:
            _tlh_rows_opt = []
            for _lbl_o, _res_o in [("TLH Only", _static_result), ("Rebalanced + TLH", _opt_result_full)]:
                _rdf_tmp = _res_o.get("realized_df", pd.DataFrame())
                _losses_h = _res_o.get("losses_harvested", 0.0)
                _tlh_ev = 0
                if not _rdf_tmp.empty and "gain_loss" in _rdf_tmp.columns:
                    _tlh_ev = int((_rdf_tmp["gain_loss"] < 0).sum())
                # Legacy estimate retained for reference; engine now also returns tax alpha series.
                _tax_saved_o = _losses_h * _opt_tax_rates.get("st_rate", 0.35)
                _tx_cost_o = _res_o.get("transaction_costs_total", 0.0)
                _net_ben = _tax_saved_o - _tx_cost_o - _res_o.get("tax_paid_total", 0.0)
                _ta2 = _res_o.get("tax_alpha_2_final", np.nan)
                _ta2_pct = (_ta2 / float(initial_capital)) if (isinstance(_ta2, (int, float)) and float(initial_capital) > 0) else np.nan
                _oi_used = _res_o.get("ordinary_income_offset_used_ytd_final", np.nan)
                _cf_st = _res_o.get("loss_carryforward_st", np.nan)
                _cf_lt = _res_o.get("loss_carryforward_lt", np.nan)
                _tlh_rows_opt.append({
                    "Scenario": _lbl_o,
                    "TLH Events (loss lots)": _tlh_ev,
                    "Total Losses Harvested ($)": f"${_losses_h:,.0f}",
                    "Est. Tax Savings ($)": f"${_tax_saved_o:,.0f}",
                    "Tax Paid ($)": f"${_res_o.get('tax_paid_total', 0.0):,.0f}",
                    "Exec Costs ($)": f"${_tx_cost_o:,.0f}",
                    "Net Benefit ($)": f"${_net_ben:+,.0f}",
                    "Tax Alpha 2 ($)": f"${_ta2:+,.0f}" if np.isfinite(_ta2) else "—",
                    "Tax Alpha 2 (% cap)": f"{_ta2_pct:+.2%}" if np.isfinite(_ta2_pct) else "—",
                    "Ordinary Offset Used (YTD, $<=3000)": f"${_oi_used:,.0f}" if np.isfinite(_oi_used) else "—",
                    "Loss CF (ST)": f"${_cf_st:,.0f}" if np.isfinite(_cf_st) else "—",
                    "Loss CF (LT)": f"${_cf_lt:,.0f}" if np.isfinite(_cf_lt) else "—",
                    "Final NAV ($)": f"${_res_o['nav_series'].iloc[-1]:,.0f}",
                })
            _tlh_df_opt = pd.DataFrame(_tlh_rows_opt)
            _opt = {
                "static_result": _static_result,
                "opt_result": _opt_result_full,
                "tlh_df": _tlh_df_opt,
                "tax_rates": _opt_tax_rates,
            }

    # ── Holdings display formatting ────────────────────────────────────────
    _display_df = _holdings.copy()
    _display_df["Weight"] = _display_df["Weight"].apply(lambda x: f"{x:.1%}")
    _display_df["Return"] = _display_df["Return"].apply(lambda x: f"{x:+.2%}")
    _display_df["Gain (%)"] = _display_df["Gain (%)"].apply(lambda x: f"{x:+.2%}")
    _display_df["Gain ($)"] = _display_df["Gain ($)"].apply(lambda x: f"${x:+,.2f}")
    _display_df["Start Value"] = _display_df["Start Value"].apply(lambda x: f"${x:,.2f}")
    _display_df["End Value"] = _display_df["End Value"].apply(lambda x: f"${x:,.2f}")
    _display_df["Start Price"] = _display_df["Start Price"].apply(lambda x: f"${x:.2f}")
    _display_df["End Price"] = _display_df["End Price"].apply(lambda x: f"${x:.2f}")

    # ── Build export tables dict for Download All ──────────────────────────
    _export: Dict[str, pd.DataFrame] = {
        "Portfolio Summary": _summary_df,
        "Allocation Drift": _drift_df,
        "Holdings Detail": _holdings,
    }
    if _rebal.get("has_strategies"):
        _export["Strategy Metrics"] = _rebal["metrics_df"]
        _export["Strategy Ranking"] = _rebal["rank_df"]
        _export["Transaction Costs"] = _rebal["cost_df"]
        _export["Drawdown Summary"] = _rebal["dd_df"]
        if not _rebal["elog_sum_df"].empty:
            _export["Rebal Event Summary"] = _rebal["elog_sum_df"]
    if _opt.get("opt_result"):
        _export["TLH Tax Summary"] = _opt["tlh_df"]
        _rdf_exp = _opt["opt_result"].get("realized_df", pd.DataFrame())
        _tdf_exp = _opt["opt_result"].get("trades_df", pd.DataFrame())
        if not _rdf_exp.empty:
            _export["Realized Gain/Loss"] = _rdf_exp
        if not _tdf_exp.empty:
            _export["Trade Log"] = _tdf_exp

    # ── Persist to session_state ───────────────────────────────────────────
    st.session_state["_r_sig"] = _param_sig  # mark results as current
    st.session_state["_r"] = {
        "summary": _summary, "holdings": _holdings, "display_df": _display_df,
        "daily": _daily, "tickers_used": _tickers_used,
        "bh_vals": _bh_vals, "bh_metrics": _bh_metrics, "n_days": _n_days,
        "summary_df": _summary_df, "drift_df": _drift_df,
        "rebal": _rebal, "opt": _opt, "export": _export,
        # TASK 4: store rolling vol window so the display section can use it
        "rolling_vol_window": rolling_vol_window,
        "params": {
            "start_date": str(start_date), "end_date": str(end_date),
            "initial_capital": float(initial_capital),
            "global_st_rate": global_st_rate, "global_lt_rate": global_lt_rate,
            "global_tax_rates": global_tax_rates,
            "commission_bps": _commission_bps, "slippage_bps": _slippage_bps,
            "bid_ask_bps": _bid_ask_bps, "total_cost_rate": _total_cost_rate,
            "run_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

# ----------------------------------------------------------------
#  GUARD — show placeholder until first run
# ----------------------------------------------------------------
if "_r" not in st.session_state:
    st.info("\U0001f448 Configure your portfolio in the sidebar and press **Calculate Returns**.")
    st.stop()

# ----------------------------------------------------------------
#  UNPACK from session_state
# ----------------------------------------------------------------
_r = st.session_state["_r"]
summary = _r["summary"]
holdings = _r["holdings"]
display_df = _r["display_df"]
daily = _r["daily"]
tickers_used = _r["tickers_used"]
_bh_vals = _r["bh_vals"]
_bh_metrics = _r["bh_metrics"]
_n_days = _r["n_days"]
_summary_df = _r["summary_df"]
_drift_df = _r["drift_df"]
rebal = _r["rebal"]
opt = _r["opt"]
export = _r["export"]
p = _r["params"]

# Stale-results banner: show a warning if sidebar settings have changed since
# the last calculation. The signature is a compact hash of all key sidebar inputs.
if st.session_state.get("_r_sig") != _param_sig:
    st.warning(
        "⚠️ **Settings have changed.** Results shown below are from a previous run "
        "and may not reflect current sidebar inputs. "
        "Press **Calculate Returns** to update."
    )

# Timestamp badge so users know when results were last computed
st.caption(
    f"\U0001f4ca Results from **{p.get('run_timestamp', 'last run')}**. "
    "Press **Calculate Returns** in the sidebar to recompute with current settings."
)

# ── Dropped ticker warnings ────────────────────────────────────────────────
if summary["tickers_dropped"] > 0:
    for tk, w, reason in summary["dropped_details"]:
        st.warning(f"\u26a0\ufe0f Dropped **{tk}** (weight {w:.2%}): {reason}")

# ── KPI Cards (Buy-and-Hold) ───────────────────────────────────────────────
total_return = summary["portfolio_total_return"]
gain_dollars = summary["total_unrealized_gain_dollars"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Starting Value", f"${summary['portfolio_start_value']:,.0f}")
col2.metric("Ending Value", f"${summary['portfolio_end_value']:,.0f}")
col3.metric("Total Return", f"{total_return:+.2%}", delta=f"${gain_dollars:+,.0f}")
col4.metric("Unrealized Gain", f"${gain_dollars:+,.0f}", delta=f"{summary['total_unrealized_gain_pct']:+.2%}")

st.markdown("---")

st.subheader("Portfolio Value vs Cost Basis")
st.line_chart(_safe_chart_cols(daily[["Portfolio Value", "Cost Basis"]]), color=["#1a73e8", "#888888"], use_container_width=True, height=380)

# ── TASK 4: Rolling Volatility Chart ─────────────────────────────────────────
# Annualized rolling volatility = rolling std of daily returns × √252.
# Displayed as a toggleable chart so it doesn't clutter the default view.
# The window comes from the sidebar control (default 30 trading days).
show_rolling_vol = st.checkbox("Show Rolling Volatility Chart", value=False, key="show_rvol")
if show_rolling_vol:
    _rvol_window = _r.get("rolling_vol_window", 30)
    _bh_daily_rets_disp = pd.Series(
        np.diff(_bh_vals) / np.where(_bh_vals[:-1] > 0, _bh_vals[:-1], 1.0),
        index=daily.index[1:],
    )
    # rolling(window).std(ddof=1) × √252 → annualized vol at each day
    _rvol_series = _bh_daily_rets_disp.rolling(window=_rvol_window, min_periods=max(2, _rvol_window // 2)).std(ddof=1) * np.sqrt(252)
    _rvol_df = pd.DataFrame(
        {f"Rolling Vol ({_rvol_window}d, annualized)": _rvol_series * 100},
        index=_rvol_series.index,
    )
    _rvol_df.index.name = "PRICEDATE"
    st.markdown(f"#### Rolling {_rvol_window}-Day Annualized Volatility (%)")
    st.line_chart(_safe_chart_cols(_rvol_df), color=["#e8710a"], use_container_width=True, height=280)
    st.caption(
        f"Rolling {_rvol_window}-day annualized volatility (Buy & Hold). "
        f"Full-period vol: **{_bh_metrics['annualized_vol']:.2%}**. "
        "Adjust window in sidebar Parameters."
    )

st.subheader("Per-Ticker Cumulative Return (%)")
return_cols = [f"{tk} Return (%)" for tk in tickers_used if f"{tk} Return (%)" in daily.columns]
st.line_chart(_safe_chart_cols(daily[return_cols]), use_container_width=True, height=320)

# ── Portfolio Summary Statistics Table ─────────────────────────────────────
st.markdown("---")
st.subheader("Portfolio Summary Statistics")
_s_col1, _s_col2 = st.columns([2, 1])
with _s_col1:
    st.dataframe(_summary_df, use_container_width=True, hide_index=True)
with _s_col2:
    excel_download_button(
        _summary_df, "portfolio_summary_stats.xlsx",
        label="Portfolio Summary Stats", sheet_name="Summary Stats",
    )

st.markdown("#### Allocation Drift (End of Period)")
st.caption("Difference between target weights and actual weights at end of period.")
_d_col1, _d_col2 = st.columns([3, 1])
with _d_col1:
    st.dataframe(_drift_df, use_container_width=True, hide_index=True)
with _d_col2:
    excel_download_button(
        _drift_df, "allocation_drift.xlsx",
        label="Allocation Drift", sheet_name="Drift",
    )

st.markdown("---")


# ================================================================
# REBALANCING COMPARISON SECTION (read from session_state)
# ================================================================

if rebal["enabled"]:
    st.subheader("\U0001f504 Rebalancing Strategy Comparison")
    if not rebal.get("has_strategies", False):
        st.info("Enable at least one rebalancing strategy to see comparison results.")
    else:

        # ── Unpack rebalancing results from session_state ─────────────────
        comparison_df = rebal["comparison_df"]
        metrics_df = rebal["metrics_df"]
        _rank_df = rebal["rank_df"]
        _cost_df = rebal["cost_df"]
        _dd_df = rebal["dd_df"]
        _metrics_raw = rebal["metrics_raw"]
        strategy_results = rebal["strategy_results"]
        event_logs = rebal["event_logs"]
        drift_histories = rebal["drift_histories"]
        _color_map = rebal["color_map"]
        _all_strat_labels = rebal["all_strat_labels"]
        primary_label = rebal["primary_label"]
        primary_stats = rebal["primary_stats"]
        _elog_sum_df = rebal["elog_sum_df"]
        _elog_sheets = rebal["elog_sheets"]
        _commission_bps_d = p["commission_bps"]
        _slippage_bps_d = p["slippage_bps"]
        _bid_ask_bps_d = p["bid_ask_bps"]
        _total_cost_rate_d = p["total_cost_rate"]

        # ── 1. KPI Cards (top of section for at-a-glance summary) ───────────
        _bh_final = rebal["bh_vals_arr"][-1]
        _rb_final = primary_stats["final_value"]
        _rb_return = primary_stats["total_return"]
        _bh_ret = rebal["bh_m2"]["total_return"]
        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric(f"{primary_label} Final", f"${_rb_final:,.0f}", delta=f"{_rb_return:+.2%}")
        rc2.metric("Buy-and-Hold Final", f"${_bh_final:,.0f}", delta=f"{_bh_ret:+.2%}")
        rc3.metric("Strategy Advantage", f"${_rb_final - _bh_final:+,.0f}", delta=f"{(_rb_return - _bh_ret):+.4%}")
        rc4.metric("Rebalance Events", f"{primary_stats['rebalance_count']:,}", delta=f"Turnover: {primary_stats['turnover_proxy']:.2f}×")

        # ── 2. Portfolio Value Over Time ─────────────────────────────────────
        st.markdown("#### Portfolio Value Over Time")
        selected_strats = st.multiselect(
            "Strategies to display",
            options=_all_strat_labels,
            default=_all_strat_labels,
            key="value_chart_strats",
        )
        if selected_strats:
            _chart_df = comparison_df[selected_strats]
            _chart_colors = [_color_map.get(s, "#666666") for s in selected_strats]
            st.line_chart(_safe_chart_cols(_chart_df), color=_chart_colors, use_container_width=True, height=400)
        else:
            st.info("Select at least one strategy to display.")

        # ── 3. Drawdown Over Time (always shown — no toggle) ─────────────────
        strategy_colors = [_color_map.get(c, "#666666") for c in comparison_df.columns]
        st.markdown("#### Drawdown Over Time")
        dd_chart_df = pd.DataFrame(index=comparison_df.index)
        for _col in comparison_df.columns:
            _vals_c = comparison_df[_col].values
            _rm_c = np.maximum.accumulate(_vals_c)
            safe_rm_c = np.where(_rm_c > 0, _rm_c, 1.0)
            dd_chart_df[_col] = ((_vals_c - _rm_c) / safe_rm_c) * 100
        st.area_chart(_safe_chart_cols(dd_chart_df), color=strategy_colors, use_container_width=True, height=280)
        st.caption("Drawdown (%) = distance below each strategy's historical peak value.")

        # ── 4. Strategy vs B&H dollar difference ────────────────────────────
        if primary_label in comparison_df.columns:
            diff_series = comparison_df[primary_label] - comparison_df["Buy & Hold"]
            diff_chart = pd.DataFrame({f"{primary_label} vs B&H ($)": diff_series})
            advantage_color = "#34a853" if diff_series.iloc[-1] >= 0 else "#ea4335"
            st.area_chart(_safe_chart_cols(diff_chart), color=[advantage_color], use_container_width=True, height=180)
            st.caption(f"Dollar advantage of **{primary_label}** over Buy & Hold over time.")

        st.markdown("---")

        # ── 5. Performance Metrics Table ─────────────────────────────────────
        st.markdown("#### Performance Metrics")
        _mc1, _mc2 = st.columns([5, 1])
        with _mc1:
            st.dataframe(metrics_df, use_container_width=True, hide_index=True)
        with _mc2:
            excel_download_button(
                metrics_df, "strategy_comparison.xlsx",
                label="Strategy Comparison", sheet_name="Metrics",
            )
        st.caption(
            f"Transaction costs ({_commission_bps_d:.0f} commission + {_slippage_bps_d:.0f} slippage + "
            f"{_bid_ask_bps_d:.0f} bid-ask = {_total_cost_rate_d*10000:.0f} bps total) are **embedded in NAV** — "
            "the Final Value and CAGR columns already reflect these costs. "
            "The 'Est. Cost ($)' column shows the cumulative cost dollar amount for reference only. "
            "Sharpe uses Rf=0. TE (ann.) = annualized tracking error vs Buy & Hold. "
            "IR = annualized active return ÷ TE. "
            "Buy & Hold returns are **price-only** (dividends excluded from the B&H baseline)."
        )

        # ── 6. Strategy Ranking ──────────────────────────────────────────────
        st.markdown("#### Strategy Ranking")
        _rk1, _rk2 = st.columns([4, 1])
        with _rk1:
            st.dataframe(_rank_df, use_container_width=True, hide_index=True)
        with _rk2:
            excel_download_button(
                _rank_df, "strategy_ranking.xlsx",
                label="Strategy Ranking", sheet_name="Ranking",
            )
        st.caption("Composite Score = sum of per-dimension ranks. **Lower score = better overall.** Ranks 1=best within each dimension.")

        # ── 7. Transaction Cost Breakdown ────────────────────────────────────
        st.markdown("#### Transaction Cost Breakdown")
        _cc1, _cc2 = st.columns([4, 1])
        with _cc1:
            st.dataframe(_cost_df, use_container_width=True, hide_index=True)
        with _cc2:
            excel_download_button(
                _cost_df, "transaction_cost_breakdown.xlsx",
                label="Transaction Costs", sheet_name="Costs",
            )

        # ── 8. Drawdown Summary Table ────────────────────────────────────────
        st.markdown("#### Drawdown Summary")
        _ddc1, _ddc2 = st.columns([5, 1])
        with _ddc1:
            st.dataframe(_dd_df, use_container_width=True, hide_index=True)
        with _ddc2:
            excel_download_button(
                _dd_df, "drawdown_summary.xlsx",
                label="Drawdown Summary", sheet_name="Drawdowns",
            )

        # ── Drift Diagnostics ────────────────────────────────────────────────
        if drift_histories:
            st.markdown("---")
            st.markdown("#### \U0001f4ca Drift Diagnostics")
            drift_strategy_options = list(drift_histories.keys())

            if "drift_strat_idx" not in st.session_state:
                st.session_state["drift_strat_idx"] = 0
            if "drift_ticker_idx" not in st.session_state:
                st.session_state["drift_ticker_idx"] = 0

            _strat_idx = min(st.session_state["drift_strat_idx"], len(drift_strategy_options) - 1)
            _ticker_idx = min(st.session_state["drift_ticker_idx"], len(tickers_used) - 1)

            selected_drift_strategy = st.selectbox(
                "Select strategy for drift analysis",
                drift_strategy_options, index=_strat_idx, key="drift_strat_select",
            )
            st.session_state["drift_strat_idx"] = drift_strategy_options.index(selected_drift_strategy)

            dh = drift_histories[selected_drift_strategy]

            drift_ticker_select = st.selectbox(
                "Select ticker for drift distribution",
                tickers_used, index=_ticker_idx, key="drift_ticker_select",
            )
            st.session_state["drift_ticker_idx"] = tickers_used.index(drift_ticker_select)

            drift_values = np.array(dh[drift_ticker_select])
            if len(drift_values) > 0:
                drift_pct = drift_values * 100.0
                _drift_mode_lbl = rebal.get("drift_mode", "Absolute")
                tol_for_tk = rebal["tolerances"].get(drift_ticker_select, 0.05)
                breach_pct = np.mean(drift_values > tol_for_tk) * 100

                ds1, ds2, ds3, ds4 = st.columns(4)
                ds1.metric(f"Mean Drift ({_drift_mode_lbl})", f"{np.mean(drift_pct):.2f}%")
                ds2.metric("P95 Drift", f"{np.percentile(drift_pct, 95):.2f}%")
                ds3.metric("Max Drift", f"{np.max(drift_pct):.2f}%")
                ds4.metric("Days Breached (%)", f"{breach_pct:.1f}%")

                n_bins = min(30, max(15, len(drift_pct) // 15))
                counts, bin_edges = np.histogram(drift_pct, bins=n_bins)
                bin_labels = [f"{bin_edges[j]:.2f}-{bin_edges[j+1]:.2f}" for j in range(len(counts))]
                hist_df = pd.DataFrame({"Drift_pct": bin_labels, "Days_count": counts}).set_index("Drift_pct")
                st.bar_chart(hist_df, use_container_width=True, height=250)
                st.caption(
                    f"Distribution of daily {_drift_mode_lbl.lower()} drift (%) for **{drift_ticker_select}** "
                    f"under **{selected_drift_strategy}**. Tolerance = {tol_for_tk:.2%}."
                )

            show_drift_ts = st.checkbox("Show drift time series (all tickers)", value=False)
            if show_drift_ts:
                prices_wide_d = rebal["prices_wide"]
                drift_ts_data = {tk: np.array(vals) * 100.0 for tk, vals in dh.items()}
                drift_ts_df = pd.DataFrame(
                    drift_ts_data,
                    index=prices_wide_d.index[:len(list(dh.values())[0])],
                )
                drift_ts_df.index.name = "PRICEDATE"
                st.line_chart(drift_ts_df, use_container_width=True, height=300)
                st.caption(f"Daily drift (%) per ticker under **{selected_drift_strategy}**.")

        # ── TASK 4: Rebalancing Log ───────────────────────────────────────────
        # Show a structured, human-readable rebalancing activity log with:
        # Date | Strategy | Trigger | Assets Involved | Turnover ($) | Reason
        # This replaces the raw internal event log with a client-ready format.
        if event_logs:
            if not _elog_sum_df.empty:
                st.markdown("#### Rebalancing Event Summary")
                _es1, _es2 = st.columns([4, 1])
                with _es1:
                    st.dataframe(_elog_sum_df, use_container_width=True, hide_index=True)
                with _es2:
                    excel_download_button(
                        _elog_sum_df, "rebalance_event_summary.xlsx",
                        label="Event Summary", sheet_name="Summary",
                        extra_sheets=_elog_sheets,
                    )

            # Build a combined, formatted rebalancing log across all strategies
            _formatted_log_rows = []
            for _lbl_e, _log_df_e in event_logs.items():
                if _log_df_e.empty:
                    continue
                for _, _ev in _log_df_e.iterrows():
                    _reason_raw = str(_ev.get("reason", "")).lower()
                    # Map internal reason codes to plain-English labels
                    if "threshold" in _reason_raw and "calendar" in _reason_raw:
                        _trigger = "Threshold + Calendar"
                        _reason_text = "Drift exceeded tolerance band AND scheduled calendar rebalance"
                    elif "threshold" in _reason_raw:
                        _trigger = "Threshold (Drift)"
                        _reason_text = "A position drifted beyond its tolerance band"
                    elif "calendar" in _reason_raw:
                        _trigger = "Calendar"
                        _reason_text = "Scheduled rebalance date reached"
                    else:
                        _trigger = _reason_raw.title()
                        _reason_text = _reason_raw
                    _formatted_log_rows.append({
                        "Date": pd.Timestamp(_ev["date"]).strftime("%Y-%m-%d"),
                        "Strategy": _lbl_e,
                        "Trigger": _trigger,
                        "Assets Involved": _ev.get("breached_tickers", "All") or "All",
                        "Max Drift": f"{float(_ev.get('max_drift', 0)) * 100:.2f}%" if _ev.get("max_drift") else "—",
                        "Turnover ($)": f"${float(_ev.get('turnover_dollars', 0)):,.0f}",
                        "Reason": _reason_text,
                    })

            if _formatted_log_rows:
                _fmt_log_df = pd.DataFrame(_formatted_log_rows)
                with st.expander("📋 Rebalancing Activity Log", expanded=False):
                    _el1, _el2 = st.columns([5, 1])
                    with _el1:
                        st.dataframe(_fmt_log_df, use_container_width=True, hide_index=True)
                    with _el2:
                        excel_download_button(
                            _fmt_log_df, "rebalancing_log.xlsx",
                            label="Download Log", sheet_name="Rebalancing Log",
                        )
                    st.caption(
                        "Log shows every rebalancing event across all active strategies. "
                        "'Assets Involved' lists tickers that breached their drift tolerance; "
                        "'All' means a full portfolio rebalance was executed."
                    )
            else:
                with st.expander("📋 Rebalancing Activity Log", expanded=False):
                    st.info("No rebalance events were triggered in this simulation.")

        with st.expander("\u2139\ufe0f Rebalancing Engine Notes"):
            st.markdown(f"""
**Calendar:** Rebalances on schedule (Daily / Weekly / Monthly / Quarterly) at same-day closing prices.

**Threshold (Drift-Band):** Breach detected at close → executes next trading day (no look-ahead bias). Cooldown suppresses re-triggers for N days post-rebalance.

**Transaction Costs:** Commission {_commission_bps_d:.0f} bps + Slippage {_slippage_bps_d:.0f} bps + Bid-Ask {_bid_ask_bps_d:.0f} bps = **{_total_cost_rate_d*10000:.0f} bps total**. Costs are **embedded in NAV** — shares are scaled proportionally after each rebalance so the cost drag persists in all future values. This matches the treatment in the TLH engine, which deducts costs directly from cash.

**TLH Engine:** Full lot-level accounting — costs deducted from cash on every trade.

**Buy & Hold Baseline:** Price-only returns. Dividends are not included in the B&H comparison baseline (the Optimizer section does model dividends).

**Sharpe Ratio:** Risk-free rate = 0 throughout for strategy comparison consistency.
            """)

    st.markdown("---")


# ================================================================
# TAX LOSS HARVESTING (TLH) SECTION (read from session_state)
# ================================================================

if opt:
    st.subheader("\U0001f4b0 Tax Loss Harvesting (TLH) \u2014 Tax-Aware Simulation")
    static_result_d = opt["static_result"]
    opt_result_d = opt["opt_result"]
    _tlh_df_d = opt["tlh_df"]

    s_nav = static_result_d["nav_series"]
    o_nav = opt_result_d["nav_series"]
    s_final = s_nav.iloc[-1]
    o_final = o_nav.iloc[-1]
    cap = p["initial_capital"]

    # TASK 4: Total dividends — sum gross value of all DRIP trades in the
    # optimized run's trade log. DRIP = Dividend Reinvestment Plan entry.
    # Non-reinvested dividends are held as cash and not in trades_df, so
    # we estimate them as cash balance above initial_capital when NAV > market value.
    _tdf_opt = opt_result_d.get("trades_df", pd.DataFrame())
    _total_divs_opt = 0.0
    if not _tdf_opt.empty and "action" in _tdf_opt.columns and "gross_value" in _tdf_opt.columns:
        _drip_mask = _tdf_opt["action"] == "DRIP"
        _total_divs_opt = float(_tdf_opt.loc[_drip_mask, "gross_value"].sum())

    # ── 1. KPI Cards ─────────────────────────────────────────────────────────
    kc1, kc2, kc3, kc4, kc5 = st.columns(5)
    kc1.metric("TLH Only — Final NAV", f"${s_final:,.0f}", delta=f"{(s_final/cap - 1):+.2%}")
    kc2.metric("Rebalanced + TLH — Final NAV", f"${o_final:,.0f}", delta=f"{(o_final/cap - 1):+.2%}")
    kc3.metric("Rebal+TLH vs TLH Only", f"${o_final - s_final:+,.0f}", delta=f"{((o_final - s_final)/cap):+.4%}")
    kc4.metric("Total Tax Paid (Rebal+TLH)", f"${opt_result_d['tax_paid_total']:,.0f}",
               delta=f"TLH Only: ${static_result_d['tax_paid_total']:,.0f}")
    kc5.metric(
        "Total Dividends (DRIP)",
        f"${_total_divs_opt:,.0f}" if _total_divs_opt > 0 else "—",
        help="Sum of all dividend payments reinvested (DRIP) in the optimized run. "
             "Requires dividend_data.csv.",
    )

    # ── 2. Portfolio NAV Over Time (3-line: B&H + Static TLH + Optimized) ───
    # Blue = Buy & Hold (pre-tax benchmark), Grey = Static TLH, Orange = Optimized
    st.markdown("#### Portfolio NAV Over Time")
    _bh_nav_opt = daily["Portfolio Value"].reindex(o_nav.index).ffill()
    _opt_chart_base = pd.DataFrame({
        "Buy & Hold (benchmark)": _bh_nav_opt,
        "TLH Only (after-tax)": s_nav,
        "Rebalanced + TLH (after-tax)": o_nav,
    }).dropna()
    st.line_chart(
        _safe_chart_cols(_opt_chart_base),
        color=["#1a73e8", "#888888", "#e8710a"],
        use_container_width=True, height=400,
    )
    st.caption(
        "**Blue:** Buy & Hold (no TLH, no rebalancing). "
        "**Grey:** TLH Only (after-tax). "
        "**Orange:** Rebalanced + TLH (after-tax)."
    )

    # ── 3. Drawdown Over Time ────────────────────────────────────────────────
    st.markdown("#### Drawdown Over Time")
    _opt_dd_df = pd.DataFrame(index=_opt_chart_base.index)
    _opt_dd_colors = ["#1a73e8", "#888888", "#e8710a"]
    for _col in _opt_chart_base.columns:
        _v = _opt_chart_base[_col].values
        _rm = np.maximum.accumulate(_v)
        _safe_rm = np.where(_rm > 0, _rm, 1.0)
        _opt_dd_df[_col] = ((_v - _rm) / _safe_rm) * 100
    st.area_chart(_safe_chart_cols(_opt_dd_df), color=_opt_dd_colors, use_container_width=True, height=280)
    st.caption("Drawdown (%) = distance below each portfolio's historical peak value.")

    st.markdown("---")

    # ── 4. Performance Metrics Table ─────────────────────────────────────────
    # Compute the same metrics as the rebalancing section for each scenario
    # so both sections show identical columns in identical order.
    st.markdown("#### Performance Metrics")
    _opt_metrics_rows = []
    for _lbl_m, _nav_m in [
        ("Buy & Hold", _bh_nav_opt),
        ("TLH Only", s_nav),
        ("Rebalanced + TLH", o_nav),
    ]:
        _nav_arr = _nav_m.dropna().values
        _nav_dates = _nav_m.dropna().index
        if len(_nav_arr) < 2:
            continue
        _bh_arr_bench = _bh_nav_opt.reindex(_nav_dates).ffill().values
        _is_bh = _lbl_m == "Buy & Hold"
        _m_opt = compute_strategy_metrics(
            _nav_arr, cap,
            benchmark_values=None if _is_bh else _bh_arr_bench,
            dates=_nav_dates,
        )
        _opt_metrics_rows.append({
            "Scenario": _lbl_m,
            "Final Value ($)": f"${_nav_arr[-1]:,.0f}",
            "Total Return": f"{_m_opt['total_return']:+.2%}",
            "CAGR": f"{_m_opt['cagr']:+.2%}",
            "Ann. Vol": f"{_m_opt['annualized_vol']:.2%}",
            "Sharpe": f"{_m_opt['sharpe']:.3f}",
            "Max DD": f"{_m_opt['max_drawdown']:.2%}",
            "Avg DD": f"{_m_opt['avg_drawdown']:.2%}",
            "Skew": f"{_m_opt['skewness']:.3f}",
            "Kurt": f"{_m_opt['kurtosis']:.3f}",
            "TE (ann.)": "—" if _is_bh else f"{_m_opt['tracking_error']:.2%}",
            "IR": "—" if _is_bh else f"{_m_opt['information_ratio']:.3f}",
        })
    _opt_metrics_df = pd.DataFrame(_opt_metrics_rows)
    _om1, _om2 = st.columns([5, 1])
    with _om1:
        st.dataframe(_opt_metrics_df, use_container_width=True, hide_index=True)
    with _om2:
        excel_download_button(_opt_metrics_df, "tlh_metrics.xlsx",
                              label="TLH Metrics", sheet_name="Metrics")
    st.caption("Sharpe uses Rf=0. TE and IR measured vs Buy & Hold benchmark.")

    # ── 5. TLH & Tax Summary ─────────────────────────────────────────────────
    st.markdown("#### TLH & Tax Summary")
    _tl1, _tl2 = st.columns([4, 1])
    with _tl1:
        st.dataframe(_tlh_df_d, use_container_width=True, hide_index=True)
    with _tl2:
        excel_download_button(
            _tlh_df_d, "tlh_tax_summary.xlsx",
            label="TLH & Tax Summary", sheet_name="TLH Summary",
        )
    st.caption(
        "**Tax Alpha 2** = (TLH portfolio NAV) − (identical portfolio NAV without TLH). "
        "Measures the actual dollar advantage that tax-loss harvesting created relative to "
        "an identical strategy that never harvested. A positive value means TLH added value. "
        "**Net Benefit** = Est. Tax Savings − Tax Paid − Execution Costs (rough estimate). "
        "**Modeling notes:** Dividends are taxed at the long-term capital gains rate (qualified "
        "dividend assumption). The Buy & Hold benchmark uses price-only returns. "
        "Wash-sale avoidance is modeled via proxy substitution."
    )

    # ── 6. Detailed Logs (expanders) ─────────────────────────────────────────
    with st.expander("📋 Realized Gain/Loss Detail"):
        _rdf_d = opt_result_d["realized_df"]
        if not _rdf_d.empty:
            _rg1, _rg2 = st.columns([5, 1])
            with _rg1:
                st.dataframe(_rdf_d, use_container_width=True, hide_index=True)
            with _rg2:
                excel_download_button(_rdf_d, "realized_gain_loss.xlsx", label="Realized Gain/Loss", sheet_name="Realized")
        else:
            st.info("No realized gains/losses.")

    with st.expander("📋 Optimized Portfolio — Trade Log"):
        _tdf_d = opt_result_d["trades_df"]
        if not _tdf_d.empty:
            _tr1, _tr2 = st.columns([5, 1])
            with _tr1:
                st.dataframe(_tdf_d, use_container_width=True, hide_index=True)
            with _tr2:
                excel_download_button(_tdf_d, "trade_log.xlsx", label="Trade Log", sheet_name="Trades")
        else:
            st.info("No trades recorded.")

    st.markdown("---")


# ================================================================
#  HOLDINGS TABLE
# ================================================================

st.subheader("Per-Holding Detail")
_h1, _h2 = st.columns([5, 1])
with _h1:
    st.dataframe(display_df, use_container_width=True, hide_index=True)
with _h2:
    excel_download_button(
        holdings, "holdings_detail.xlsx",
        label="Holdings Detail", sheet_name="Holdings",
    )


# ================================================================
#  DOWNLOAD ALL TABLES
# ================================================================

st.markdown("---")
st.subheader("\U0001f4e6 Download All Tables")
st.caption("Single Excel workbook with every computed table as a separate sheet.")
_da1, _da2 = st.columns([1, 3])
with _da1:
    if export:
        st.download_button(
            label="\u2b07 Download All Tables (.xlsx)",
            data=to_excel_bytes(export),
            file_name="portfolio_analysis_full.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_all_tables",
        )
with _da2:
    if export:
        _sep = " \u00b7 "
        st.caption(f"**{len(export)} sheets:** {_sep.join(export.keys())}")


# ================================================================
#  ASSUMPTIONS EXPANDER
# ================================================================

with st.expander("\u2139\ufe0f Assumptions & Methodology"):
    _comm_d = p["commission_bps"]
    _slip_d = p["slippage_bps"]
    _bask_d = p["bid_ask_bps"]
    _tcr_d = p["total_cost_rate"]
    _gst_d = p["global_st_rate"]
    _glt_d = p["global_lt_rate"]
    # TASK 7: Updated assumptions text for accuracy and consistency with current engine behavior
    st.markdown(f"""
**Data:** Status 1 (active) and 15 (suspended-but-valid) rows only. Start date shifts forward to next trading day; end date shifts backward to previous trading day.

**Returns (Buy & Hold / Calendar / Threshold):** Price appreciation only — dividends and splits are not included. For dividend-inclusive returns, enable Tax Loss Harvesting (TLH) and provide a dividend data file.

**Dividends (TLH Engine):** When dividend_data.csv is present, dividends are automatically reinvested (DRIP) or held as cash depending on your selection. Total dividends reinvested is shown in the KPI row of the TLH section.

**CAGR:** Annualized geometric return using actual calendar days elapsed ÷ 365.25. For periods under 30 days, CAGR extrapolates aggressively and should be interpreted with caution.

**Annualized Volatility & Tracking Error:** Computed using 252 trading days per year (industry standard for daily data). TE is shown as % in the strategy comparison table.

**Fractional Shares:** Default. Toggle "Whole shares only" for integer shares with uninvested cash residual.

**Transaction Costs (Calendar/Threshold engines):** {_tcr_d*10000:.0f} bps total ({_comm_d:.0f} commission + {_slip_d:.0f} slippage + {_bask_d:.0f} bid-ask) applied to gross turnover. **Embedded in NAV** — shares are proportionally scaled down after each rebalance so the cost drag persists in all future values. The 'Est. Cost ($)' column is for reference only.

**Transaction Costs (TLH Engine):** Deducted from portfolio cash on every trade. Same {_comm_d:.0f}+{_slip_d:.0f}+{_bask_d:.0f} bps breakdown as above. Both engines are on the same net-of-cost basis.

**Tax Rates:** ST={_gst_d:.0%} / LT={_glt_d:.0%} (sidebar-configurable). Applied in the TLH engine with lot-level tracking, wash-sale enforcement, and loss carry-forward netting. Not applied to the base calendar/threshold engines.

**Sharpe Ratio:** Risk-free rate = 0 throughout (appropriate for cross-strategy relative comparison).
    """)

# ── END OF DISPLAY SECTION ────────────────────────────────────────────────
