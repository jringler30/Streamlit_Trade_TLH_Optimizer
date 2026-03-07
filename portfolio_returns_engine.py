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
from scipy import stats as sp_stats
import warnings


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


# ================================================================
#  CORE ENGINE FUNCTIONS
# ================================================================


def validate_weights(tickers: List[str], weights: List[float],
                     tolerance: float = 0.05) -> Tuple[List[str], List[float]]:
    """
    Normalize and validate portfolio weights before any simulation runs.

    Key behaviors:
    - Merges duplicate tickers by summing their weights (e.g., two SPY entries → one).
    - Rejects negative weights outright (no short positions in this engine).
    - Allows weights that don't sum to exactly 1.0, but only within a 5% tolerance
      band — then normalizes them. This prevents silent misconfiguration while still
      being forgiving of rounding in the UI inputs.
    """
    if len(tickers) != len(weights):
        raise ValueError(f"Length mismatch: {len(tickers)} tickers vs {len(weights)} weights.")
    if any(w < 0 for w in weights):
        raise ValueError("Negative weights are not allowed.")

    # Merge duplicate tickers: if user enters SPY twice, combine weights
    combined: Dict[str, float] = {}
    for t, w in zip(tickers, weights):
        t_upper = t.strip().upper()
        combined[t_upper] = combined.get(t_upper, 0.0) + w
    tickers_out = list(combined.keys())
    weights_out = list(combined.values())
    total = sum(weights_out)
    if total == 0:
        raise ValueError("Total weight is zero.")
    if abs(total - 1.0) > tolerance:
        raise ValueError(
            f"Weights sum to {total:.4f}, which deviates from 1.0 by more than "
            f"tolerance ({tolerance}). Please fix your weights."
        )
    # Normalize so weights sum to exactly 1.0 (removes small rounding errors)
    weights_out = [w / total for w in weights_out]
    return tickers_out, weights_out


def prepare_price_data(df: pd.DataFrame, price_field: str = "PRICECLOSE") -> pd.DataFrame:
    """
    Clean raw price data for use by all engine functions.

    Filters to active trading items only (status 1 = active, 15 = suspended-but-valid).
    Status 15 is kept because suspended instruments may still have valid historical
    prices needed for backtesting. All other statuses (delisted, errored, etc.) are
    excluded to avoid stale or unreliable price data.

    The sort by (ticker, date) is critical: get_ticker_prices relies on iloc[0] and
    iloc[-1] to find the first/last price in a date range, which only works correctly
    on sorted data. This function runs once at app startup via @st.cache_data.
    """
    df = df.copy()
    df["PRICEDATE"] = pd.to_datetime(df["PRICEDATE"], errors="coerce")
    df = df.dropna(subset=["PRICEDATE"])
    if "TRADINGITEMSTATUSID" in df.columns:
        df = df[df["TRADINGITEMSTATUSID"].isin([1, 15])].copy()
    if price_field not in df.columns:
        raise ValueError(f"Price field '{price_field}' not found in dataset.")
    df[price_field] = pd.to_numeric(df[price_field], errors="coerce")
    df = df.dropna(subset=[price_field])
    df["TICKERSYMBOL"] = df["TICKERSYMBOL"].astype(str).str.strip().str.upper()
    df = df.sort_values(["TICKERSYMBOL", "PRICEDATE"]).reset_index(drop=True)
    return df


def get_ticker_prices(ticker_df, ticker, start_date, end_date, price_field):
    """
    Find the nearest valid trading-day prices for a single ticker within
    [start_date, end_date]. If the exact start/end date has no price data
    (weekends, holidays), the date is shifted to the nearest available trading day:
      - Start date shifts FORWARD to the next available trading day
      - End date shifts BACKWARD to the previous available trading day

    This asymmetry is intentional: it guarantees the actual measurement window
    falls entirely within the user's requested window, never extending beyond it.
    Shifts are recorded as flags so the UI can warn users about date adjustments.

    Returns a dict with price data on success, or {"error": ...} on failure.
    The caller is responsible for handling the error case (typically by dropping
    the ticker and re-normalizing weights).
    """
    flags = []
    on_or_after = ticker_df[ticker_df["PRICEDATE"] >= start_date]
    if on_or_after.empty:
        return {"error": f"No data for {ticker} on/after {start_date.date()}."}
    start_row = on_or_after.iloc[0]
    start_date_used = start_row["PRICEDATE"]
    start_price = float(start_row[price_field])
    if start_date_used != start_date:
        flags.append(f"start shifted {start_date.date()}->{start_date_used.date()}")
    on_or_before = ticker_df[ticker_df["PRICEDATE"] <= end_date]
    if on_or_before.empty:
        return {"error": f"No data for {ticker} on/before {end_date.date()}."}
    end_row = on_or_before.iloc[-1]
    end_date_used = end_row["PRICEDATE"]
    end_price = float(end_row[price_field])
    if end_date_used != end_date:
        flags.append(f"end shifted {end_date.date()}->{end_date_used.date()}")
    if start_date_used > end_date_used:
        return {"error": f"Adjusted start after end for {ticker}."}
    return {
        "start_date_used": start_date_used, "end_date_used": end_date_used,
        "start_price": start_price, "end_price": end_price, "flags": flags,
    }


