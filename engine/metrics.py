"""
engine/metrics.py
=================
Performance metric calculations for portfolio simulation results.

All functions are pure Python (no Streamlit dependency) and importable
directly in unit tests without any mocking infrastructure.

Public API
----------
compute_strategy_metrics(daily_values, initial_capital, benchmark_values, dates)
    Returns a dict of CAGR, Sharpe, Calmar, volatility, drawdown, TE, IR, etc.
"""

import numpy as np
import pandas as pd
from scipy import stats as sp_stats


def compute_strategy_metrics(daily_values, initial_capital, benchmark_values=None, dates=None):
    """
    Compute performance metrics from a daily portfolio value series.

    V4 extends the original {total_return, cagr, vol, sharpe, max_dd} with:
    - Calmar: CAGR / |MaxDrawdown| — negative when CAGR is negative
    - Skewness: negative skew = fatter left tail = more downside risk
    - Kurtosis (excess/Fisher): >0 means heavier tails than normal distribution
    - Avg Drawdown: mean of all daily drawdowns (not just max)
    - Tracking Error: annualized std of active returns vs benchmark
    - Information Ratio: annualized active return / tracking error

    CAGR time-horizon:
    - When ``dates`` is provided (DatetimeIndex or list of timestamps), CAGR uses
      actual calendar days elapsed ÷ 365.25 for the year fraction. This avoids
      the 252-trading-day approximation error which understates years on short
      periods (e.g. a 3-month Q1 with 63 trading days: 63/252 = 0.25 years,
      but the calendar says 90/365.25 = 0.246 years — small but consistent).
    - Without dates, uses (n-1)/252 rather than n/252. n data points have
      n-1 return intervals; using n overcounts elapsed time by one day.
    - Annualized vol and Sharpe always use 252 (industry standard for daily data).

    Sharpe uses Rf=0 (appropriate for relative strategy comparison).

    Parameters
    ----------
    daily_values : array-like
        Daily NAV/portfolio values.
    initial_capital : float
        Starting capital used to compute total return.
    benchmark_values : array-like, optional
        Same-length daily values for TE and IR computation.
    dates : DatetimeIndex or list, optional
        Trading dates corresponding to daily_values; enables calendar-accurate CAGR.

    Returns
    -------
    dict with keys:
        total_return, cagr, annualized_vol, sharpe, max_drawdown, calmar_ratio,
        skewness, kurtosis, avg_drawdown, tracking_error, information_ratio,
        trading_days, years_used
    """
    n = len(daily_values)
    if n < 2:
        return {
            "total_return": 0.0, "cagr": 0.0, "annualized_vol": 0.0,
            "sharpe": 0.0, "max_drawdown": 0.0, "calmar_ratio": 0.0,
            "skewness": 0.0, "kurtosis": 0.0, "avg_drawdown": 0.0,
            "tracking_error": 0.0, "information_ratio": 0.0,
            "trading_days": n, "years_used": 0.0,
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
        try:
            t0 = pd.Timestamp(dates[0])
            t1 = pd.Timestamp(dates[-1])
            calendar_days = (t1 - t0).days
            # 365.25 accounts for leap years; minimum 1 day to avoid div-zero
            years = max(calendar_days, 1) / 365.25
        except Exception:
            years = max(n - 1, 1) / 252.0
    else:
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
    safe_rm = np.where(running_max > 0, running_max, 1.0)
    drawdowns = (daily_values - running_max) / safe_rm
    max_dd = float(np.min(drawdowns))

    # ── Calmar ratio ─────────────────────────────────────────────────────────
    # Calmar = CAGR / |MaxDrawdown|. Negative CAGR → negative Calmar.
    # Do NOT take abs() of the full ratio — that would incorrectly flip
    # a loss-generating strategy to look positive.
    calmar_ratio = (cagr / abs(max_dd)) if max_dd != 0 else 0.0

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
            # IR numerator = CAGR difference (not mean(daily) * 252).
            # mean(daily) * 252 compounds arithmetic drift; CAGR difference is the
            # true geometric excess return over the period.
            bm_cagr = float((bm_vals[-1] / bm_vals[0]) ** (1.0 / years) - 1.0)
            information_ratio = (cagr - bm_cagr) / tracking_error

    return {
        "total_return": round(total_return, 6),
        "cagr": round(cagr, 6),
        "annualized_vol": round(ann_vol, 6),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 6),
        "calmar_ratio": round(calmar_ratio, 4),
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
