"""
agent_tools.py — Utilities for the TLH Dashboard AI Assistant.

Responsibilities:
  - load_code_context(): full source (use for paid APIs with large context budgets)
  - load_compact_context(): ~4K token summary with verbatim key algorithms (default)
  - build_data_namespace(): extract raw DataFrames from st.session_state["_r"]
  - describe_namespace(): human-readable summary of available data objects
  - extract_chart_code(): parse <CHART_CODE>...</CHART_CODE> from agent response
  - clean_response_text(): replace CHART_CODE blocks with a placeholder for display
  - execute_chart_code(): safe exec() in restricted namespace → (fig, error)
"""
import re
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

REPO_ROOT = Path(__file__).parent

# Source files loaded into the agent's system prompt.
# Keep this list focused — only files the agent needs to cite.
CODE_FILES = [
    (
        "portfolio_returns_engine.py",
        "Main Streamlit app: data loading, buy-and-hold engine, calendar rebalancing (V3), "
        "threshold/drift-band rebalancing (V4), performance metrics, charts, session state, "
        "strategy comparison, transaction cost estimation",
    ),
    (
        "optimizer_msba_v1_engine.py",
        "Tax-aware simulation engine: TaxEngine (ST/LT classification, loss carry-forward), "
        "Portfolio class (lot tracking, TAX_OPTIMAL sell, DRIP dividends), TLH logic, "
        "run_optimizer_simulation() daily loop",
    ),
]

_CHART_CODE_RE = re.compile(r"<CHART_CODE>(.*?)</CHART_CODE>", re.DOTALL)

# Restricted builtins for safe chart code execution.
# Blocks: import, open, exec, eval, __import__, os, sys, etc.
_SAFE_BUILTINS = {
    "range": range, "len": len, "list": list, "dict": dict, "tuple": tuple,
    "set": set, "str": str, "int": int, "float": float, "bool": bool,
    "zip": zip, "enumerate": enumerate, "min": min, "max": max,
    "sum": sum, "abs": abs, "sorted": sorted, "reversed": reversed,
    "isinstance": isinstance, "type": type, "round": round,
    "hasattr": hasattr, "getattr": getattr, "print": print,
    "None": None, "True": True, "False": False,
    "Exception": Exception, "ValueError": ValueError, "KeyError": KeyError,
}


# ── Code context ───────────────────────────────────────────────────────────────

def load_code_context() -> str:
    """
    Read the relevant source files and return a combined string suitable for
    injection into the AI system prompt. Called once per session and cached.
    """
    parts = []
    for filename, description in CODE_FILES:
        filepath = REPO_ROOT / filename
        if filepath.exists():
            code = filepath.read_text(encoding="utf-8")
            parts.append(
                f"### FILE: {filename}\n"
                f"### DESCRIPTION: {description}\n"
                f"```python\n{code}\n```"
            )
        else:
            parts.append(f"### FILE: {filename}\n(NOT FOUND at expected path: {filepath})")
    return "\n\n---\n\n".join(parts)


# ── Compact code context (default for free-tier APIs) ─────────────────────────

