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
    # TASK 1 FIX: total_turnover_dollars was missing here, causing transaction cost
    # estimates to show $0 for all calendar rebalancing strategies. Added explicitly.
    rebal_stats = {
        "rebalance_count": rebalance_count,
        "turnover_proxy": round(turnover_proxy, 4),
        "final_value": round(portfolio_values[-1], 2),
        "total_return": round(portfolio_values[-1] / initial_capital - 1, 6),
        "total_turnover_dollars": round(total_turnover_dollars, 2),
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
    # TASK 1 FIX: total_turnover_dollars added (was missing, caused $0 cost estimates)
    rebal_stats = {
        "rebalance_count": rebalance_count,
        "calendar_rebal_count": calendar_rebal_count,
        "threshold_rebal_count": threshold_rebal_count,
        "turnover_proxy": round(turnover_proxy, 4),
        "final_value": round(portfolio_values[-1], 2),
        "total_return": round(portfolio_values[-1] / initial_capital - 1, 6),
        "total_turnover_dollars": round(total_turnover_dollars, 2),
    }
    if event_log:
        event_log_df = pd.DataFrame(event_log)
    else:
        event_log_df = pd.DataFrame(columns=["date", "reason", "breached_tickers", "max_drift", "turnover_dollars"])
    return rebal_daily, rebal_stats, event_log_df, drift_history


# ================================================================
# V4 ADDITION: Enhanced Performance Metrics
# ================================================================

def compute_strategy_metrics(daily_values, initial_capital, benchmark_values=None, dates=None):
    """
    Compute performance metrics from a daily portfolio value series.

    V4 extends the original {total_return, cagr, vol, sharpe, max_dd} with:
    - Skewness: negative skew = fatter left tail = more downside risk
    - Kurtosis (excess/Fisher): >0 means heavier tails than normal distribution
    - Avg Drawdown: mean of all daily drawdowns (not just max)
    - Tracking Error: annualized std of active returns vs benchmark
    - Information Ratio: annualized active return / tracking error

    TASK 1 FIX — CAGR time-horizon correction:
    - When `dates` is provided (DatetimeIndex or list of timestamps), CAGR uses
      actual calendar days elapsed ÷ 365.25 for the year fraction. This avoids
      the 252-trading-day approximation error which understates years on short
      periods (e.g. a 3-month Q1 with 63 trading days: 63/252 = 0.25 years,
      but the calendar says 90/365.25 = 0.246 years — small but consistent).
    - Without dates, we use (n-1)/252 rather than n/252. n data points have
      n-1 return intervals; using n overcounts elapsed time by one day, making
      CAGR fractionally too low for short periods.
    - Annualized vol and Sharpe always use 252 (industry standard for daily data).

    All Sharpe calculations use Rf=0 (appropriate for relative strategy comparison).
    """
    n = len(daily_values)
    if n < 2:
        return {
            "total_return": 0.0, "cagr": 0.0, "annualized_vol": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0,
            "skewness": 0.0, "kurtosis": 0.0, "avg_drawdown": 0.0,
            "tracking_error": 0.0, "information_ratio": 0.0,
            "trading_days": n,
        }
    # Guard: replace any NaN/inf in daily_values so downstream math is clean
    daily_values = np.where(np.isfinite(daily_values), daily_values, initial_capital)
    final = daily_values[-1]
    # Prevent divide-by-zero if initial_capital is zero or portfolio collapses
    if initial_capital <= 0 or final <= 0:
        total_return = 0.0
    else:
        total_return = final / initial_capital - 1

    # ── CAGR calculation ─────────────────────────────────────────────────────
    # Prefer actual calendar days when dates are available (more accurate for
    # sub-year and exact-year periods). Fall back to (n-1)/252 otherwise.
    # Using (n-1) instead of n: n data points → n-1 elapsed day intervals.
    if dates is not None and len(dates) >= 2:
        # Actual calendar elapsed time avoids the 252-day approximation
        try:
            t0 = pd.Timestamp(dates[0])
            t1 = pd.Timestamp(dates[-1])
            calendar_days = (t1 - t0).days
            # 365.25 accounts for leap years; minimum 1 day to avoid div-zero
            years = max(calendar_days, 1) / 365.25
        except Exception:
            # Fallback if date parsing fails
            years = max(n - 1, 1) / 252.0
    else:
        # (n-1) intervals / 252 trading days per year
        years = max(n - 1, 1) / 252.0

    if years > 0 and final > 0 and initial_capital > 0:
        cagr = (final / initial_capital) ** (1.0 / years) - 1
    else:
        cagr = 0.0

    # ── Daily returns ─────────────────────────────────────────────────────────
    # Simple (not log) returns. np.diff gives day-over-day changes; dividing by
    # the previous day's value gives percentage returns. ddof=1 = Bessel correction
    # for unbiased sample std. Guard against zero-price days with np.where.
    prev_vals = daily_values[:-1]
    # Replace zeros in denominator with 1 (return = 0 on that day, not undefined)
    safe_prev = np.where(prev_vals > 0, prev_vals, 1.0)
    daily_rets = np.diff(daily_values) / safe_prev
    # Drop any remaining NaN/inf that could propagate through statistics
    daily_rets = daily_rets[np.isfinite(daily_rets)]

    ann_vol = np.std(daily_rets, ddof=1) * np.sqrt(252) if len(daily_rets) > 1 else 0.0

    # Sharpe = CAGR / Vol (Rf=0 for cross-strategy comparison consistency)
    sharpe = (cagr / ann_vol) if ann_vol > 0 else 0.0

    # ── Drawdown series ────────────────────────────────────────────────────────
    # running_max tracks the historical high-water mark at each day.
    # drawdowns[i] = (value[i] - peak_value) / peak_value ≤ 0
    running_max = np.maximum.accumulate(daily_values)
    # Guard: avoid divide-by-zero if running_max ever hits zero
    safe_rm = np.where(running_max > 0, running_max, 1.0)
    drawdowns = (daily_values - running_max) / safe_rm
    max_dd = float(np.min(drawdowns))

    # ── Higher moments ─────────────────────────────────────────────────────────
    # Skewness: negative = fatter left tail = more frequent large losses.
    # Kurtosis (excess/Fisher): >0 = heavier tails than a normal distribution.
    # Require minimum sample sizes before computing (scipy returns NaN otherwise).
    skewness = float(sp_stats.skew(daily_rets)) if len(daily_rets) > 2 else 0.0
    kurtosis = float(sp_stats.kurtosis(daily_rets, fisher=True)) if len(daily_rets) > 3 else 0.0

    # Average drawdown: mean of ALL daily drawdowns, not just the worst.
    # Captures the "typical" underwater experience — a strategy with low max DD
    # but high avg DD spends a lot of time slightly underwater.
    avg_drawdown = float(np.mean(drawdowns))

    # ── Tracking error & information ratio ────────────────────────────────────
    # Only computed when a benchmark (typically buy-and-hold) is provided.
    # TE = annualized std of daily active returns vs benchmark
    # IR = annualized excess return / TE. IR > 0.5 generally indicates
    # the active strategy adds consistent value beyond random variation.
    tracking_error = 0.0
    information_ratio = 0.0
    if benchmark_values is not None and len(benchmark_values) == n:
        bm_vals = np.where(np.isfinite(benchmark_values), benchmark_values, initial_capital)
        bm_prev = bm_vals[:-1]
        safe_bm_prev = np.where(bm_prev > 0, bm_prev, 1.0)
        bm_rets = np.diff(bm_vals) / safe_bm_prev
        # Align lengths after NaN-filtering (use min length to stay in sync)
        active_rets = daily_rets[:len(bm_rets)] - bm_rets[:len(daily_rets)]
        active_rets = active_rets[np.isfinite(active_rets)]
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
        # trading_days exposed so callers can display the period length
        "trading_days": n,
        # years_used: for debugging / validating CAGR time horizon
        "years_used": round(years, 4),
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
price_field = st.sidebar.selectbox("Price Field", ["PRICECLOSE", "PRICEMID"])
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
        ("Calmar Ratio", f"{abs(_bh_metrics['cagr'] / _bh_metrics['max_drawdown']):.3f}" if _bh_metrics['max_drawdown'] != 0 else "—"),
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
                    _rd, _rs = build_rebalanced_series(_prices_wide, _target_weights, float(initial_capital), _freq)
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
                    whole_shares=allow_cash,
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
                        whole_shares=allow_cash,
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
        except Exception:
            _div_df_opt = None

        _opt_tax_rates = global_tax_rates
        _opt_reinvest = opt_div_handling == "Reinvest dividends"
        _opt_tickers_list = _holdings["Ticker"].tolist()
        _opt_weights_list = _holdings["Weight"].tolist()
        _opt_rebal_freq = selected_freq if enable_calendar_rebal else "None"

        _static_result = None
        _opt_result_full = None

        with st.spinner("Running MSBA v1 Static simulation..."):
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
                st.error(f"MSBA v1 Static simulation failed: {e}")

        with st.spinner("Running MSBA v1 Optimized simulation..."):
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
                st.error(f"MSBA v1 Optimized simulation failed: {e}")

        if _static_result and _opt_result_full:
            _tlh_rows_opt = []
            for _lbl_o, _res_o in [("Static (TLH only)", _static_result), ("Optimized (Rebal+TLH)", _opt_result_full)]:
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
            _export["Realized Gains"] = _rdf_exp
        if not _tdf_exp.empty:
            _export["Trade Log"] = _tdf_exp

    # ── Persist to session_state ───────────────────────────────────────────
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
            f"Est. Cost = Turnover ($) × {_total_cost_rate_d*10000:.0f} bps "
            f"({_commission_bps_d:.0f} commission + {_slippage_bps_d:.0f} slippage + {_bid_ask_bps_d:.0f} bid-ask). "
            "Sharpe uses Rf=0. TE (ann.) = annualized tracking error vs Buy & Hold, shown as %. "
            "IR = annualized active return ÷ TE (unitless)."
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
**Calendar:** Rebalances on schedule (Daily / Weekly / Monthly / Quarterly) at closing prices. No transaction costs applied to NAV — see the Est. Cost column in the tables above.

**Threshold (Drift-Band):** Breach detected at close → executes next trading day (no look-ahead). Cooldown suppresses re-triggers for N days post-rebalance.

**Transaction Cost Estimation:** Commission {_commission_bps_d:.0f} bps + Slippage {_slippage_bps_d:.0f} bps + Bid-Ask {_bid_ask_bps_d:.0f} bps = **{_total_cost_rate_d*10000:.0f} bps total** applied to turnover dollars. Adjust rates in the sidebar.

**MSBA v1 Optimizer:** Full lot-level accounting — costs are actually deducted from cash (not estimated).

**Sharpe Ratio:** Risk-free rate = 0 throughout for strategy comparison consistency.
            """)

    st.markdown("---")


# ================================================================
# MSBA v1 OPTIMIZER SECTION (read from session_state)
# ================================================================

if opt:
    st.subheader("\U0001f9e0 Optimizer MSBA v1 \u2014 Tax-Aware Simulation")
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
    kc1.metric("Static Final NAV", f"${s_final:,.0f}", delta=f"{(s_final/cap - 1):+.2%}")
    kc2.metric("Optimized Final NAV", f"${o_final:,.0f}", delta=f"{(o_final/cap - 1):+.2%}")
    kc3.metric("Optimizer Advantage", f"${o_final - s_final:+,.0f}", delta=f"{((o_final - s_final)/cap):+.4%}")
    kc4.metric("Total Tax Paid (Opt)", f"${opt_result_d['tax_paid_total']:,.0f}",
               delta=f"Static: ${static_result_d['tax_paid_total']:,.0f}")
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
        "Static TLH (after-tax)": s_nav,
        "Optimized Rebal+TLH (after-tax)": o_nav,
    }).dropna()
    st.line_chart(
        _safe_chart_cols(_opt_chart_base),
        color=["#1a73e8", "#888888", "#e8710a"],
        use_container_width=True, height=400,
    )
    st.caption(
        "**Blue:** Buy & Hold (no TLH, no rebalancing). "
        "**Grey:** Static TLH only (after-tax). "
        "**Orange:** Optimized Rebal + TLH (after-tax)."
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
        ("Static TLH", s_nav),
        ("Optimized Rebal+TLH", o_nav),
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
        excel_download_button(_opt_metrics_df, "optimizer_metrics.xlsx",
                              label="Optimizer Metrics", sheet_name="Metrics")
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
    st.caption("Net Benefit = Est. Tax Savings − Tax Paid − Execution Costs. Positive = TLH added value.")

    # ── 6. Detailed Logs (expanders) ─────────────────────────────────────────
    with st.expander("📋 Optimized Portfolio — Realized Gains"):
        _rdf_d = opt_result_d["realized_df"]
        if not _rdf_d.empty:
            _rg1, _rg2 = st.columns([5, 1])
            with _rg1:
                st.dataframe(_rdf_d, use_container_width=True, hide_index=True)
            with _rg2:
                excel_download_button(_rdf_d, "realized_gains.xlsx", label="Realized Gains", sheet_name="Realized")
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

**Returns (Buy & Hold / Calendar / Threshold):** Price appreciation only — dividends and splits are not included. For dividend-inclusive returns, use the MSBA v1 Optimizer with a dividend data file.

**Dividends (MSBA v1 Optimizer):** When dividend_data.csv is present, dividends are automatically reinvested (DRIP) or held as cash depending on your selection. Total dividends reinvested is shown in the KPI row of the optimizer section.

**CAGR:** Annualized geometric return using actual calendar days elapsed ÷ 365.25. For periods under 30 days, CAGR extrapolates aggressively and should be interpreted with caution.

**Annualized Volatility & Tracking Error:** Computed using 252 trading days per year (industry standard for daily data). TE is shown as % in the strategy comparison table.

**Fractional Shares:** Default. Toggle "Whole shares only" for integer shares with uninvested cash residual.

**Transaction Costs (Calendar/Threshold engines):** Estimated at {_tcr_d*10000:.0f} bps total ({_comm_d:.0f} commission + {_slip_d:.0f} slippage + {_bask_d:.0f} bid-ask) applied to turnover dollars. **Not deducted from NAV** — shown as a dollar estimate only.

**Transaction Costs (MSBA v1 Optimizer):** Deducted from portfolio cash on every trade. Same {_comm_d:.0f}+{_slip_d:.0f}+{_bask_d:.0f} bps breakdown as above.

**Tax Rates:** ST={_gst_d:.0%} / LT={_glt_d:.0%} (sidebar-configurable). Applied in the MSBA v1 optimizer with lot-level tracking, wash-sale enforcement, and loss carry-forward netting. Not applied to the base calendar/threshold engines.

**Sharpe Ratio:** Risk-free rate = 0 throughout (appropriate for cross-strategy relative comparison).
    """)

# ── END OF DISPLAY SECTION ────────────────────────────────────────────────