def calculate_portfolio_returns(
    df, tickers, weights, start_date, end_date,
    initial_capital=100_000.0, price_field="PRICECLOSE",
    allow_cash_residual=False,
):
    """
    Core buy-and-hold returns calculation. This is the foundation that all other
    strategies build upon — it establishes the initial share allocations and
    computes point-to-point returns.

    When allow_cash_residual=True, uses whole (integer) shares and tracks the
    uninvested cash separately. Otherwise uses fractional shares (the default)
    which assumes full capital deployment.

    If a ticker has no price data in the date range, it's dropped and the
    remaining tickers' weights are re-normalized — so a 3-ticker portfolio
    with one bad ticker becomes a 2-ticker portfolio with proportionally
    scaled weights.
    """
    if price_field not in ("PRICECLOSE", "PRICEMID"):
        raise ValueError(f"price_field must be 'PRICECLOSE' or 'PRICEMID'.")
    tickers, weights = validate_weights(tickers, weights)
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    if start_dt >= end_dt:
        raise ValueError("start_date must be before end_date.")
    clean = df
    available = set(clean["TICKERSYMBOL"].unique())
    missing = [t for t in tickers if t not in available]
    if missing:
        raise ValueError(f"Tickers not found in dataset: {missing}")

    # Attempt to resolve prices for each ticker; track any that fail
    rows, dropped = [], []
    for ticker, weight in zip(tickers, weights):
        result = get_ticker_prices(
            clean[clean["TICKERSYMBOL"] == ticker], ticker, start_dt, end_dt, price_field
        )
        if "error" in result:
            dropped.append((ticker, weight, result["error"]))
            continue
        rows.append({"ticker": ticker, "weight": weight, **result})
    if dropped and not rows:
        raise ValueError("All tickers dropped -- insufficient data.")

    # Re-normalize weights after dropping any tickers with missing data
    if dropped:
        total_w = sum(r["weight"] for r in rows)
        for r in rows:
            r["weight"] /= total_w

    holdings_data = []
    total_cash_residual = 0.0
    for r in rows:
        alloc = initial_capital * r["weight"]
        if allow_cash_residual:
            # Whole shares: floor division, remainder stays as cash
            shares = int(alloc // r["start_price"])
            total_cash_residual += alloc - shares * r["start_price"]
        else:
            shares = alloc / r["start_price"]
        end_value = shares * r["end_price"]
        cost = shares * r["start_price"]
        holdings_data.append({
            "Ticker": r["ticker"], "Weight": r["weight"],
            "Start Date": r["start_date_used"].strftime("%Y-%m-%d"),
            "End Date": r["end_date_used"].strftime("%Y-%m-%d"),
            "Start Price": round(r["start_price"], 2),
            "End Price": round(r["end_price"], 2),
            "Shares": round(shares, 4),
            "Start Value": round(cost, 2),
            "End Value": round(end_value, 2),
            # Per-asset return: pure price appreciation (end_price/start_price - 1).
            # This is independent of allocation size — same for $1K or $100K invested.
            "Return": round((r["end_price"] / r["start_price"]) - 1, 6),
            # Dollar gain: absolute P&L for this position at the allocated capital level
            "Gain ($)": round(end_value - cost, 2),
            # Percentage gain: same as Return when fractional shares are used, but
            # can differ slightly with whole shares due to cash residual not being invested
            "Gain (%)": round((end_value - cost) / cost, 6) if cost else 0,
            "Flags": "; ".join(r["flags"]) if r["flags"] else "OK",
        })
    holdings_df = pd.DataFrame(holdings_data)
    # Portfolio end value includes cash residual (if whole-shares mode is on).
    # This ensures the total return accounts for ALL initial capital, not just
    # the portion deployed in shares.
    port_end = holdings_df["End Value"].sum() + total_cash_residual
    # The summary dict is the primary output contract consumed by the Streamlit
    # dashboard for KPI cards, and by downstream engines as a reference point.
    summary = {
        "portfolio_start_value": initial_capital,
        "portfolio_end_value": round(port_end, 2),
        "portfolio_total_return": round(port_end / initial_capital - 1, 6),
        "total_unrealized_gain_dollars": round(port_end - initial_capital, 2),
        "total_unrealized_gain_pct": round((port_end - initial_capital) / initial_capital, 6),
        "cash_residual": round(total_cash_residual, 2),
        "tickers_dropped": len(dropped),
        "dropped_details": dropped,
    }
    return summary, holdings_df


def build_daily_series(df, holdings, initial_capital, price_field="PRICECLOSE"):
    """
    Construct a daily time series for the buy-and-hold portfolio. Each ticker's
    daily value = shares_held × closing_price. The join uses outer merge + ffill/bfill
    to handle tickers with different trading calendars (e.g., if one ticker is
    missing a date, its last known value carries forward).

    This series is the baseline "Buy & Hold" curve used throughout the dashboard
    and as the benchmark for tracking error / information ratio calculations.
    """
    clean = df.copy()
    clean["PRICEDATE"] = pd.to_datetime(clean["PRICEDATE"], errors="coerce")
    clean["TICKERSYMBOL"] = clean["TICKERSYMBOL"].astype(str).str.strip().str.upper()
    clean[price_field] = pd.to_numeric(clean[price_field], errors="coerce")
    all_start = pd.to_datetime(holdings["Start Date"]).min()
    all_end = pd.to_datetime(holdings["End Date"]).max()
    clean = clean[(clean["PRICEDATE"] >= all_start) & (clean["PRICEDATE"] <= all_end)]
    tickers = holdings["Ticker"].tolist()
    clean = clean[clean["TICKERSYMBOL"].isin(tickers)]

    # Build one time series per ticker, then join them on date.
    # Each ticker becomes a column of dollar values (shares × price).
    # We build per-ticker first because different tickers may have different
    # trading calendars — an ETF might trade on a day when a bond fund doesn't.
    frames = []
    for _, row in holdings.iterrows():
        tk = row["Ticker"]
        shares = row["Shares"]
        tk_prices = (
            clean[clean["TICKERSYMBOL"] == tk][["PRICEDATE", price_field]]
            .drop_duplicates(subset="PRICEDATE")
            .set_index("PRICEDATE").sort_index()
            .rename(columns={price_field: tk})
        )
        # Convert from price → dollar value held
        tk_prices[tk] = tk_prices[tk] * shares
        frames.append(tk_prices)

    # Outer join preserves all dates from all tickers. ffill then bfill ensures
    # no NaN gaps — if ticker A trades on Monday but ticker B doesn't, B's
    # Friday close carries forward. bfill handles the edge case where a ticker's
    # data starts later than others.
    daily = frames[0]
    for f in frames[1:]:
        daily = daily.join(f, how="outer")
    daily = daily.sort_index().ffill().bfill()
    daily["Portfolio Value"] = daily[tickers].sum(axis=1)
    # Cost basis is a flat horizontal line at initial_capital. This is by design —
    # in buy-and-hold, no additional capital is deployed, so the gap between
    # Portfolio Value and Cost Basis represents the unrealized gain/loss over time.
    daily["Cost Basis"] = initial_capital

    # Per-ticker cumulative return series (used for the return breakdown chart)
    for tk in tickers:
        start_val = daily[tk].iloc[0]
        daily[f"{tk} Return (%)"] = (daily[tk] / start_val - 1) * 100
    return daily


# ================================================================
# V3: Calendar rebalancing engine (UNCHANGED from V3)
# ================================================================

def build_prices_wide(df, tickers, start_date, end_date, price_field="PRICECLOSE"):
    """
    Pivot long-format price data into a (date × ticker) wide matrix.

    This is the shared data structure consumed by both the calendar and threshold
    rebalancing engines. Filtering to only needed tickers and dates FIRST keeps
    memory usage manageable even with a large universe (the full dataset has
    thousands of tickers, but we typically simulate 3–10).
    """
    mask = (
        df["TICKERSYMBOL"].isin(tickers)
        & (df["PRICEDATE"] >= pd.Timestamp(start_date))
        & (df["PRICEDATE"] <= pd.Timestamp(end_date))
    )
    subset = df.loc[mask, ["TICKERSYMBOL", "PRICEDATE", price_field]].copy()
    # De-duplicate before pivoting — if the source data has multiple rows for the
    # same ticker+date (e.g., from different exchanges), pivot() would raise an error.
    subset = subset.drop_duplicates(subset=["TICKERSYMBOL", "PRICEDATE"])
    # Pivot from long format (one row per ticker per day) to wide format (one column
    # per ticker, one row per day). This is the format both simulation engines need
    # for fast random access by date: prices_wide.loc[date, ticker].
    wide = subset.pivot(index="PRICEDATE", columns="TICKERSYMBOL", values=price_field)
    # ffill then bfill handles missing dates (holidays, delistings) so every
    # ticker has a price for every trading day in the range
    wide = wide.sort_index().ffill().bfill()
    missing_cols = [t for t in tickers if t not in wide.columns]
    if missing_cols:
        raise ValueError(f"Tickers missing from price data after filtering: {missing_cols}")
    wide = wide[tickers]
    return wide


def _get_rebalance_dates(trading_dates, freq):
    """
    Determine which trading days are rebalance dates for a given calendar frequency.

    The logic detects transitions: e.g., for Monthly, the first trading day where
    the month differs from the previous day's month triggers a rebalance. This
    naturally handles holidays — if the 1st of the month is a holiday, the 2nd
    (or next trading day) becomes the rebalance date.

    Returns a set for O(1) membership testing in the main simulation loop.
    """
    dates = pd.DatetimeIndex(trading_dates)
    if len(dates) < 2:
        return set()
    if freq == "Daily":
        return set(dates[1:])
    rebal_set = set()
    if freq == "Weekly":
        # Track ISO week number. Rebalance fires on the first trading day where
        # the week number changes. Year tracking prevents false triggers at
        # year boundaries (ISO week 1 of new year vs week 52 of old year).
        prev_week = dates[0].isocalendar()[1]
        prev_year = dates[0].year
        for dt in dates[1:]:
            iso = dt.isocalendar()
            if iso[1] != prev_week or dt.year != prev_year:
                rebal_set.add(dt)
                prev_week = iso[1]
                prev_year = dt.year
    elif freq == "Monthly":
        prev_month = dates[0].month
        prev_year = dates[0].year
        for dt in dates[1:]:
            if dt.month != prev_month or dt.year != prev_year:
                rebal_set.add(dt)
                prev_month = dt.month
                prev_year = dt.year
    elif freq == "Quarterly":
        # Only rebalance at the start of calendar quarters (Jan/Apr/Jul/Oct).
        # The prev_month tracking outside the quarter_months check is necessary
        # to avoid re-triggering: without it, every trading day in January would
        # match the condition if we only checked dt.month != prev_month.
        quarter_months = {1, 4, 7, 10}
        prev_month = dates[0].month
        prev_year = dates[0].year
        for dt in dates[1:]:
            if dt.month in quarter_months and (dt.month != prev_month or dt.year != prev_year):
                rebal_set.add(dt)
            # Always track month transitions so we don't re-trigger mid-quarter
            if dt.month != prev_month or dt.year != prev_year:
                prev_month = dt.month
                prev_year = dt.year
    else:
        raise ValueError(f"Unknown rebalance frequency: {freq}")
    return rebal_set


def build_rebalanced_series(prices_wide, target_weights, initial_capital, rebalance_freq):
    """
    Original calendar-only rebalancing engine (V3). Kept intact because the
    threshold engine (V4) is additive — this function is still used for
    pure-calendar strategies in the comparison dashboard.

    On each rebalance date, the portfolio is valued and shares are adjusted to
    restore exact target weights. Trades execute at same-day closing prices.
    Fractional shares are used (consistent with the base engine).

    The 1e-10 drift tolerance avoids unnecessary rebalance events when weights
    are already at target (floating-point noise).
    """
    tickers = list(target_weights.keys())
    dates = prices_wide.index.tolist()
    n_days = len(dates)
    if n_days == 0:
        raise ValueError("No trading dates in the filtered price data.")
    rebal_dates = _get_rebalance_dates(dates, rebalance_freq)

    # Day 0: allocate initial capital to shares based on target weights
    shares = {}
    for tk in tickers:
        alloc = initial_capital * target_weights[tk]
        shares[tk] = alloc / prices_wide.loc[dates[0], tk]

    # Pre-allocate numpy arrays for performance — avoids DataFrame append overhead
    # during the hot simulation loop
    portfolio_values = np.empty(n_days, dtype=np.float64)
    ticker_values_arr = {tk: np.empty(n_days, dtype=np.float64) for tk in tickers}
    rebalance_count = 0
    total_turnover_dollars = 0.0

    for i, dt in enumerate(dates):
        # Mark-to-market: value each position at today's close
        total_value = 0.0
        tv = {}
        for tk in tickers:
            val = shares[tk] * prices_wide.loc[dt, tk]
            tv[tk] = val
            total_value += val
        portfolio_values[i] = total_value
        for tk in tickers:
            ticker_values_arr[tk][i] = tv[tk]

        # Calendar rebalance: check if today is a scheduled rebalance date.
        # The "needs_rebalance" check avoids a rebalance event (and turnover
        # accounting) when the portfolio is already at target — this can happen
        # with Daily frequency after a day where all assets moved in lockstep.
        if dt in rebal_dates and total_value > 0:
            day_turnover = 0.0
            needs_rebalance = False
            for tk in tickers:
                current_weight = tv[tk] / total_value
                if abs(current_weight - target_weights[tk]) > 1e-10:
                    needs_rebalance = True
                    break
            if needs_rebalance:
                rebalance_count += 1
                # Rebalance all assets simultaneously: compute target dollar value
                # for each ticker, convert to shares at today's price, and record
                # the trade volume for turnover calculation.
                for tk in tickers:
                    target_value = target_weights[tk] * total_value
                    new_shares = target_value / prices_wide.loc[dt, tk]
                    trade_shares = new_shares - shares[tk]
                    trade_dollars = abs(trade_shares * prices_wide.loc[dt, tk])
                    day_turnover += trade_dollars
                    shares[tk] = new_shares
                total_turnover_dollars += day_turnover

    # Turnover proxy: sum of absolute trade dollars / average portfolio value.
    # This gives a single scalar that captures how "active" the strategy is —
    # higher values mean more trading cost drag in a real implementation.
    avg_port_value = np.mean(portfolio_values)
    turnover_proxy = (total_turnover_dollars / avg_port_value) if avg_port_value > 0 else 0.0
    rebal_daily = pd.DataFrame(index=dates)
    rebal_daily.index.name = "PRICEDATE"
    # Per-ticker columns use "(Rebal)" suffix to distinguish from the buy-and-hold
    # daily series which uses bare ticker names. This prevents column collisions
    # when both series are displayed side by side.
    for tk in tickers:
        rebal_daily[f"{tk} (Rebal)"] = ticker_values_arr[tk]
    rebal_daily["Portfolio Value"] = portfolio_values
    # rebal_stats is the summary contract consumed by the metrics table and KPI cards.
    # turnover_proxy is dimensionless (dollars traded / average portfolio value) so
    # it's comparable across different capital amounts and time periods.
    rebal_stats = {
        "rebalance_count": rebalance_count,
        "turnover_proxy": round(turnover_proxy, 4),
        "final_value": round(portfolio_values[-1], 2),
        "total_return": round(portfolio_values[-1] / initial_capital - 1, 6),
    }
    return rebal_daily, rebal_stats


# ================================================================
# V4 ADDITION: THRESHOLD (DRIFT-BAND) REBALANCING ENGINE
# ================================================================
#
# The threshold engine introduces a fundamentally different rebalancing trigger:
# instead of calendar dates, it monitors portfolio drift continuously and fires
# when any asset breaches its tolerance band. This section contains the helper
# functions and the main simulation loop.

### THRESHOLD REBALANCE ADDITIONS -- Helper Functions


def compute_weights(shares: Dict[str, float], prices: Dict[str, float]) -> Dict[str, float]:
    """Compute current portfolio weights from shares and prices."""
    values = {tk: shares[tk] * prices[tk] for tk in shares}
    total = sum(values.values())
    if total <= 0:
        return {tk: 0.0 for tk in shares}
    return {tk: values[tk] / total for tk in shares}


def compute_drift(
    current_weights: Dict[str, float],
    target_weights: Dict[str, float],
    drift_mode: str = "Absolute",
) -> Dict[str, float]:
    """
    Compute per-asset drift between current and target weights.

    Two modes reflect different portfolio management philosophies:
    - Absolute: |w_i - target_i|. Simple, intuitive. A 50% target drifting to
      55% has the same drift (5pp) as a 5% target drifting to 10%.
    - Relative: |w_i / target_i - 1|. Proportional. The 5%→10% drift is 100%
      relative drift, while 50%→55% is only 10%. Better for portfolios with
      very different-sized allocations.
    """
    drift = {}
    for tk in target_weights:
        w_cur = current_weights.get(tk, 0.0)
        w_tgt = target_weights[tk]
        if drift_mode == "Relative":
            # Guard against division by zero for zero-weight targets
            if w_tgt < 1e-12:
                drift[tk] = abs(w_cur)
            else:
                drift[tk] = abs(w_cur / w_tgt - 1.0)
        else:
            drift[tk] = abs(w_cur - w_tgt)
    return drift


def find_threshold_triggers(
    drift: Dict[str, float],
    tolerances: Dict[str, float],
) -> List[str]:
    """
    Return list of tickers whose drift exceeds their per-asset tolerance.

    The 1e-12 epsilon prevents floating-point noise from triggering false
    breaches when drift is exactly at the tolerance boundary.
    """
    breached = []
    for tk, d in drift.items():
        tol = tolerances.get(tk, 0.05)
        if d > tol + 1e-12:
            breached.append(tk)
    return breached


def apply_rebalance_full(
    shares: Dict[str, float],
    target_weights: Dict[str, float],
    prices: Dict[str, float],
    total_value: float,
    whole_shares: bool = False,
) -> Tuple[Dict[str, float], float]:
    """
    Full rebalance: set ALL assets to exact target weights regardless of which
    ones breached. This is the simpler and more common institutional approach —
    it ensures the portfolio is always at target after a rebalance event.

    Returns (new_shares, turnover) where turnover is the sum of absolute dollar
    values of all trades. Note: this is a mathematical position reset, not a
    real trade execution — there are no tax lots, realized gains, or cash
    accounting. Compare with the MSBA v1 optimizer which does all of those.
    """
    turnover = 0.0
    new_shares = {}
    for tk in target_weights:
        target_val = target_weights[tk] * total_value
        if whole_shares:
            ns = int(target_val // prices[tk]) if prices[tk] > 0 else 0
        else:
            ns = target_val / prices[tk] if prices[tk] > 0 else 0.0
        trade_dollars = abs(ns - shares[tk]) * prices[tk]
        turnover += trade_dollars
        new_shares[tk] = ns
    return new_shares, turnover


def apply_rebalance_partial(
    shares: Dict[str, float],
    target_weights: Dict[str, float],
    tolerances: Dict[str, float],
    breached_tickers: List[str],
    prices: Dict[str, float],
    total_value: float,
    whole_shares: bool = False,
) -> Tuple[Dict[str, float], float]:
    """
    Partial rebalance: only trade the breached assets back to target weight.
    Non-breached assets are scaled proportionally to absorb the weight change,
    preserving their relative allocation to each other.

    This minimizes trading costs but can leave the portfolio slightly off-target
    for non-breached assets. It's the preferred approach when transaction costs
    or tax impact are a concern.

    The scaling logic: if breached assets consume X% of total weight at target,
    the remaining (1-X)% is distributed among non-breached assets proportional
    to their current weight ratios.
    """
    tickers = list(target_weights.keys())
    current_weights = compute_weights(shares, prices)
    breached_set = set(breached_tickers)
    # Calculate how much weight budget remains for non-breached assets after
    # the breached ones are set to their exact target weights.
    # Example: if SPY (50% target) breached, remaining_budget = 0.50 for AGG + QQQ.
    breached_target_sum = sum(target_weights[tk] for tk in breached_set)
    remaining_budget = 1.0 - breached_target_sum
    # Non-breached assets are scaled proportionally to their CURRENT weights
    # (not targets). This preserves relative allocation among the "healthy" assets
    # rather than forcing them to target, which would defeat the purpose of partial.
    non_breached_current_sum = sum(
        current_weights.get(tk, 0.0) for tk in tickers if tk not in breached_set
    )

    # Build desired weights: exact targets for breached, proportional scale for others
    desired_weights = {}
    for tk in tickers:
        if tk in breached_set:
            desired_weights[tk] = target_weights[tk]
        else:
            if non_breached_current_sum > 1e-12:
                desired_weights[tk] = (current_weights.get(tk, 0.0) / non_breached_current_sum) * remaining_budget
            else:
                # Edge case: all non-breached assets have zero weight — distribute evenly
                n_non = len(tickers) - len(breached_set)
                desired_weights[tk] = remaining_budget / n_non if n_non > 0 else 0.0
    turnover = 0.0
    new_shares = {}
    for tk in tickers:
        target_val = desired_weights[tk] * total_value
        if whole_shares:
            ns = int(target_val // prices[tk]) if prices[tk] > 0 else 0
        else:
            ns = target_val / prices[tk] if prices[tk] > 0 else 0.0
        trade_dollars = abs(ns - shares[tk]) * prices[tk]
        turnover += trade_dollars
        new_shares[tk] = ns
    return new_shares, turnover


def build_threshold_rebalanced_series(
    prices_wide,
    target_weights,
    initial_capital,
    tolerances,
    drift_mode="Absolute",
    rebalance_action="Full",
    cooldown_days=0,
    calendar_freq=None,
    enable_calendar=False,
    enable_threshold=True,
    whole_shares=False,
):
    """
    Combined calendar + threshold (drift-band) rebalancing engine.

    This is the V4 flagship engine. It runs both rebalancing mechanisms
    simultaneously without either suppressing the other. The key design
    decisions are:

    1. NEXT-DAY EXECUTION: Threshold breaches detected at end-of-day execute
       on the NEXT trading day. This avoids look-ahead bias — in practice,
       you'd detect drift at close and submit orders for next-day execution.

    2. COOLDOWN: After a threshold rebalance, further threshold triggers are
       suppressed for N trading days. This prevents whipsaw in volatile markets
       where drift might oscillate around the tolerance band. Calendar events
       are NOT affected by cooldown — they always fire on schedule.

    3. ORDER OF OPERATIONS within each day:
       a) Mark-to-market and record drift
       b) Execute any pending threshold rebalance from yesterday's breach
       c) Execute calendar rebalance if today is a scheduled date
       d) Check for new threshold breaches (scheduled for tomorrow)
       e) Decrement cooldown counter

    Returns: (rebal_daily, rebal_stats, event_log_df, drift_history)
    - event_log_df: structured log of every rebalance event for audit/export
    - drift_history: per-ticker daily drift values for diagnostics visualization
    """
    tickers = list(target_weights.keys())
    dates = prices_wide.index.tolist()
    n_days = len(dates)
    if n_days == 0:
        raise ValueError("No trading dates in the filtered price data.")

    # Pre-compute the set of calendar rebalance dates (empty if calendar disabled)
    calendar_dates = set()
    if enable_calendar and calendar_freq and calendar_freq != "None":
        calendar_dates = _get_rebalance_dates(dates, calendar_freq)

    # Initialize shares at Day 0
    shares = {}
    for tk in tickers:
        alloc = initial_capital * target_weights[tk]
        p0 = prices_wide.loc[dates[0], tk]
        if whole_shares:
            shares[tk] = int(alloc // p0) if p0 > 0 else 0
        else:
            shares[tk] = alloc / p0 if p0 > 0 else 0.0

    # Pre-allocate arrays (same pattern as calendar engine for performance)
    portfolio_values = np.empty(n_days, dtype=np.float64)
    ticker_values_arr = {tk: np.empty(n_days, dtype=np.float64) for tk in tickers}
    drift_history = {tk: [] for tk in tickers}
    event_log = []

    rebalance_count = 0
    calendar_rebal_count = 0
    threshold_rebal_count = 0
    total_turnover_dollars = 0.0
    cooldown_remaining = 0

    # Threshold state machine: breach detected today → pending for tomorrow.
    # These three variables carry the pending state across loop iterations.
    pending_threshold_breach = False
    pending_breached_tickers = []
    pending_max_drift = 0.0

    for i, dt in enumerate(dates):
        prices_today = {tk: float(prices_wide.loc[dt, tk]) for tk in tickers}
        total_value = sum(shares[tk] * prices_today[tk] for tk in tickers)
        portfolio_values[i] = total_value
        for tk in tickers:
            ticker_values_arr[tk][i] = shares[tk] * prices_today[tk]

        # Record drift for every day regardless of whether a rebalance occurs.
        # This powers the drift diagnostics section: histograms, P95, breach %,
        # and the drift time-series chart. Computing it unconditionally means
        # we capture drift behavior between rebalance events, not just at trigger points.
        current_weights = compute_weights(shares, prices_today)
        drift = compute_drift(current_weights, target_weights, drift_mode)
        for tk in tickers:
            drift_history[tk].append(drift.get(tk, 0.0))

        did_rebalance_today = False
        rebal_reasons = []

        # STEP 1: Execute pending threshold rebalance (breach detected yesterday).
        # This is the next-day execution model — avoids using information we
        # wouldn't have had in real-time.
        if pending_threshold_breach and enable_threshold and i > 0:
            if cooldown_remaining <= 0 and total_value > 0:
                if rebalance_action == "Full":
                    new_shares, turnover = apply_rebalance_full(
                        shares, target_weights, prices_today, total_value, whole_shares)
                else:
                    new_shares, turnover = apply_rebalance_partial(
                        shares, target_weights, tolerances, pending_breached_tickers,
                        prices_today, total_value, whole_shares)
                shares = new_shares
                total_turnover_dollars += turnover
                rebalance_count += 1
                threshold_rebal_count += 1
                did_rebalance_today = True
                rebal_reasons.append("threshold")
                cooldown_remaining = cooldown_days

                # Recompute portfolio values after the rebalance so charts
                # reflect post-trade positions for this day
                total_value = sum(shares[tk] * prices_today[tk] for tk in tickers)
                portfolio_values[i] = total_value
                for tk in tickers:
                    ticker_values_arr[tk][i] = shares[tk] * prices_today[tk]
                event_log.append({
                    "date": dt, "reason": "threshold",
                    "breached_tickers": ", ".join(pending_breached_tickers),
                    "max_drift": round(pending_max_drift, 6),
                    "turnover_dollars": round(turnover, 2),
                })
            # Clear the pending state regardless of whether we executed
            # (cooldown may have prevented execution)
            pending_threshold_breach = False
            pending_breached_tickers = []
            pending_max_drift = 0.0

        # STEP 2: Calendar rebalance (independent of threshold — both can fire same day).
        # Calendar always uses full rebalance (all assets to target) regardless of
        # the rebalance_action setting, which only applies to threshold triggers.
        # The did_rebalance_today flag prevents double-counting: if both threshold
        # and calendar fire on the same day, the combined event counts as one in
        # rebalance_count but both are tracked separately in their specific counters.
        if enable_calendar and dt in calendar_dates and total_value > 0:
            cw_now = compute_weights(shares, prices_today)
            needs_rebal = any(abs(cw_now.get(tk, 0) - target_weights[tk]) > 1e-10 for tk in tickers)
            if needs_rebal:
                new_shares, turnover = apply_rebalance_full(
                    shares, target_weights, prices_today, total_value, whole_shares)
                shares = new_shares
                total_turnover_dollars += turnover
                # Only increment total count if threshold didn't already count today
                if not did_rebalance_today:
                    rebalance_count += 1
                calendar_rebal_count += 1
                rebal_reasons.append("calendar")
                total_value = sum(shares[tk] * prices_today[tk] for tk in tickers)
                portfolio_values[i] = total_value
                for tk in tickers:
                    ticker_values_arr[tk][i] = shares[tk] * prices_today[tk]
                reason_str = "+".join(rebal_reasons) if len(rebal_reasons) > 1 else "calendar"
                event_log.append({
                    "date": dt, "reason": reason_str,
                    "breached_tickers": "",
                    "max_drift": round(max(drift.values()) if drift else 0, 6),
                    "turnover_dollars": round(turnover, 2),
                })

        # STEP 3: End-of-day threshold check — if breached, schedule for next day.
        # We skip the last day since there's no next day to execute on.
        # Note: this checks post-rebalance weights if a rebalance happened today,
        # so a calendar rebalance that brings everything to target will NOT
        # immediately trigger a threshold breach on the same day.
        if enable_threshold and i < n_days - 1:
            cw_post = compute_weights(shares, prices_today)
            drift_post = compute_drift(cw_post, target_weights, drift_mode)
            breached = find_threshold_triggers(drift_post, tolerances)
            if breached and cooldown_remaining <= 0:
                pending_threshold_breach = True
                pending_breached_tickers = breached
                pending_max_drift = max(drift_post[tk] for tk in breached)

        # Cooldown countdown (decrements even on non-rebalance days)
        if cooldown_remaining > 0:
            cooldown_remaining -= 1

    # Assemble outputs
    avg_port_value = np.mean(portfolio_values)
    turnover_proxy = (total_turnover_dollars / avg_port_value) if avg_port_value > 0 else 0.0
    rebal_daily = pd.DataFrame(index=dates)
    rebal_daily.index.name = "PRICEDATE"
    for tk in tickers:
        rebal_daily[f"{tk} (Thresh)"] = ticker_values_arr[tk]
    rebal_daily["Portfolio Value"] = portfolio_values
    rebal_stats = {
        "rebalance_count": rebalance_count,
        "calendar_rebal_count": calendar_rebal_count,
        "threshold_rebal_count": threshold_rebal_count,
        "turnover_proxy": round(turnover_proxy, 4),
        "final_value": round(portfolio_values[-1], 2),
        "total_return": round(portfolio_values[-1] / initial_capital - 1, 6),
    }
    if event_log:
        event_log_df = pd.DataFrame(event_log)
    else:
        event_log_df = pd.DataFrame(columns=["date", "reason", "breached_tickers", "max_drift", "turnover_dollars"])
    return rebal_daily, rebal_stats, event_log_df, drift_history


# ================================================================
# V4 ADDITION: Enhanced Performance Metrics
# ================================================================

def compute_strategy_metrics(daily_values, initial_capital, benchmark_values=None):
    """
    Compute performance metrics from a daily portfolio value series.

    V4 extends the original {total_return, cagr, vol, sharpe, max_dd} with:
    - Skewness: negative skew = fatter left tail = more downside risk
    - Kurtosis (excess/Fisher): >0 means heavier tails than normal distribution
    - Avg Drawdown: mean of all daily drawdowns (not just max)
    - Tracking Error: annualized std of active returns vs benchmark
    - Information Ratio: annualized active return / tracking error

    All annualization uses 252 trading days. Sharpe uses Rf=0 (simplification
    appropriate for relative strategy comparison).
    """
    n = len(daily_values)
    if n < 2:
        return {
            "total_return": 0.0, "cagr": 0.0, "annualized_vol": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0,
            "skewness": 0.0, "kurtosis": 0.0, "avg_drawdown": 0.0,
            "tracking_error": 0.0, "information_ratio": 0.0,
        }
    final = daily_values[-1]
    total_return = final / initial_capital - 1

    # CAGR: annualized geometric return. Uses 252 trading days = 1 year.
    # The formula (final/initial)^(1/years) - 1 correctly compounds over
    # multi-year periods. For sub-year periods it extrapolates (which can
    # overstate returns for very short periods — interpret with caution).
    years = n / 252.0
    if years > 0 and final > 0 and initial_capital > 0:
        cagr = (final / initial_capital) ** (1 / years) - 1
    else:
        cagr = 0.0

    # Daily returns: simple (not log) returns. np.diff gives day-over-day price
    # changes; dividing by the previous day's value converts to percentage returns.
    # ddof=1 uses Bessel's correction for an unbiased sample standard deviation.
    daily_rets = np.diff(daily_values) / daily_values[:-1]
    ann_vol = np.std(daily_rets, ddof=1) * np.sqrt(252) if len(daily_rets) > 1 else 0.0

    # Sharpe = CAGR / Vol (risk-free rate = 0 for simplicity in relative comparison)
    sharpe = (cagr / ann_vol) if ann_vol > 0 else 0.0

    # Drawdown series: how far below the running peak at each point
    running_max = np.maximum.accumulate(daily_values)
    drawdowns = (daily_values - running_max) / running_max
    max_dd = float(np.min(drawdowns))

    # Higher-moment statistics (V4) — require minimum samples to be meaningful.
    # Skewness: negative values indicate the distribution has a longer left tail
    # (more frequent large losses). Most equity portfolios show slight negative skew.
    # Kurtosis (excess/Fisher): values > 0 mean heavier tails than a normal
    # distribution — more extreme events than a bell curve would predict.
    skewness = float(sp_stats.skew(daily_rets)) if len(daily_rets) > 2 else 0.0
    kurtosis = float(sp_stats.kurtosis(daily_rets, fisher=True)) if len(daily_rets) > 3 else 0.0
    # Average drawdown: the mean of ALL daily drawdowns (most of which are small).
    # Unlike max drawdown which is a single worst case, this captures the "typical"
    # underwater experience. A strategy with low max DD but high avg DD spends a
    # lot of time slightly underwater — important for investor psychology.
    avg_drawdown = float(np.mean(drawdowns))

    # Tracking error and information ratio: only computed when a benchmark
    # (typically buy-and-hold) is provided. These measure active return quality:
    # TE = how much the strategy's returns deviate from the benchmark day-to-day
    # IR = how efficiently the strategy generates excess return per unit of deviation
    # A high IR (> 0.5) suggests the rebalancing strategy adds consistent value.
    tracking_error = 0.0
    information_ratio = 0.0
    if benchmark_values is not None and len(benchmark_values) == n:
        bm_rets = np.diff(benchmark_values) / benchmark_values[:-1]
        # Active returns = strategy daily returns minus benchmark daily returns
        active_rets = daily_rets - bm_rets
        tracking_error = float(np.std(active_rets, ddof=1) * np.sqrt(252)) if len(active_rets) > 1 else 0.0
        if tracking_error > 1e-12:
            # Annualize active return: daily mean × 252 trading days
            ann_active_mean = float(np.mean(active_rets) * 252)
            information_ratio = ann_active_mean / tracking_error

    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "annualized_vol": round(ann_vol, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "skewness": round(skewness, 4),
        "kurtosis": round(kurtosis, 4),
        "avg_drawdown": round(avg_drawdown, 6),
        "tracking_error": round(tracking_error, 6),
        "information_ratio": round(information_ratio, 4),
    }


# ================================================================
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

start_date = date_cols[0].date_input("Start Date", value=default_start, min_value=min_date, max_value=max_date)
end_date = date_cols[1].date_input("End Date", value=default_end, min_value=min_date, max_value=max_date)

initial_capital = st.sidebar.number_input(
    "Initial Capital ($)", min_value=1_000, max_value=100_000_000,
    value=100_000, step=10_000, format="%d",
)
price_field = st.sidebar.selectbox("Price Field", ["PRICECLOSE", "PRICEMID"])
allow_cash = st.sidebar.checkbox("Whole shares only (cash residual)", value=False)

# ================================================================
# V4: Universal Page-Level Tax Parameters
# ================================================================
# These tax rates are shared across all strategy sections — the MSBA v1
# optimizer uses them directly, and they're threaded through as placeholders
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

REBAL_FREQS = ["Daily", "Weekly", "Monthly", "Quarterly"]
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
    help="Compute & compare Buy-and-Hold + all 4 calendar rebalance frequencies at once.",
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
        help="Absolute: |current_weight - target_weight|. Relative: |current_weight / target_weight - 1|.",
    )
    rebalance_action = st.sidebar.selectbox(
        "Rebalance Action", ["Full", "Partial"],
        help="Full: rebalance ALL assets to target. Partial: only trade breached assets, scale others.",
    )
    default_tolerance_pct = st.sidebar.slider(
        "Default Drift Tolerance (%)", min_value=0.5, max_value=20.0,
        value=5.0, step=0.5, key="thresh_tol",
        help="Default tolerance for all assets. Override per-asset in the main panel.",
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

# ================================================================
# MSBA v1 OPTIMIZER SIDEBAR
# ================================================================
# The optimizer sidebar only appears if the module imported successfully.
# When the optimizer toggle is off, we still define default values for
# opt_tlh_threshold and opt_div_handling so the rest of the code doesn't
# need to check enable_optimizer before accessing these variables.
# Note: V4 removed the separate optimizer tax rate inputs — they now use
# the universal global_tax_rates defined above.
if _OPTIMIZER_AVAILABLE:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### \U0001f9e0 Optimizer MSBA v1")
    enable_optimizer = st.sidebar.toggle("Enable Optimizer MSBA v1", value=False)
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
    else:
        opt_tlh_threshold = 0.05
        opt_div_handling = "Reinvest dividends"
else:
    enable_optimizer = False

# ================================================================
# TRANSACTION COST SIDEBAR
# ================================================================
st.sidebar.markdown("---")
st.sidebar.markdown("### 💸 Transaction Cost Assumptions")
st.sidebar.caption("Applied to the MSBA v1 optimizer. Used to estimate costs for calendar/threshold strategies.")

_commission_bps = st.sidebar.number_input(
    "Commission (bps/trade)", min_value=0.0, max_value=50.0, value=5.0, step=0.5,
    help="Flat execution cost in basis points of trade value (~$0.005/sh on a $100 stock = 5 bps).",
)
_slippage_bps = st.sidebar.number_input(
    "Slippage (bps)", min_value=0.0, max_value=50.0, value=5.0, step=0.5,
    help="Market impact / price improvement slippage in basis points.",
)
_bid_ask_bps = st.sidebar.number_input(
    "Bid-Ask Spread (bps, one-way)", min_value=0.0, max_value=30.0, value=2.0, step=0.5,
    help="One-way half-spread cost in basis points.",
)
_cost_config = {
    "commission_bps": _commission_bps,
    "slippage_bps": _slippage_bps,
    "bid_ask_bps": _bid_ask_bps,
}
_total_cost_rate = (_commission_bps + _slippage_bps + _bid_ask_bps) / 10_000.0

run_btn = st.sidebar.button("\U0001f680 Calculate Returns", use_container_width=True, type="primary")


# ================================================================
#  MAIN PAGE
# ================================================================
# Everything below this point only executes when the user clicks "Calculate Returns".
# st.stop() is used at multiple guard points to halt rendering early if there's
# an error — this prevents confusing partial UI states where some charts render
# but others fail.

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

if not run_btn:
    st.info("\U0001f448 Configure your portfolio in the sidebar and press **Calculate Returns**.")
    st.stop()

weight_sum = sum(weight_inputs)
if weight_sum == 0:
    st.error("All weights are zero. Please assign weights to at least one ticker.")
    st.stop()

# Run the core buy-and-hold engine — this produces the holdings table and
# summary stats that all downstream sections (charts, rebalancing, optimizer) use.
try:
    summary, holdings = calculate_portfolio_returns(
        df=df, tickers=ticker_inputs, weights=weight_inputs,
        start_date=str(start_date), end_date=str(end_date),
        initial_capital=float(initial_capital), price_field=price_field,
        allow_cash_residual=allow_cash,
    )
except ValueError as e:
    st.error(f"**Error:** {e}")
    st.stop()

if summary["tickers_dropped"] > 0:
    for tk, w, reason in summary["dropped_details"]:
        st.warning(f"\u26a0\ufe0f Dropped **{tk}** (weight {w:.2%}): {reason}")

# KPI Cards (Buy-and-Hold)
total_return = summary["portfolio_total_return"]
gain_dollars = summary["total_unrealized_gain_dollars"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Starting Value", f"${summary['portfolio_start_value']:,.0f}")
col2.metric("Ending Value", f"${summary['portfolio_end_value']:,.0f}")
col3.metric("Total Return", f"{total_return:+.2%}", delta=f"${gain_dollars:+,.0f}")
col4.metric("Unrealized Gain", f"${gain_dollars:+,.0f}", delta=f"{summary['total_unrealized_gain_pct']:+.2%}")

st.markdown("---")

# Build the buy-and-hold daily time series (used for charts and as benchmark)
daily = build_daily_series(df, holdings, float(initial_capital), price_field)
tickers_used = holdings["Ticker"].tolist()

st.subheader("Portfolio Value vs Cost Basis")
chart1_df = daily[["Portfolio Value", "Cost Basis"]].copy()
st.line_chart(chart1_df, color=["#1a73e8", "#888888"], use_container_width=True, height=380)

st.subheader("Per-Ticker Cumulative Return (%)")
return_cols = [f"{tk} Return (%)" for tk in tickers_used]
st.line_chart(_safe_chart_cols(daily[return_cols]), use_container_width=True, height=320)

# ── Portfolio Summary Statistics Table ─────────────────────────────────────
st.markdown("---")
st.subheader("Portfolio Summary Statistics")
_bh_vals = daily["Portfolio Value"].values
_bh_metrics = compute_strategy_metrics(_bh_vals, float(initial_capital))
_n_days = len(_bh_vals)
_years = _n_days / 252.0

_summary_rows = [
    ("Period", f"{str(start_date)} → {str(end_date)}"),
    ("Trading Days", f"{_n_days:,}"),
    ("Initial Capital", f"${float(initial_capital):,.0f}"),
    ("Final Value (B&H)", f"${_bh_vals[-1]:,.0f}"),
    ("Total Return", f"{_bh_metrics['total_return']:+.2%}"),
    ("CAGR", f"{_bh_metrics['cagr']:+.2%}"),
    ("Annualized Volatility", f"{_bh_metrics['annualized_vol']:.2%}"),
    ("Sharpe Ratio (Rf=0)", f"{_bh_metrics['sharpe']:.3f}"),
    ("Max Drawdown", f"{_bh_metrics['max_drawdown']:.2%}"),
    ("Avg Drawdown", f"{_bh_metrics['avg_drawdown']:.2%}"),
    ("Calmar Ratio", f"{abs(_bh_metrics['cagr'] / _bh_metrics['max_drawdown']):.3f}" if _bh_metrics['max_drawdown'] != 0 else "—"),
    ("Return Skewness", f"{_bh_metrics['skewness']:.3f}"),
    ("Excess Kurtosis", f"{_bh_metrics['kurtosis']:.3f}"),
]
_summary_df = pd.DataFrame(_summary_rows, columns=["Metric", "Value (Buy & Hold)"])
_s_col1, _s_col2 = st.columns([2, 1])
with _s_col1:
    st.dataframe(_summary_df, use_container_width=True, hide_index=True)
with _s_col2:
    excel_download_button(
        _summary_df, "portfolio_summary_stats.xlsx",
        label="Portfolio Summary Stats", sheet_name="Summary Stats",
    )

# ── Allocation Drift Table (current weights vs target) ─────────────────────
_final_values = {row["Ticker"]: row["End Value"] for _, row in holdings.iterrows()}
_port_end_val = sum(_final_values.values())
_drift_rows = []
for _, row in holdings.iterrows():
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
# REBALANCING COMPARISON SECTION (Calendar + Threshold)
# ================================================================
# This section orchestrates all rebalancing strategies and presents them
# in a unified comparison view. The wide price matrix is built once and
# shared across all strategy computations.

if enable_rebalancing:
    st.subheader("\U0001f504 Rebalancing Strategy Comparison")
    target_weights = {row["Ticker"]: row["Weight"] for _, row in holdings.iterrows()}

    try:
        all_start = pd.to_datetime(holdings["Start Date"]).min()
        all_end = pd.to_datetime(holdings["End Date"]).max()
        prices_wide = build_prices_wide(df, tickers_used, all_start, all_end, price_field)
    except ValueError as e:
        st.error(f"**Error building price matrix:** {e}")
        st.stop()

    ### Per-asset tolerance UI — allows overriding the global default tolerance
    ### for individual tickers (e.g., tighter band on a volatile small-cap)
    tolerances = {tk: default_tolerance_pct / 100.0 for tk in tickers_used}
    if enable_threshold_rebal:
        with st.expander("\U0001f4cf Advanced: Per-Asset Drift Tolerances"):
            st.caption("Override the default tolerance for individual tickers.")
            tol_cols = st.columns(min(len(tickers_used), 4))
            for idx, tk in enumerate(tickers_used):
                col_idx = idx % min(len(tickers_used), 4)
                tol_val = tol_cols[col_idx].number_input(
                    f"{tk} tol (%)", min_value=0.5, max_value=50.0,
                    value=default_tolerance_pct, step=0.5,
                    key=f"tol_{tk}", format="%.1f",
                )
                tolerances[tk] = tol_val / 100.0

    # Compute all enabled strategies
    strategy_results = {}
    event_logs = {}
    drift_histories = {}

    # Calendar strategies (V3 engine — one run per frequency)
    if enable_calendar_rebal:
        if show_all_strategies:
            freqs_to_run = REBAL_FREQS
            st.caption("Computing: Buy & Hold + all calendar frequencies" +
                       (" + Threshold" if enable_threshold_rebal else "") + "...")
        else:
            freqs_to_run = [selected_freq]
            st.caption(f"Comparing Buy & Hold vs **{selected_freq}** rebalancing" +
                       (" + Threshold" if enable_threshold_rebal else "") + ".")
        for freq in freqs_to_run:
            try:
                rd, rs = build_rebalanced_series(prices_wide, target_weights, float(initial_capital), freq)
                strategy_results[f"Rebal: {freq}"] = (rd, rs)
            except ValueError as e:
                st.warning(f"\u26a0\ufe0f Could not compute {freq} rebalancing: {e}")
    else:
        freqs_to_run = []
        if enable_threshold_rebal:
            st.caption("Comparing Buy & Hold vs **Threshold** rebalancing (no calendar).")

    # Threshold strategy (V4 engine — may include calendar combo)
    if enable_threshold_rebal:
        try:
            thresh_rd, thresh_rs, thresh_log, thresh_drift = build_threshold_rebalanced_series(
                prices_wide=prices_wide, target_weights=target_weights,
                initial_capital=float(initial_capital), tolerances=tolerances,
                drift_mode=drift_mode, rebalance_action=rebalance_action,
                cooldown_days=cooldown_days,
                calendar_freq=selected_freq if enable_calendar_rebal else None,
                enable_calendar=enable_calendar_rebal, enable_threshold=True,
                whole_shares=allow_cash,
            )
            # Label reflects whether this is threshold-only or a calendar+threshold combo
            combo_label = "Threshold" if not enable_calendar_rebal else f"Cal({selected_freq})+Thresh"
            strategy_results[combo_label] = (thresh_rd, thresh_rs)
            event_logs[combo_label] = thresh_log
            drift_histories[combo_label] = thresh_drift
        except ValueError as e:
            st.warning(f"\u26a0\ufe0f Could not compute threshold rebalancing: {e}")

    if not strategy_results and not enable_threshold_rebal and not enable_calendar_rebal:
        st.info("Enable at least one rebalancing strategy to see comparison results.")
    elif strategy_results:
        # Align all strategy series to the same date index for apples-to-apples comparison.
        # The reindex + dropna ensures we only compare days where ALL strategies have data.
        # This handles edge cases where the buy-and-hold series has slightly different
        # date coverage than the rebalanced series (due to the outer join in build_daily_series).
        comparison_df = pd.DataFrame(index=prices_wide.index)
        comparison_df.index.name = "PRICEDATE"
        # Buy-and-hold comes from the daily series built earlier; reindex aligns it
        # to the wide price matrix's trading days.
        bh_values = daily["Portfolio Value"].reindex(comparison_df.index)
        comparison_df["Buy & Hold"] = bh_values
        for label, (rd, rs) in strategy_results.items():
            comparison_df[label] = rd["Portfolio Value"].reindex(comparison_df.index)
        comparison_df = comparison_df.dropna()

        # Enhanced metrics table: each strategy benchmarked against buy-and-hold.
        # Buy-and-hold is computed with benchmark_values=None (no self-tracking-error)
        # while all other strategies pass bh_vals_arr as benchmark for TE/IR calculation.
        bh_vals_arr = comparison_df["Buy & Hold"].values
        bh_metrics = compute_strategy_metrics(bh_vals_arr, float(initial_capital), benchmark_values=None)
        # Manually inject rebalancing-specific fields that compute_strategy_metrics doesn't produce
        bh_metrics["rebalance_count"] = 0
        bh_metrics["turnover_proxy"] = 0.0

        # Collect raw (numeric) metrics for ranking and cost estimation
        metrics_raw = {}  # label → dict of raw floats

        bh_turnover_dollars = 0.0
        bh_est_cost = 0.0
        metrics_raw["Buy & Hold"] = {
            **bh_metrics,
            "rebalance_count": 0, "turnover_proxy": 0.0,
            "turnover_dollars": bh_turnover_dollars,
            "est_transaction_cost": bh_est_cost,
        }

        metrics_rows = [{
            "Strategy": "Buy & Hold",
            "Final Value ($)": f"${bh_vals_arr[-1]:,.0f}",
            "Total Return": f"{bh_metrics['total_return']:+.2%}",
            "CAGR": f"{bh_metrics['cagr']:+.2%}",
            "Ann. Vol": f"{bh_metrics['annualized_vol']:.2%}",
            "Sharpe": f"{bh_metrics['sharpe']:.3f}",
            "Max DD": f"{bh_metrics['max_drawdown']:.2%}",
            "Avg DD": f"{bh_metrics['avg_drawdown']:.2%}",
            "Skew": f"{bh_metrics['skewness']:.3f}",
            "Kurt": f"{bh_metrics['kurtosis']:.3f}",
            "TE": "—",
            "IR": "—",
            "Turnover": "0.00×",
            "Rebal Events": 0,
            "Turnover ($)": "$0",
            "Est. Cost ($)": "$0",
        }]

        for label in strategy_results:
            rd, rs = strategy_results[label]
            vals = comparison_df[label].values
            m = compute_strategy_metrics(vals, float(initial_capital), benchmark_values=bh_vals_arr)
            turnover_dollars = rs.get("total_turnover_dollars", 0.0)
            est_cost = turnover_dollars * _total_cost_rate
            metrics_raw[label] = {
                **m,
                "rebalance_count": rs["rebalance_count"],
                "turnover_proxy": rs["turnover_proxy"],
                "turnover_dollars": turnover_dollars,
                "est_transaction_cost": est_cost,
            }
            metrics_rows.append({
                "Strategy": label,
                "Final Value ($)": f"${rs['final_value']:,.0f}",
                "Total Return": f"{m['total_return']:+.2%}",
                "CAGR": f"{m['cagr']:+.2%}",
                "Ann. Vol": f"{m['annualized_vol']:.2%}",
                "Sharpe": f"{m['sharpe']:.3f}",
                "Max DD": f"{m['max_drawdown']:.2%}",
                "Avg DD": f"{m['avg_drawdown']:.2%}",
                "Skew": f"{m['skewness']:.3f}",
                "Kurt": f"{m['kurtosis']:.3f}",
                "TE": f"{m['tracking_error']:.4f}",
                "IR": f"{m['information_ratio']:.3f}",
                "Turnover": f"{rs['turnover_proxy']:.2f}×",
                "Rebal Events": rs["rebalance_count"],
                "Turnover ($)": f"${turnover_dollars:,.0f}",
                "Est. Cost ($)": f"${est_cost:,.0f}",
            })

        st.markdown("#### Performance Metrics")
        metrics_df = pd.DataFrame(metrics_rows)
        _mc1, _mc2 = st.columns([5, 1])
        with _mc1:
            st.dataframe(metrics_df, use_container_width=True, hide_index=True)
        with _mc2:
            excel_download_button(
                metrics_df, "strategy_comparison.xlsx",
                label="Strategy Comparison", sheet_name="Metrics",
            )
        st.caption(
            f"Est. Cost = Turnover ($) × {_total_cost_rate*10000:.0f} bps "
            f"({_commission_bps:.0f} commission + {_slippage_bps:.0f} slippage + {_bid_ask_bps:.0f} bid-ask). "
            "Sharpe uses Rf=0."
        )

        # ── Strategy Ranking Table ──────────────────────────────────────────
        # Composite scoring: each strategy ranked across 6 dimensions.
        # Higher CAGR, higher Sharpe, higher IR = better → rank ascending (1=best).
        # Lower Max DD, lower Turnover, lower Est. Cost = better → rank ascending.
        # Equal weight per dimension. Ties broken by sum of raw ranks.
        st.markdown("#### Strategy Ranking")
        _rank_labels = list(metrics_raw.keys())
        _rank_dims = {
            "CAGR (↑)": ("cagr", True),
            "Sharpe (↑)": ("sharpe", True),
            "Max DD (↓)": ("max_drawdown", False),
            "IR vs B&H (↑)": ("information_ratio", True),
            "Turnover (↓)": ("turnover_proxy", False),
            "Est. Cost $ (↓)": ("est_transaction_cost", False),
        }
        _rank_data: Dict[str, dict] = {lbl: {} for lbl in _rank_labels}
        for dim_name, (key, higher_is_better) in _rank_dims.items():
            vals_for_dim = [(lbl, metrics_raw[lbl].get(key, 0.0)) for lbl in _rank_labels]
            vals_for_dim.sort(key=lambda x: x[1], reverse=higher_is_better)
            for rank, (lbl, _) in enumerate(vals_for_dim, 1):
                _rank_data[lbl][dim_name] = rank
        for lbl in _rank_labels:
            _rank_data[lbl]["Composite Score"] = sum(_rank_data[lbl].values())
        _rank_rows = []
        for lbl in sorted(_rank_labels, key=lambda x: _rank_data[x]["Composite Score"]):
            row = {"Strategy": lbl, **_rank_data[lbl]}
            _rank_rows.append(row)
        _rank_df = pd.DataFrame(_rank_rows)
        _rk1, _rk2 = st.columns([4, 1])
        with _rk1:
            st.dataframe(_rank_df, use_container_width=True, hide_index=True)
        with _rk2:
            excel_download_button(
                _rank_df, "strategy_ranking.xlsx",
                label="Strategy Ranking", sheet_name="Ranking",
            )
        st.caption("Composite Score = sum of per-dimension ranks. **Lower score = better overall.** Ranks 1=best within each dimension.")

        # ── Transaction Cost Breakdown Table ────────────────────────────────
        st.markdown("#### Transaction Cost Breakdown")
        _cost_rows = []
        for lbl in _rank_labels:
            mr = metrics_raw[lbl]
            _cost_rows.append({
                "Strategy": lbl,
                "Rebal Events": int(mr["rebalance_count"]),
                "Turnover ($)": f"${mr['turnover_dollars']:,.0f}",
                "Turnover Ratio": f"{mr['turnover_proxy']:.2f}×",
                "Est. Commission ($)": f"${mr['turnover_dollars'] * _commission_bps / 10000:,.0f}",
                "Est. Slippage ($)": f"${mr['turnover_dollars'] * _slippage_bps / 10000:,.0f}",
                "Est. Bid-Ask ($)": f"${mr['turnover_dollars'] * _bid_ask_bps / 10000:,.0f}",
                "Total Est. Cost ($)": f"${mr['est_transaction_cost']:,.0f}",
                "Cost as % of Capital": f"{mr['est_transaction_cost'] / float(initial_capital):.3%}",
            })
        _cost_df = pd.DataFrame(_cost_rows)
        _cc1, _cc2 = st.columns([4, 1])
        with _cc1:
            st.dataframe(_cost_df, use_container_width=True, hide_index=True)
        with _cc2:
            excel_download_button(
                _cost_df, "transaction_cost_breakdown.xlsx",
                label="Transaction Costs", sheet_name="Costs",
            )

        # ── Drawdown Summary Table ───────────────────────────────────────────
        st.markdown("#### Drawdown Summary")
        _dd_rows = []
        for col in comparison_df.columns:
            vals = comparison_df[col].values
            dates = comparison_df.index
            rm = np.maximum.accumulate(vals)
            dd = (vals - rm) / rm
            max_dd_idx = int(np.argmin(dd))
            # Find the peak before max drawdown
            peak_idx = int(np.argmax(vals[:max_dd_idx + 1]))
            # Recovery: first date after trough where value >= peak
            peak_val = vals[peak_idx]
            recovery_idx = None
            for j in range(max_dd_idx, len(vals)):
                if vals[j] >= peak_val:
                    recovery_idx = j
                    break
            dd_duration = max_dd_idx - peak_idx
            recovery_duration = (recovery_idx - max_dd_idx) if recovery_idx is not None else None
            _dd_rows.append({
                "Strategy": col,
                "Max Drawdown": f"{dd[max_dd_idx]:.2%}",
                "Avg Drawdown": f"{np.mean(dd):.2%}",
                "Peak Date": dates[peak_idx].strftime("%Y-%m-%d"),
                "Trough Date": dates[max_dd_idx].strftime("%Y-%m-%d"),
                "Recovery Date": dates[recovery_idx].strftime("%Y-%m-%d") if recovery_idx is not None else "Not recovered",
                "Days to Trough": dd_duration,
                "Days to Recover": recovery_duration if recovery_duration is not None else "—",
            })
        _dd_df = pd.DataFrame(_dd_rows)
        _ddc1, _ddc2 = st.columns([5, 1])
        with _ddc1:
            st.dataframe(_dd_df, use_container_width=True, hide_index=True)
        with _ddc2:
            excel_download_button(
                _dd_df, "drawdown_summary.xlsx",
                label="Drawdown Summary", sheet_name="Drawdowns",
            )

        # KPI cards: headline comparison of first strategy vs buy-and-hold.
        primary_label = list(strategy_results.keys())[0]
        _, primary_stats = strategy_results[primary_label]
        bh_final = bh_vals_arr[-1]
        rb_final = primary_stats["final_value"]
        rb_return = primary_stats["total_return"]
        bh_ret = bh_metrics["total_return"]

        rc1, rc2, rc3, rc4 = st.columns(4)
        rc1.metric(f"{primary_label} Final", f"${rb_final:,.0f}", delta=f"{rb_return:+.2%}")
        rc2.metric("Buy-and-Hold Final", f"${bh_final:,.0f}", delta=f"{bh_ret:+.2%}")
        rc3.metric("Strategy Advantage", f"${rb_final - bh_final:+,.0f}", delta=f"{(rb_return - bh_ret):+.4%}")
        rc4.metric("Rebalance Events", f"{primary_stats['rebalance_count']:,}", delta=f"Turnover: {primary_stats['turnover_proxy']:.2f}")

        # Portfolio value chart with strategy toggle
        st.markdown("#### Portfolio Value Over Time")
        # Color mapping: each strategy type gets a distinct color for visual clarity.
        # Blue for B&H (the stable baseline), warm colors for calendar frequencies
        # (orange/green/purple/red in order of increasing interval), and amber for
        # threshold to distinguish it from all calendar strategies.
        freq_color_map = {"Daily": "#e8710a", "Weekly": "#34a853", "Monthly": "#9c27b0", "Quarterly": "#ea4335"}
        threshold_color = "#ffab00"

        # Build a color map keyed by strategy label for consistent coloring.
        # Strategy labels follow patterns: "Buy & Hold", "Rebal: Monthly", "Threshold",
        # or "Cal(Monthly)+Thresh" — the prefix determines which color pool to use.
        _all_strat_labels = list(comparison_df.columns)
        _color_map = {}
        _color_map["Buy & Hold"] = "#1a73e8"
        for label in strategy_results:
            if label.startswith("Rebal:"):
                freq_key = label.replace("Rebal: ", "")
                _color_map[label] = freq_color_map.get(freq_key, "#666666")
            else:
                _color_map[label] = threshold_color

        # Multiselect lets users toggle individual strategies on/off in the chart
        selected_strats = st.multiselect(
            "Strategies to display",
            options=_all_strat_labels,
            default=_all_strat_labels,
            key="value_chart_strats",
        )

        if selected_strats:
            _chart_df = comparison_df[selected_strats]
            _chart_colors = [_color_map.get(s, "#666666") for s in selected_strats]
            st.line_chart(_safe_chart_cols(_chart_df), color=_chart_colors, use_container_width=True, height=420)
        else:
            st.info("Select at least one strategy to display.")

        strategy_colors = [_color_map.get(c, "#666666") for c in comparison_df.columns]

        # Drawdown chart: shows peak-to-trough declines over time for all strategies.
        # Drawdown at day i = (value_i - max_value_up_to_i) / max_value_up_to_i × 100.
        # Values are always ≤ 0 (0% = at peak, -10% = 10% below peak).
        # Area chart makes periods of drawdown visually prominent.
        show_drawdown = st.checkbox("Show Drawdown Chart", value=False)
        if show_drawdown:
            st.markdown("#### Drawdown Over Time")
            dd_df = pd.DataFrame(index=comparison_df.index)
            for col in comparison_df.columns:
                vals = comparison_df[col].values
                # Running max = the highest portfolio value seen up to each point
                rm = np.maximum.accumulate(vals)
                dd_df[col] = ((vals - rm) / rm) * 100
            st.area_chart(_safe_chart_cols(dd_df), color=strategy_colors, use_container_width=True, height=300)

        # Difference chart: visual of strategy advantage/disadvantage vs buy-and-hold
        if primary_label in comparison_df.columns:
            diff_series = comparison_df[primary_label] - comparison_df["Buy & Hold"]
            diff_chart = pd.DataFrame({f"{primary_label} vs B&H ($)": diff_series})
            advantage_color = "#34a853" if diff_series.iloc[-1] >= 0 else "#ea4335"
            st.area_chart(_safe_chart_cols(diff_chart), color=[advantage_color], use_container_width=True, height=200)

        ### DRIFT DIAGNOSTICS ###
        # Only shown when threshold rebalancing produced drift history data.
        # Provides per-ticker drift distribution analysis to help users calibrate
        # their tolerance bands — are they too tight (constant rebalancing) or
        # too loose (drift never triggers)?
        if drift_histories:
            st.markdown("---")
            st.markdown("#### \U0001f4ca Drift Diagnostics")
            drift_strategy_options = list(drift_histories.keys())

            # Persist dropdown selections across reruns via session_state so the
            # UI doesn't reset every time Streamlit re-executes
            if "drift_strat_idx" not in st.session_state:
                st.session_state["drift_strat_idx"] = 0
            if "drift_ticker_idx" not in st.session_state:
                st.session_state["drift_ticker_idx"] = 0

            # Clamp indices to valid range (strategies/tickers may change between runs)
            _strat_idx = min(st.session_state["drift_strat_idx"], len(drift_strategy_options) - 1)
            _ticker_idx = min(st.session_state["drift_ticker_idx"], len(tickers_used) - 1)

            selected_drift_strategy = st.selectbox(
                "Select strategy for drift analysis",
                drift_strategy_options,
                index=_strat_idx,
                key="drift_strat_select",
            )
            st.session_state["drift_strat_idx"] = drift_strategy_options.index(selected_drift_strategy)

            dh = drift_histories[selected_drift_strategy]

            drift_ticker_select = st.selectbox(
                "Select ticker for drift distribution",
                tickers_used,
                index=_ticker_idx,
                key="drift_ticker_select",
            )
            st.session_state["drift_ticker_idx"] = tickers_used.index(drift_ticker_select)

            drift_values = np.array(dh[drift_ticker_select])
            if len(drift_values) > 0:
                # Display drift stats as percentages for readability
                drift_pct = drift_values * 100.0
                tol_for_tk = tolerances.get(drift_ticker_select, default_tolerance_pct / 100.0)
                # "Days Breached" tells users what % of trading days this ticker
                # would have exceeded its tolerance — a key input for calibration
                breach_pct = np.mean(drift_values > tol_for_tk) * 100

                ds1, ds2, ds3, ds4 = st.columns(4)
                ds1.metric(f"Mean Drift ({drift_mode})", f"{np.mean(drift_pct):.2f}%")
                ds2.metric("P95 Drift", f"{np.percentile(drift_pct, 95):.2f}%")
                ds3.metric("Max Drift", f"{np.max(drift_pct):.2f}%")
                ds4.metric("Days Breached (%)", f"{breach_pct:.1f}%")

                # Histogram of daily drift values — bin count scales with data length
                # to avoid either too few bins (hiding distribution shape with <1yr data)
                # or too many (noisy with multi-year data). The 15-30 range works well
                # for typical simulation lengths of 252-1260 trading days.
                n_bins = min(30, max(15, len(drift_pct) // 15))
                counts, bin_edges = np.histogram(drift_pct, bins=n_bins)
                bin_labels = [
                    f"{bin_edges[j]:.2f}-{bin_edges[j+1]:.2f}"
                    for j in range(len(counts))
                ]
                hist_df = pd.DataFrame({
                    "Drift_pct": bin_labels,
                    "Days_count": counts,
                }).set_index("Drift_pct")
                st.bar_chart(hist_df, use_container_width=True, height=250)
                st.caption(
                    f"Distribution of daily {drift_mode.lower()} drift (%) for **{drift_ticker_select}** "
                    f"under **{selected_drift_strategy}**. Tolerance = {tol_for_tk:.2%}."
                )

            # Optional time series view — shows drift evolution over the full period
            show_drift_ts = st.checkbox("Show drift time series (all tickers)", value=False)
            if show_drift_ts:
                drift_ts_data = {tk: np.array(vals) * 100.0 for tk, vals in dh.items()}
                drift_ts_df = pd.DataFrame(drift_ts_data, index=prices_wide.index[:len(list(dh.values())[0])])
                drift_ts_df.index.name = "PRICEDATE"
                st.line_chart(drift_ts_df, use_container_width=True, height=300)
                st.caption(f"Daily {drift_mode.lower()} drift (%) per ticker under **{selected_drift_strategy}**.")

        ### EVENT LOG — structured record of every rebalance event ###
        if event_logs:
            # Summary table: aggregate statistics per strategy
            _elog_summary = []
            _elog_sheets = {}
            for label, log_df in event_logs.items():
                if not log_df.empty and "turnover_dollars" in log_df.columns:
                    total_to = log_df["turnover_dollars"].sum()
                    avg_to = log_df["turnover_dollars"].mean()
                    _elog_summary.append({
                        "Strategy": label,
                        "Total Events": len(log_df),
                        "Total Turnover ($)": f"${total_to:,.0f}",
                        "Avg Turnover/Event ($)": f"${avg_to:,.0f}",
                        "Threshold Events": int((log_df["reason"] == "threshold").sum()) if "reason" in log_df.columns else "—",
                        "Calendar Events": int((log_df["reason"] == "calendar").sum()) if "reason" in log_df.columns else "—",
                    })
                else:
                    _elog_summary.append({"Strategy": label, "Total Events": 0, "Total Turnover ($)": "$0",
                                          "Avg Turnover/Event ($)": "$0", "Threshold Events": 0, "Calendar Events": 0})
                _elog_sheets[label[:28]] = log_df

            if _elog_summary:
                _elog_sum_df = pd.DataFrame(_elog_summary)
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

            with st.expander("📋 Full Rebalance Event Log"):
                for label, log_df in event_logs.items():
                    st.markdown(f"**{label}**")
                    if not log_df.empty:
                        st.dataframe(log_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No rebalance events triggered.")

        with st.expander("ℹ️ Rebalancing Engine Notes"):
            st.markdown(f"""
**Calendar:** Rebalances on schedule (Daily / Weekly / Monthly / Quarterly) at closing prices. No transaction costs applied to NAV — see the Est. Cost column in the tables above.

**Threshold (Drift-Band):** Breach detected at close → executes next trading day (no look-ahead). Cooldown suppresses re-triggers for N days post-rebalance.

**Transaction Cost Estimation:** Commission {_commission_bps:.0f} bps + Slippage {_slippage_bps:.0f} bps + Bid-Ask {_bid_ask_bps:.0f} bps = **{_total_cost_rate*10000:.0f} bps total** applied to turnover dollars. Adjust rates in the sidebar.

**MSBA v1 Optimizer:** Full lot-level accounting — costs are actually deducted from cash (not estimated).

**Sharpe Ratio:** Risk-free rate = 0 throughout for strategy comparison consistency.
            """)

    st.markdown("---")


# ================================================================
# MSBA v1 OPTIMIZER SECTION
# ================================================================
# This section runs two parallel simulations through the tax-aware engine
# from optimizer_msba_v1_engine.py:
#   1. Static (static=True): buy-and-hold with TLH only (no rebalancing).
#      This isolates the value of tax-loss harvesting alone.
#   2. Optimized (static=False): scheduled rebalancing + TLH + tax-aware lot disposal.
#      This shows the combined benefit of active management + TLH.
#
# Both simulations use IDENTICAL parameters (same tickers, weights, dates, tax
# rates, TLH threshold) — the only difference is the static flag. This ensures
# the comparison is apples-to-apples: the "Optimizer Advantage" KPI card
# directly shows the incremental value of rebalancing on top of TLH.
#
# Key architectural difference from the base engine: the optimizer creates actual
# Portfolio objects with lot-level accounting, cash balances, and realized gain
# tracking — it's a portfolio ACCOUNTING engine, not just a valuation engine.

if enable_optimizer:
    st.subheader("\U0001f9e0 Optimizer MSBA v1 \u2014 Tax-Aware Simulation")

    # Attempt to load dividend data. The dividend CSV may not exist in all
    # deployments (it's optional — the optimizer runs fine without it, just
    # without dividend processing). If present, it requires a TICKERSYMBOL
    # column or a TRADINGITEMID column that can be mapped to ticker symbols
    # using the price dataset. The TRADINGITEMID → TICKERSYMBOL mapping is
    # necessary because the raw dividend data from Capital IQ uses
    # TRADINGITEMID as the primary key, not the human-readable ticker symbol.
    _div_df = None
    try:
        import os
        _div_path = os.path.join(os.path.dirname(__file__), "dividend_data.csv")
        if os.path.exists(_div_path):
            _div_df = pd.read_csv(_div_path)
            _div_df["PAYDATE"] = pd.to_datetime(_div_df["PAYDATE"], errors="coerce")
            _div_df["EXDATE"] = pd.to_datetime(_div_df["EXDATE"], errors="coerce")
            if "TICKERSYMBOL" not in _div_df.columns:
                if "TRADINGITEMID" in _div_df.columns and "TRADINGITEMID" in df.columns:
                    _ticker_map = (
                        df[["TRADINGITEMID", "TICKERSYMBOL"]]
                        .drop_duplicates()
                        .set_index("TRADINGITEMID")["TICKERSYMBOL"]
                        .to_dict()
                    )
                    _div_df["TICKERSYMBOL"] = _div_df["TRADINGITEMID"].map(_ticker_map)
                    _div_df = _div_df.dropna(subset=["TICKERSYMBOL"])
    except Exception:
        _div_df = None

    _opt_tax_rates = global_tax_rates  # V4: use universal tax rates (not optimizer-specific)
    _opt_reinvest = opt_div_handling == "Reinvest dividends"
    _opt_tickers = holdings["Ticker"].tolist()
    _opt_weights = holdings["Weight"].tolist()
    # Pass the calendar frequency to the optimizer. If calendar rebalancing is
    # disabled in the main panel, pass "None" so the optimizer runs as pure
    # buy-and-hold (the static run) or TLH-only (both runs skip rebalancing).
    _opt_rebal_freq = selected_freq if enable_calendar_rebal else "None"

    with st.spinner("Running MSBA v1 Static simulation..."):
        try:
            static_result = run_optimizer_simulation(
                prices_df=df, dividends_df=_div_df,
                tickers=_opt_tickers, weights=_opt_weights,
                start_date=str(start_date), end_date=str(end_date),
                rebalance_frequency=_opt_rebal_freq,
                tax_rates=_opt_tax_rates, tlh_threshold=opt_tlh_threshold,
                reinvest_dividends=_opt_reinvest,
                initial_capital=float(initial_capital),
                price_field=price_field, static=True, cost_config=_cost_config,
            )
        except Exception as e:
            st.error(f"MSBA v1 Static simulation failed: {e}")
            static_result = None

    with st.spinner("Running MSBA v1 Optimized simulation..."):
        try:
            opt_result = run_optimizer_simulation(
                prices_df=df, dividends_df=_div_df,
                tickers=_opt_tickers, weights=_opt_weights,
                start_date=str(start_date), end_date=str(end_date),
                rebalance_frequency=_opt_rebal_freq,
                tax_rates=_opt_tax_rates, tlh_threshold=opt_tlh_threshold,
                reinvest_dividends=_opt_reinvest,
                initial_capital=float(initial_capital),
                price_field=price_field, static=False, cost_config=_cost_config,
            )
        except Exception as e:
            st.error(f"MSBA v1 Optimized simulation failed: {e}")
            opt_result = None

    if static_result and opt_result:
        s_nav = static_result["nav_series"]
        o_nav = opt_result["nav_series"]
        s_final = s_nav.iloc[-1]
        o_final = o_nav.iloc[-1]
        cap = float(initial_capital)

        kc1, kc2, kc3, kc4 = st.columns(4)
        kc1.metric("Static Final NAV", f"${s_final:,.0f}", delta=f"{(s_final/cap - 1):+.2%}")
        kc2.metric("Optimized Final NAV", f"${o_final:,.0f}", delta=f"{(o_final/cap - 1):+.2%}")
        kc3.metric("Optimizer Advantage", f"${o_final - s_final:+,.0f}", delta=f"{((o_final - s_final)/cap):+.4%}")
        kc4.metric("Total Tax Paid (Opt)", f"${opt_result['tax_paid_total']:,.0f}",
                   delta=f"Static: ${static_result['tax_paid_total']:,.0f}")

        st.markdown("#### MSBA v1 — Portfolio NAV Over Time")
        opt_chart = pd.DataFrame({"Static (TLH only)": s_nav, "Optimized (Rebal + TLH)": o_nav}).dropna()
        st.line_chart(_safe_chart_cols(opt_chart), color=["#888888", "#e8710a"], use_container_width=True, height=380)

        # ── TLH + Tax Summary Table ──────────────────────────────────────────
        st.markdown("#### TLH & Tax Summary")
        _tlh_rows = []
        for _lbl, _res in [("Static (TLH only)", static_result), ("Optimized (Rebal+TLH)", opt_result)]:
            _rdf_tmp = _res.get("realized_df", pd.DataFrame())
            _losses_harvested = _res.get("losses_harvested", 0.0)
            _tlh_events = 0
            if not _rdf_tmp.empty and "gain_loss" in _rdf_tmp.columns:
                _tlh_events = int((_rdf_tmp["gain_loss"] < 0).sum())
            _tax_saved = _losses_harvested * _opt_tax_rates.get("st_rate", 0.35)
            _tx_cost = _res.get("transaction_costs_total", 0.0)
            _net_benefit = _tax_saved - _tx_cost - _res.get("tax_paid_total", 0.0)
            _tlh_rows.append({
                "Scenario": _lbl,
                "TLH Events (loss lots)": _tlh_events,
                "Total Losses Harvested ($)": f"${_losses_harvested:,.0f}",
                "Est. Tax Savings ($)": f"${_tax_saved:,.0f}",
                "Tax Paid ($)": f"${_res.get('tax_paid_total', 0.0):,.0f}",
                "Exec Costs ($)": f"${_tx_cost:,.0f}",
                "Net Benefit ($)": f"${_net_benefit:+,.0f}",
                "Final NAV ($)": f"${_res['nav_series'].iloc[-1]:,.0f}",
            })
        _tlh_df = pd.DataFrame(_tlh_rows)
        _tl1, _tl2 = st.columns([4, 1])
        with _tl1:
            st.dataframe(_tlh_df, use_container_width=True, hide_index=True)
        with _tl2:
            excel_download_button(
                _tlh_df, "tlh_tax_summary.xlsx",
                label="TLH & Tax Summary", sheet_name="TLH Summary",
            )
        st.caption("Net Benefit = Est. Tax Savings − Tax Paid − Execution Costs. Positive = TLH added value.")

        # ── Realized Gains Summary ──────────────────────────────────────────
        with st.expander("📋 Optimized Portfolio — Realized Gains"):
            _rdf = opt_result["realized_df"]
            if not _rdf.empty:
                _rg1, _rg2 = st.columns([5, 1])
                with _rg1:
                    st.dataframe(_rdf, use_container_width=True, hide_index=True)
                with _rg2:
                    excel_download_button(
                        _rdf, "realized_gains.xlsx",
                        label="Realized Gains", sheet_name="Realized",
                    )
            else:
                st.info("No realized gains/losses.")

        with st.expander("📋 Optimized Portfolio — Trade Log"):
            _tdf = opt_result["trades_df"]
            if not _tdf.empty:
                _tr1, _tr2 = st.columns([5, 1])
                with _tr1:
                    st.dataframe(_tdf, use_container_width=True, hide_index=True)
                with _tr2:
                    excel_download_button(
                        _tdf, "trade_log.xlsx",
                        label="Trade Log", sheet_name="Trades",
                    )
            else:
                st.info("No trades recorded.")

    st.markdown("---")

# ================================================================
#  HOLDINGS TABLE
# ================================================================
# Format the holdings DataFrame for human-readable display. The raw holdings
# DataFrame has numeric values; here we convert to formatted strings with
# currency symbols, percentage signs, and proper alignment. This is purely
# a presentation layer — the underlying data has already been consumed by
# all engine functions above.

st.subheader("Per-Holding Detail")

display_df = holdings.copy()
display_df["Weight"] = display_df["Weight"].apply(lambda x: f"{x:.1%}")
display_df["Return"] = display_df["Return"].apply(lambda x: f"{x:+.2%}")
display_df["Gain (%)"] = display_df["Gain (%)"].apply(lambda x: f"{x:+.2%}")
display_df["Gain ($)"] = display_df["Gain ($)"].apply(lambda x: f"${x:+,.2f}")
display_df["Start Value"] = display_df["Start Value"].apply(lambda x: f"${x:,.2f}")
display_df["End Value"] = display_df["End Value"].apply(lambda x: f"${x:,.2f}")
display_df["Start Price"] = display_df["Start Price"].apply(lambda x: f"${x:.2f}")
display_df["End Price"] = display_df["End Price"].apply(lambda x: f"${x:.2f}")

_h1, _h2 = st.columns([5, 1])
with _h1:
    st.dataframe(display_df, use_container_width=True, hide_index=True)
with _h2:
    excel_download_button(
        holdings, "holdings_detail.xlsx",
        label="Holdings Detail", sheet_name="Holdings",
    )

# ================================================================
#  ASSUMPTIONS EXPANDER
# ================================================================

with st.expander("ℹ️ Assumptions & Methodology"):
    st.markdown(f"""
**Data:** Status 1 (active) and 15 (suspended-but-valid) rows only. Start date shifts forward; end date shifts backward to nearest trading day.

**Returns:** Price-only (no dividends, no splits in base engine). MSBA v1 optimizer supports dividends via dividend_data.csv.

**Fractional Shares:** Default. Toggle "Whole shares only" for integer shares with cash residual.

**Transaction Costs (Calendar/Threshold engines):** Estimated at {_total_cost_rate*10000:.0f} bps of turnover (sidebar-configurable). **Not deducted from NAV** — shown as a cost estimate in the tables.

**Transaction Costs (MSBA v1 Optimizer):** Deducted from cash on every trade. Commission {_commission_bps:.0f} bps + Slippage {_slippage_bps:.0f} bps + Bid-Ask {_bid_ask_bps:.0f} bps.

**Tax:** ST={global_st_rate:.0%} / LT={global_lt_rate:.0%}. Applied in MSBA v1 optimizer with lot-level tracking and carry-forward netting. Calendar/threshold engines use the rates as an estimation reference only.

**Sharpe Ratio:** Risk-free rate = 0 throughout.
    """)
