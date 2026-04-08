"""
engine/core.py
==============
Core portfolio simulation functions: validation, price lookup,
buy-and-hold calculation, and daily series construction.

All functions are pure Python (no Streamlit dependency) and importable
directly in unit tests without any mocking infrastructure.

Public API
----------
validate_weights(tickers, weights, tolerance) -> (tickers, weights)
prepare_price_data(df, price_field) -> DataFrame
get_ticker_prices(ticker_df, ticker, start_date, end_date, price_field) -> dict
calculate_portfolio_returns(df, tickers, weights, ...) -> (summary, holdings_df)
build_daily_series(df, holdings, initial_capital, price_field) -> DataFrame
build_prices_wide(df, tickers, start_date, end_date, price_field) -> DataFrame
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional


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
            "Return": round((r["end_price"] / r["start_price"]) - 1, 6),
            # Dollar gain: absolute P&L for this position at the allocated capital level
            "Gain ($)": round(end_value - cost, 2),
            # Percentage gain at the position level
            "Gain (%)": round((end_value - cost) / cost, 6) if cost else 0,
            "Flags": "; ".join(r["flags"]) if r["flags"] else "OK",
        })
    holdings_df = pd.DataFrame(holdings_data)
    port_end = holdings_df["End Value"].sum() + total_cash_residual
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
    daily value = shares_held × closing_price. The join uses outer merge + ffill
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
        tk_prices[tk] = tk_prices[tk] * shares
        frames.append(tk_prices)

    # Outer join preserves all dates from all tickers. ffill then dropna ensures
    # no NaN gaps — if ticker A trades on Monday but ticker B doesn't, B's
    # Friday close carries forward. Leading rows where any ticker has no data yet
    # are dropped to avoid lookahead bias.
    daily = frames[0]
    for f in frames[1:]:
        daily = daily.join(f, how="outer")
    daily = daily.sort_index().ffill().dropna()
    daily["Portfolio Value"] = daily[tickers].sum(axis=1)
    daily["Cost Basis"] = initial_capital

    # Per-ticker cumulative return series (used for the return breakdown chart)
    for tk in tickers:
        start_val = daily[tk].iloc[0]
        daily[f"{tk} Return (%)"] = (daily[tk] / start_val - 1) * 100
    return daily


def build_prices_wide(df, tickers, start_date, end_date, price_field="PRICECLOSE"):
    """
    Pivot long-format price data into a (date × ticker) wide matrix.

    This is the shared data structure consumed by both the calendar and threshold
    rebalancing engines. Filtering to only needed tickers and dates FIRST keeps
    memory usage manageable even with a large universe.

    Forward-fill only (no bfill) to avoid lookahead bias. Leading NaN rows from
    tickers that start trading after the requested start date are dropped via
    dropna(). If all rows are dropped, raises ValueError with a clear message.
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
    wide = subset.pivot(index="PRICEDATE", columns="TICKERSYMBOL", values=price_field)
    # Forward-fill only: carries the last valid price through weekends/holidays.
    # Backward fill is intentionally omitted — it would propagate future prices into
    # earlier dates for tickers that start trading after the requested start date,
    # introducing lookahead bias. Leading NaN rows are dropped instead.
    wide = wide.sort_index().ffill()
    wide = wide.dropna()
    if wide.empty:
        raise ValueError(
            "No overlapping trading dates found for all tickers after forward-fill. "
            "One or more tickers may lack price history at the requested start date."
        )
    missing_cols = [t for t in tickers if t not in wide.columns]
    if missing_cols:
        raise ValueError(f"Tickers missing from price data after filtering: {missing_cols}")
    wide = wide[tickers]
    return wide