def load_compact_context() -> str:
    """
    Return a ~4K-token code summary for use with free-tier APIs (e.g. Gemini Flash).
    Includes a full function index plus verbatim code for the key algorithms that
    users most commonly ask about. Accurate because snippets are taken directly
    from the source; omitted sections are described precisely enough to answer Q&A.
    """
    return '''
## CODEBASE: portfolio-tlh-optimizer
### Files: portfolio_returns_engine.py (main app, ~2225 lines) | optimizer_msba_v1_engine.py (tax engine, ~499 lines)

---
## FUNCTION INDEX — portfolio_returns_engine.py

| Line | Function | Purpose |
|------|----------|---------|
| 64   | to_excel_bytes(dfs) | Serialize dict of DataFrames to Excel bytes (in-memory) |
| 76   | excel_download_button(...) | Streamlit download button wrapper |
| 101  | ensure_data() | Download price parquet from Google Drive if not cached in /tmp |
| 117  | validate_weights(tickers, weights) | Deduplicate tickers, normalize weights, check tolerance |
| 154  | prepare_price_data(df) | Filter to security_status 1 or 15, convert dates, sort |
| 181  | get_ticker_prices(df, ticker, start, end, price_field) | Find start/end prices with date boundary shifting |
| 222  | calculate_portfolio_returns(...) | Core buy-and-hold: allocate shares, compute per-ticker returns, build holdings DataFrame |
| 324  | build_daily_series(df, holdings, capital, price_field) | Time series of portfolio value + cost basis + per-ticker return % |
| 387  | build_prices_wide(df, tickers, start, end, price_field) | Pivot long price data → (date × ticker) matrix for O(1) sim lookups |
| 419  | _get_rebalance_dates(trading_dates, freq) | Detect calendar rebalance dates by frequency (see code below) |
| 476  | build_rebalanced_series(prices_wide, weights, capital, freq) | V3 calendar rebalancing: allocate → daily loop → rebalance on schedule |
| 584  | compute_weights(shares, prices) | Dict of ticker → current weight |
| 593  | compute_drift(current_weights, target_weights, mode) | Absolute: |cur-tgt|  Relative: |cur/tgt - 1| |
| 623  | find_threshold_triggers(drift, tolerances) | Return list of tickers where drift > tolerance |
| 641  | apply_rebalance_full(shares, weights, prices, total_val, whole_shares) | Set ALL assets to exact target weights; return (new_shares, turnover_dollars) |
| 672  | apply_rebalance_partial(shares, weights, tolerances, breached, prices, total_val, whole_shares) | Trade only breached tickers; scale others proportionally |
| 735  | build_threshold_rebalanced_series(...) | V4 drift-band + calendar engine (see code below) |
| 947  | compute_strategy_metrics(daily_values, initial_capital, benchmark_values) | 10 metrics: return, CAGR, vol, Sharpe, max/avg drawdown, skew, kurtosis, TE, IR (see code below) |
| 1044 | _safe_chart_cols(chart_df) | Sanitize column names for Vega-Lite (remove &, $, (), :) |
| 1075 | load_data() | Read parquet, call prepare_price_data() |

Session state key: st.session_state["_r"] — populated on "Calculate Returns" button press.
Contains: summary, holdings (DataFrame), display_df, daily (DataFrame), tickers_used, bh_vals (np.array),
bh_metrics (dict), n_days, summary_df, drift_df, rebal (dict), opt (dict), export (dict), params (dict).

Transaction cost estimation (calendar/threshold engines, NOT deducted from NAV):
  _total_cost_rate = (commission_bps + slippage_bps + bid_ask_bps) / 10_000
  est_transaction_cost = total_turnover_dollars * _total_cost_rate
  Shown in cost_df table. Costs ARE deducted from cash in the MSBA v1 optimizer.

---
## FUNCTION INDEX — optimizer_msba_v1_engine.py

| Line | Class/Function | Purpose |
|------|----------------|---------|
| 46   | TaxEngine | ST/LT classification + loss carry-forward netting (see code below) |
| 101  | Portfolio | Lot-level accounting: cash, lots list, trades log, realized gains |
| 165  | Portfolio.buy(date, ticker, shares, price, source) | Open new lot; deduct exec_cost = gross * _cost_rate from cash |
| 200  | Portfolio.sell(date, ticker, shares, price, lot_selection) | Dispose lots (TAX_OPTIMAL/FIFO); classify gain; compute/deduct tax; return net proceeds |
| 150  | Portfolio._sorted_lots_for_sell(ticker, price, date) | TAX_OPTIMAL sort: losses first (biggest ST loss first), then smallest gains |
| 259  | Portfolio.process_dividend(date, ticker, div_per_share, price, reinvest) | Add dividend to cash; optionally DRIP-buy new lot |
| 274  | Portfolio.market_value(prices) | sum(shares × price) across all open lots |
| 281  | Portfolio.nav(prices) | market_value + cash |
| 301  | _build_rebalance_set(dates, freq) | Same logic as _get_rebalance_dates() above |
| 329  | run_optimizer_simulation(...) | Main daily loop: dividends → TLH → rebalancing → record NAV (see TLH loop below) |

DEFAULT_COST_CONFIG = {"commission_bps": 5.0, "slippage_bps": 5.0, "bid_ask_bps": 2.0}
_cost_rate = (commission_bps + slippage_bps + bid_ask_bps) / 10_000  →  0.0012 (12 bps total)

---
## KEY ALGORITHM CODE SNIPPETS (verbatim from source)

### 1. compute_strategy_metrics() — portfolio_returns_engine.py line 947
```python
def compute_strategy_metrics(daily_values, initial_capital, benchmark_values=None):
    n = len(daily_values)
    final = daily_values[-1]
    total_return = final / initial_capital - 1

    years = n / 252.0
    cagr = (final / initial_capital) ** (1 / years) - 1  # geometric, 252 days/year

    daily_rets = np.diff(daily_values) / daily_values[:-1]  # simple (not log) returns
    ann_vol = np.std(daily_rets, ddof=1) * np.sqrt(252)     # Bessel-corrected, annualized
    sharpe = cagr / ann_vol  # Rf = 0

    running_max = np.maximum.accumulate(daily_values)
    drawdowns = (daily_values - running_max) / running_max
    max_dd = float(np.min(drawdowns))
    avg_drawdown = float(np.mean(drawdowns))  # mean of ALL daily drawdowns, not just max

    skewness = float(sp_stats.skew(daily_rets))
    kurtosis = float(sp_stats.kurtosis(daily_rets, fisher=True))  # excess kurtosis

    if benchmark_values is not None and len(benchmark_values) == n:
        bm_rets = np.diff(benchmark_values) / benchmark_values[:-1]
        active_rets = daily_rets - bm_rets
        tracking_error = float(np.std(active_rets, ddof=1) * np.sqrt(252))
        ann_active_mean = float(np.mean(active_rets) * 252)
        information_ratio = ann_active_mean / tracking_error
```

### 2. Turnover calculation — build_rebalanced_series() line 526
```python
# On each rebalance date:
for tk in tickers:
    target_value = target_weights[tk] * total_value
    new_shares = target_value / prices_wide.loc[dt, tk]
    trade_shares = new_shares - shares[tk]
    trade_dollars = abs(trade_shares * prices_wide.loc[dt, tk])
    day_turnover += trade_dollars
    shares[tk] = new_shares
total_turnover_dollars += day_turnover

# After simulation:
avg_port_value = np.mean(portfolio_values)
turnover_proxy = total_turnover_dollars / avg_port_value  # "turnover ratio" (× factor)
est_cost = total_turnover_dollars * _total_cost_rate       # estimated $ cost (not deducted from NAV)
```

### 3. _get_rebalance_dates() — portfolio_returns_engine.py line 419
```python
# Daily: every trading day after day 0
# Weekly: first trading day where ISO week number changes
# Monthly: first trading day where month changes
# Quarterly: first trading day in Jan/Apr/Jul/Oct where month changes
# Returns a set for O(1) membership in the simulation loop
if freq == "Daily":
    return set(dates[1:])
if freq == "Weekly":
    # prev_week + prev_year tracking avoids year-boundary false triggers
    ...rebal_set.add(dt) when iso_week changes
if freq == "Monthly":
    ...rebal_set.add(dt) when dt.month != prev_month
if freq == "Quarterly":
    quarter_months = {1, 4, 7, 10}
    ...rebal_set.add(dt) when dt.month in quarter_months and month changed
```

### 4. build_threshold_rebalanced_series() state machine — line 735
```python
# Key design: NEXT-DAY EXECUTION (no look-ahead bias) + COOLDOWN suppression
# State carried across iterations:
pending_threshold_breach = False
pending_breached_tickers = []
cooldown_remaining = 0

for i, dt in enumerate(dates):
    # a) Mark-to-market, record drift unconditionally (powers drift diagnostics)
    current_weights = compute_weights(shares, prices_today)
    drift = compute_drift(current_weights, target_weights, drift_mode)

    # b) Execute YESTERDAY'S pending threshold breach (next-day execution)
    if pending_threshold_breach and cooldown_remaining <= 0:
        new_shares, turnover = apply_rebalance_full(...)   # or partial
        cooldown_remaining = cooldown_days   # suppress threshold for N days

    # c) Calendar rebalance fires independently — NOT blocked by cooldown
    if enable_calendar and dt in calendar_dates:
        new_shares, turnover = apply_rebalance_full(...)

    # d) End-of-day: detect new threshold breach → schedule for TOMORROW
    if enable_threshold and i < n_days - 1:
        drift_post = compute_drift(compute_weights(shares, prices_today), target_weights, drift_mode)
        breached = find_threshold_triggers(drift_post, tolerances)
        if breached and cooldown_remaining <= 0:
            pending_threshold_breach = True   # will execute next day
```

### 5. TaxEngine — optimizer_msba_v1_engine.py line 46
```python
class TaxEngine:
    # ST rate default 35%, LT rate default 20%, LT threshold 365 days
    def classify(self, open_date, close_date):
        days = (close_date - open_date).days
        return ("LT", self.lt_rate) if days >= self.lt_days else ("ST", self.st_rate)

    def compute_tax(self, gain, gain_type):
        if gain < 0:
            self.st_loss_cf += abs(gain)  # (or lt_loss_cf if LT)
            return 0.0   # losses NEVER create a refund; carried forward only
        # Netting order for ST gain: net against ST carry-forward first, then LT
        # Netting order for LT gain: net against LT carry-forward first, then ST
        taxable = gain
        used = min(taxable, self.st_loss_cf);  taxable -= used;  self.st_loss_cf -= used
        used = min(taxable, self.lt_loss_cf);  taxable -= used;  self.lt_loss_cf -= used
        return taxable * self.st_rate   # (or lt_rate for LT gains)
```

### 6. TAX_OPTIMAL lot selection — Portfolio._sorted_lots_for_sell() line 150
```python
# Sort lots for selling: losses first, then smallest gains
# Within losses: biggest ST loss first (harvests the most tax benefit immediately)
for lot in lots:
    lot["_pnl"] = price - lot["cost_basis"]
    lot["_days"] = (date - lot["open_date"]).days
    lot["_is_loss"] = 1 if lot["_pnl"] < 0 else 0
    lot["_is_lt"] = 1 if lot["_days"] >= self.tax.lt_days else 0
lots.sort(key=lambda x: (-x["_is_loss"], x["_is_lt"], x["_pnl"]))
# Result: [biggest ST loss, other ST losses, LT losses, smallest ST gain, ..., biggest LT gain]
```

### 7. TLH loop — run_optimizer_simulation() line 444
```python
# Each trading day, if tlh_threshold > 0:
for tk in tickers:
    for lot in pf._open_lots(tk):
        unrealized_pct = (prices_today[tk] - lot["cost_basis"]) / lot["cost_basis"]
        if unrealized_pct <= -tlh_threshold:   # e.g. -0.05 for 5% loss threshold
            lots_to_harvest.append((lot["lot_id"], lot["shares"]))

for lot_id, lot_shares in lots_to_harvest:
    pf.sell(dt, tk, lot_shares, prices_today[tk], lot_selection="TAX_OPTIMAL")
    pf.buy(dt, tk, lot_shares, prices_today[tk], source="TLH_REBUY")
    # Effect: loss is locked in carry-forward; new lot has zero unrealized gain;
    # transaction costs are deducted from cash on both legs
```

### 8. Transaction costs in Portfolio.buy() / sell()
```python
# On buy:
exec_cost = gross * self._cost_rate        # _cost_rate = (commission + slippage + bid_ask) / 10_000
total_cash_needed = gross + exec_cost      # cost deducted from cash, increases cost basis
cost_basis = (gross + exec_cost) / shares  # cost basis includes transaction cost

# On sell:
exec_cost = proceeds * self._cost_rate
net_proceeds = proceeds - exec_cost        # execution cost reduces cash received
# Tax on gain is then computed and ALSO deducted from cash immediately

# Calendar/Threshold engines (portfolio_returns_engine.py): costs NOT deducted from NAV.
# Shown as an estimate only: est_cost = turnover_dollars * total_cost_rate
```
'''


