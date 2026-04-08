"""
engine — portfolio simulation, rebalancing, and performance metrics.

All modules are pure Python with no Streamlit dependency.
Import directly from sub-modules for the cleanest namespace, or use
this package-level re-export for backward compatibility.
"""

from engine.core import (
    validate_weights,
    prepare_price_data,
    get_ticker_prices,
    calculate_portfolio_returns,
    build_daily_series,
    build_prices_wide,
)
from engine.rebalancing import (
    _get_rebalance_dates,
    build_rebalanced_series,
    compute_weights,
    compute_drift,
    find_threshold_triggers,
    apply_rebalance_full,
    apply_rebalance_partial,
    build_threshold_rebalanced_series,
)
from engine.metrics import compute_strategy_metrics

__all__ = [
    "validate_weights",
    "prepare_price_data",
    "get_ticker_prices",
    "calculate_portfolio_returns",
    "build_daily_series",
    "build_prices_wide",
    "_get_rebalance_dates",
    "build_rebalanced_series",
    "compute_weights",
    "compute_drift",
    "find_threshold_triggers",
    "apply_rebalance_full",
    "apply_rebalance_partial",
    "build_threshold_rebalanced_series",
    "compute_strategy_metrics",
]
