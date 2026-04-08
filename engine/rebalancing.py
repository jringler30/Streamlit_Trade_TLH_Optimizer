"""
engine/rebalancing.py
=====================
Calendar and threshold (drift-band) rebalancing engines.

All functions are pure Python (no Streamlit dependency) and importable
directly in unit tests without any mocking infrastructure.

Public API
----------
_get_rebalance_dates(trading_dates, freq) -> set  # Daily/Weekly/Monthly/Quarterly/6 Month/Annual/2 Year/5 Year
build_rebalanced_series(prices_wide, target_weights, ...) -> (DataFrame, dict)
compute_weights(shares, prices) -> dict
compute_drift(current_weights, target_weights, drift_mode) -> dict
find_threshold_triggers(drift, tolerances) -> list
apply_rebalance_full(shares, target_weights, prices, total_value, ...) -> (dict, float)
apply_rebalance_partial(shares, target_weights, ...) -> (dict, float)
build_threshold_rebalanced_series(prices_wide, target_weights, ...) -> (DataFrame, dict, DataFrame, dict)
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional, Set


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
        quarter_months = {1, 4, 7, 10}
        prev_month = dates[0].month
        prev_year = dates[0].year
        for dt in dates[1:]:
            if dt.month in quarter_months and (dt.month != prev_month or dt.year != prev_year):
                rebal_set.add(dt)
            if dt.month != prev_month or dt.year != prev_year:
                prev_month = dt.month
                prev_year = dt.year
    elif freq == "6 Month":
        # Rebalance at the start of Jan and Jul each year.
        semi_months = {1, 7}
        prev_month = dates[0].month
        prev_year = dates[0].year
        for dt in dates[1:]:
            if dt.month in semi_months and (dt.month != prev_month or dt.year != prev_year):
                rebal_set.add(dt)
            if dt.month != prev_month or dt.year != prev_year:
                prev_month = dt.month
                prev_year = dt.year
    elif freq == "Annual":
        # Rebalance on the first trading day of each calendar year.
        prev_year = dates[0].year
        for dt in dates[1:]:
            if dt.year != prev_year:
                rebal_set.add(dt)
                prev_year = dt.year
    elif freq == "2 Year":
        # Rebalance on the first trading day of every other calendar year.
        base_year = dates[0].year
        last_rebal_year = base_year
        for dt in dates[1:]:
            if dt.year != last_rebal_year and (dt.year - base_year) % 2 == 0:
                rebal_set.add(dt)
                last_rebal_year = dt.year
    elif freq == "5 Year":
        # Rebalance on the first trading day of every 5th calendar year.
        base_year = dates[0].year
        last_rebal_year = base_year
        for dt in dates[1:]:
            if dt.year != last_rebal_year and (dt.year - base_year) % 5 == 0:
                rebal_set.add(dt)
                last_rebal_year = dt.year
    else:
        raise ValueError(f"Unknown rebalance frequency: {freq}")
    return rebal_set


def build_rebalanced_series(prices_wide, target_weights, initial_capital, rebalance_freq,
                            cost_rate: float = 0.0):
    """
    Original calendar-only rebalancing engine (V3).

    On each rebalance date, the portfolio is valued and shares are adjusted to
    restore exact target weights. Trades execute at same-day closing prices.
    Fractional shares are used (consistent with the base engine).

    cost_rate: total transaction cost as a fraction of turnover (e.g. 0.0012 for 12 bps).
        Costs are embedded permanently in the NAV series by proportionally scaling
        all share counts after each rebalance. This ensures the cost drag compounds
        forward and makes calendar/threshold engines comparable to the optimizer,
        which deducts costs from cash directly.
        Default 0.0 preserves backward-compatible behaviour.

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

    # Pre-allocate numpy arrays for performance
    portfolio_values = np.empty(n_days, dtype=np.float64)
    ticker_values_arr = {tk: np.empty(n_days, dtype=np.float64) for tk in tickers}
    rebalance_count = 0
    total_turnover_dollars = 0.0

    for i, dt in enumerate(dates):
        # Mark-to-market
        total_value = 0.0
        tv = {}
        for tk in tickers:
            val = shares[tk] * prices_wide.loc[dt, tk]
            tv[tk] = val
            total_value += val
        portfolio_values[i] = total_value
        for tk in tickers:
            ticker_values_arr[tk][i] = tv[tk]

        # Calendar rebalance check
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
                for tk in tickers:
                    target_value = target_weights[tk] * total_value
                    new_shares = target_value / prices_wide.loc[dt, tk]
                    trade_shares = new_shares - shares[tk]
                    trade_dollars = abs(trade_shares * prices_wide.loc[dt, tk])
                    day_turnover += trade_dollars
                    shares[tk] = new_shares

                # Embed transaction costs in NAV: scale all shares proportionally
                # so the cost drag is permanently reflected in the NAV series.
                if cost_rate > 0 and day_turnover > 0 and total_value > 0:
                    cost_drag = day_turnover * cost_rate
                    scale = max(0.0, (total_value - cost_drag) / total_value)
                    for tk in tickers:
                        shares[tk] *= scale

                # Update day-end recorded values to reflect post-rebalance, post-cost state.
                total_value = sum(shares[tk] * prices_wide.loc[dt, tk] for tk in tickers)
                portfolio_values[i] = total_value
                for tk in tickers:
                    ticker_values_arr[tk][i] = shares[tk] * prices_wide.loc[dt, tk]

                total_turnover_dollars += day_turnover

    avg_port_value = np.mean(portfolio_values)
    turnover_proxy = (total_turnover_dollars / avg_port_value) if avg_port_value > 0 else 0.0
    rebal_daily = pd.DataFrame(index=dates)
    rebal_daily.index.name = "PRICEDATE"
    for tk in tickers:
        rebal_daily[f"{tk} (Rebal)"] = ticker_values_arr[tk]
    rebal_daily["Portfolio Value"] = portfolio_values
    rebal_stats = {
        "rebalance_count": rebalance_count,
        "turnover_proxy": round(turnover_proxy, 4),
        "final_value": round(portfolio_values[-1], 2),
        "total_return": round(portfolio_values[-1] / initial_capital - 1, 6),
        "total_turnover_dollars": round(total_turnover_dollars, 2),
    }
    return rebal_daily, rebal_stats


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
    - Relative: |log(w_i / target_i)|. Symmetric log-ratio. The 2%→4% drift
      equals the 4%→2% drift (both are log(2) ≈ 0.693). The naive formula
      |w/tgt - 1| is asymmetric: doubling gives 100% but halving gives only 50%.
    """
    drift = {}
    for tk in target_weights:
        w_cur = current_weights.get(tk, 0.0)
        w_tgt = target_weights[tk]
        if drift_mode == "Relative":
            # Symmetric log-ratio. Guards: use a small floor (1e-12) to avoid log(0).
            if w_tgt >= 1e-12 and w_cur > 1e-12:
                drift[tk] = abs(np.log(w_cur / w_tgt))
            elif w_tgt >= 1e-12:
                drift[tk] = abs(np.log(1e-12 / w_tgt))
            else:
                drift[tk] = abs(w_cur)
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
    ones breached. Returns (new_shares, turnover) where turnover is the sum of
    absolute dollar values of all trades.
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
    for non-breached assets.
    """
    tickers = list(target_weights.keys())
    current_weights = compute_weights(shares, prices)
    breached_set = set(breached_tickers)
    breached_target_sum = sum(target_weights[tk] for tk in breached_set)
    remaining_budget = 1.0 - breached_target_sum
    non_breached_current_sum = sum(
        current_weights.get(tk, 0.0) for tk in tickers if tk not in breached_set
    )

    desired_weights = {}
    for tk in tickers:
        if tk in breached_set:
            desired_weights[tk] = target_weights[tk]
        else:
            if non_breached_current_sum > 1e-12:
                desired_weights[tk] = (current_weights.get(tk, 0.0) / non_breached_current_sum) * remaining_budget
            else:
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
    cost_rate: float = 0.0,
):
    """
    Combined calendar + threshold (drift-band) rebalancing engine (V4).

    Key design decisions:

    1. NEXT-DAY EXECUTION: Threshold breaches detected at end-of-day execute
       on the NEXT trading day. This avoids look-ahead bias.

    2. COOLDOWN: After a threshold rebalance, further threshold triggers are
       suppressed for N trading days. Calendar events are NOT affected by cooldown.

    3. ORDER OF OPERATIONS within each day:
       a) Mark-to-market and record drift
       b) Execute any pending threshold rebalance from yesterday's breach
       c) Execute calendar rebalance if today is a scheduled date
       d) Check for new threshold breaches (scheduled for tomorrow)
       e) Decrement cooldown counter

    Transaction costs are embedded in NAV via proportional share scaling after
    each rebalance event (same treatment as the calendar engine and the MSBA v1
    optimizer which deducts from cash directly).

    Returns: (rebal_daily, rebal_stats, event_log_df, drift_history)
    - event_log_df: structured log of every rebalance event for audit/export
    - drift_history: per-ticker daily drift values for diagnostics visualization
    """
    tickers = list(target_weights.keys())
    dates = prices_wide.index.tolist()
    n_days = len(dates)
    if n_days == 0:
        raise ValueError("No trading dates in the filtered price data.")

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
    pending_threshold_breach = False
    pending_breached_tickers = []
    pending_max_drift = 0.0

    for i, dt in enumerate(dates):
        prices_today = {tk: float(prices_wide.loc[dt, tk]) for tk in tickers}
        total_value = sum(shares[tk] * prices_today[tk] for tk in tickers)
        portfolio_values[i] = total_value
        for tk in tickers:
            ticker_values_arr[tk][i] = shares[tk] * prices_today[tk]

        # Record drift for every day (powers diagnostics visualization)
        current_weights = compute_weights(shares, prices_today)
        drift = compute_drift(current_weights, target_weights, drift_mode)
        for tk in tickers:
            drift_history[tk].append(drift.get(tk, 0.0))

        did_rebalance_today = False
        rebal_reasons = []

        # STEP 1: Execute pending threshold rebalance (breach detected yesterday).
        # Next-day execution model — avoids using information unavailable in real-time.
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

                # Embed transaction costs in NAV by proportionally scaling shares down.
                if cost_rate > 0 and turnover > 0 and total_value > 0:
                    cost_drag = turnover * cost_rate
                    scale = max(0.0, (total_value - cost_drag) / total_value)
                    shares = {tk: sh * scale for tk, sh in shares.items()}

                total_turnover_dollars += turnover
                rebalance_count += 1
                threshold_rebal_count += 1
                did_rebalance_today = True
                rebal_reasons.append("threshold")
                cooldown_remaining = cooldown_days

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
            pending_threshold_breach = False
            pending_breached_tickers = []
            pending_max_drift = 0.0

        # STEP 2: Calendar rebalance (independent of threshold).
        # Calendar always uses full rebalance regardless of rebalance_action setting.
        if enable_calendar and dt in calendar_dates and total_value > 0:
            cw_now = compute_weights(shares, prices_today)
            needs_rebal = any(abs(cw_now.get(tk, 0) - target_weights[tk]) > 1e-10 for tk in tickers)
            if needs_rebal:
                new_shares, turnover = apply_rebalance_full(
                    shares, target_weights, prices_today, total_value, whole_shares)
                shares = new_shares

                if cost_rate > 0 and turnover > 0 and total_value > 0:
                    cost_drag = turnover * cost_rate
                    scale = max(0.0, (total_value - cost_drag) / total_value)
                    shares = {tk: sh * scale for tk, sh in shares.items()}

                total_turnover_dollars += turnover
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

        # STEP 3: End-of-day threshold check — schedule for next day if breached.
        # Skipped on the last day (no next day to execute on).
        if enable_threshold and i < n_days - 1:
            cw_post = compute_weights(shares, prices_today)
            drift_post = compute_drift(cw_post, target_weights, drift_mode)
            breached = find_threshold_triggers(drift_post, tolerances)
            if breached and cooldown_remaining <= 0:
                pending_threshold_breach = True
                pending_breached_tickers = breached
                pending_max_drift = max(drift_post[tk] for tk in breached)

        if cooldown_remaining > 0:
            cooldown_remaining -= 1

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
        "total_turnover_dollars": round(total_turnover_dollars, 2),
    }
    if event_log:
        event_log_df = pd.DataFrame(event_log)
    else:
        event_log_df = pd.DataFrame(columns=["date", "reason", "breached_tickers", "max_drift", "turnover_dollars"])
    return rebal_daily, rebal_stats, event_log_df, drift_history