# ── Code context ───────────────────────────────────────────────────────────────

def build_data_namespace(r: dict) -> dict:
    """
    Extract raw DataFrames and Series from session_state['_r'].

    Returns a dict of variable_name → object. Only includes objects that are
    non-empty and useful for chart generation. Formatted display tables
    (metrics_df, rank_df, etc.) are included but noted as string-formatted.

    Key structural notes (important for chart code correctness):
      - comparison_df: DATE is the INDEX (name="PRICEDATE"), columns = strategy names
      - daily_df: "PRICEDATE" is a COLUMN (not index)
      - nav_static / nav_optimized: pd.Series with date index, float NAV values
    """
    ns: dict = {}

    # Buy-and-hold daily time series.
    # Columns: PRICEDATE (column), Portfolio Value, Cost Basis, {Ticker} Return (%)
    daily = r.get("daily")
    if isinstance(daily, pd.DataFrame) and not daily.empty:
        ns["daily_df"] = daily

    # Holdings — one row per ticker
    holdings = r.get("holdings")
    if isinstance(holdings, pd.DataFrame) and not holdings.empty:
        ns["holdings_df"] = holdings

    # Rebalancing outputs
    rebal = r.get("rebal") or {}
    if rebal.get("has_strategies"):
        # comparison_df: raw float portfolio values, date as INDEX
        for src_key, ns_name in [
            ("comparison_df", "comparison_df"),
            ("metrics_df",    "metrics_df"),    # string-formatted
            ("rank_df",       "rank_df"),        # string-formatted
            ("cost_df",       "cost_df"),        # string-formatted
            ("dd_df",         "dd_df"),          # string-formatted
        ]:
            val = rebal.get(src_key)
            if isinstance(val, pd.DataFrame) and not val.empty:
                ns[ns_name] = val

        # Combine per-strategy event logs into one DataFrame
        event_logs = rebal.get("event_logs") or {}
        dfs = [
            df.assign(strategy=name)
            for name, df in event_logs.items()
            if isinstance(df, pd.DataFrame) and not df.empty
        ]
        if dfs:
            ns["event_log_df"] = pd.concat(dfs, ignore_index=True)

    # Optimizer results (raw pd.Series from run_optimizer_simulation)
    opt = r.get("opt") or {}
    for result_key, ns_name in [("static_result", "nav_static"), ("opt_result", "nav_optimized")]:
        result = opt.get(result_key) or {}
        nav = result.get("nav_series")
        if isinstance(nav, pd.Series) and len(nav) > 0:
            ns[ns_name] = nav

    tlh_df = opt.get("tlh_df")
    if isinstance(tlh_df, pd.DataFrame) and not tlh_df.empty:
        ns["tlh_df"] = tlh_df  # string-formatted summary table

    return ns


def describe_namespace(ns: dict) -> str:
    """Return a plain-text inventory of available data objects for the system prompt."""
    if not ns:
        return "  (none — the user has not run a calculation yet)"
    lines = []
    for name, obj in ns.items():
        if isinstance(obj, pd.DataFrame):
            idx_note = (
                f", index.name='{obj.index.name}'"
                if obj.index.name
                else ", index=RangeIndex"
            )
            lines.append(
                f"  • `{name}`: DataFrame  shape={obj.shape}{idx_note}\n"
                f"    columns={list(obj.columns)}"
            )
        elif isinstance(obj, pd.Series):
            lines.append(
                f"  • `{name}`: Series  length={len(obj)}, "
                f"index.name='{obj.index.name}', dtype={obj.dtype}"
            )
        else:
            lines.append(f"  • `{name}`: {type(obj).__name__}")
    return "\n".join(lines)


# ── Chart code parsing & execution ─────────────────────────────────────────────

def extract_chart_code(text: str) -> str | None:
    """Return the first <CHART_CODE>...</CHART_CODE> block from agent output, or None."""
    m = _CHART_CODE_RE.search(text)
    return m.group(1).strip() if m else None


def clean_response_text(text: str) -> str:
    """Replace raw <CHART_CODE> blocks with a readable placeholder for display."""
    return _CHART_CODE_RE.sub("\n*(chart rendered below)*\n", text).strip()


def execute_chart_code(code: str, data_namespace: dict) -> tuple:
    """
    Execute chart-generating Python code in a restricted namespace.

    The code must assign a plotly Figure to `fig`.
    Returns (figure, error_string) — exactly one will be None.

    Security:
      - Restricted builtins block import, open, exec, eval, os, sys
      - Only pd, np, go, px + user data objects are in scope
      - All exceptions are caught and returned as error strings
    """
    exec_ns: dict = {
        "pd": pd,
        "np": np,
        "go": go,
        "px": px,
        "fig": None,
        "__builtins__": _SAFE_BUILTINS,
    }
    exec_ns.update(data_namespace)

    try:
        exec(code, exec_ns)  # noqa: S102
        fig = exec_ns.get("fig")
        if fig is None:
            return None, (
                "Code executed but `fig` was not assigned. "
                "Make sure the final figure is assigned to a variable named `fig`."
            )
        if not isinstance(fig, go.Figure):
            return None, (
                f"Expected `fig` to be a plotly.graph_objects.Figure, "
                f"got {type(fig).__name__}."
            )
        return fig, None
    except Exception:
        return None, traceback.format_exc()
