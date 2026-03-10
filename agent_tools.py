"""
agent_tools.py — Utilities for the TLH Dashboard AI Assistant.

Architecture (efficiency-first):
  - classify_query(): pure Python intent detection — zero API calls
  - get_snippet_for_query(): keyword → minimal code snippet, not full context
  - build_prompt(): assembles a lean, query-specific system prompt
  - build_data_namespace(): extract DataFrames from st.session_state["_r"]
  - describe_namespace(): schema-only summary, no raw values
  - extract_chart_code() / clean_response_text() / execute_chart_code(): chart pipeline

Token budget targets (vs ~5,000 previously):
  - Code question:  ~500–800  tokens (function index + 1 relevant snippet)
  - Chart question: ~400–600  tokens (data schema + chart rules only)
  - General:        ~200–300  tokens (minimal instructions)
"""
import re
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

REPO_ROOT = Path(__file__).parent

# ── Regex for chart code blocks ────────────────────────────────────────────────
_CHART_CODE_RE = re.compile(r"<CHART_CODE>(.*?)</CHART_CODE>", re.DOTALL)

# ── Safe builtins for chart exec() ────────────────────────────────────────────
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

# ── Query classification keywords ──────────────────────────────────────────────
_CHART_KW = frozenset([
    "plot", "chart", "show", "visualize", "graph", "draw", "display",
    "histogram", "bar", "line", "scatter", "compare", "over time",
    "cumulative", "returns chart", "drawdown chart",
])
_CODE_KW = frozenset([
    "how is", "how does", "how are", "which file", "what function",
    "where is", "calculated", "computed", "implemented", "explain",
    "what file", "where does", "walk me through", "source", "algorithm",
    "formula", "logic", "code", "function", "what controls",
    "what is", "what are", "describe", "tell me about",
])

# ── Code snippets: individually addressable, ~150–250 tokens each ──────────────
_SNIPPETS: dict[str, str] = {

    "metrics": """\
# compute_strategy_metrics() — portfolio_returns_engine.py line 947
# Inputs: daily_values (np.array), initial_capital (float), benchmark_values (optional)
total_return = final / initial_capital - 1
cagr = (final / initial_capital) ** (1 / (n / 252.0)) - 1   # 252 trading days/year
daily_rets = np.diff(daily_values) / daily_values[:-1]        # simple returns (not log)
ann_vol = np.std(daily_rets, ddof=1) * np.sqrt(252)
sharpe = cagr / ann_vol   # Rf = 0
running_max = np.maximum.accumulate(daily_values)
drawdowns = (daily_values - running_max) / running_max
max_dd = float(np.min(drawdowns))
avg_drawdown = float(np.mean(drawdowns))  # mean of ALL daily drawdowns
skewness = scipy.stats.skew(daily_rets)
kurtosis = scipy.stats.kurtosis(daily_rets, fisher=True)  # excess kurtosis
# Tracking error / IR only when benchmark_values provided:
active_rets = daily_rets - bm_rets
tracking_error = np.std(active_rets, ddof=1) * np.sqrt(252)
information_ratio = np.mean(active_rets) * 252 / tracking_error""",

    "turnover": """\
# Turnover — build_rebalanced_series() line 526 (portfolio_returns_engine.py)
# On each rebalance date:
for tk in tickers:
    target_value = target_weights[tk] * total_value
    new_shares = target_value / prices_wide.loc[dt, tk]
    trade_dollars = abs((new_shares - shares[tk]) * prices_wide.loc[dt, tk])
    day_turnover += trade_dollars
    shares[tk] = new_shares
total_turnover_dollars += day_turnover

# After simulation:
turnover_proxy = total_turnover_dollars / np.mean(portfolio_values)  # "× factor"
# Transaction cost estimate (NOT deducted from NAV in calendar/threshold engines):
est_cost = total_turnover_dollars * (commission_bps + slippage_bps + bid_ask_bps) / 10_000
# In the MSBA v1 optimizer, costs ARE deducted from cash on every buy/sell.""",

    "rebal_dates": """\
# _get_rebalance_dates() — portfolio_returns_engine.py line 419
# Returns a set of dates for O(1) membership testing in the simulation loop.
# Daily:     set(dates[1:])  — every trading day after day 0
# Weekly:    first trading day where ISO week number changes (tracks year to avoid boundary bugs)
# Monthly:   first trading day where dt.month != prev_month
# Quarterly: first trading day in {1,4,7,10} where month changed
# Holidays are handled automatically: if the 1st is a holiday, the 2nd becomes the rebalance date.""",

    "threshold": """\
# build_threshold_rebalanced_series() — portfolio_returns_engine.py line 735
# V4 engine: drift-band + optional calendar, no look-ahead bias, cooldown suppression.

# State machine (carried across loop iterations):
pending_threshold_breach = False   # breach detected at close → executes NEXT day
cooldown_remaining = 0             # suppresses threshold triggers for N calendar days

for i, dt in enumerate(dates):
    # 1. Mark-to-market + record drift unconditionally (powers drift diagnostics)
    drift = compute_drift(compute_weights(shares, prices_today), target_weights, drift_mode)

    # 2. Execute YESTERDAY's pending breach (next-day execution = no look-ahead bias)
    if pending_threshold_breach and cooldown_remaining <= 0:
        shares, turnover = apply_rebalance_full(...)  # or partial
        cooldown_remaining = cooldown_days

    # 3. Calendar rebalance fires independently — NOT blocked by cooldown
    if enable_calendar and dt in calendar_dates:
        shares, turnover = apply_rebalance_full(...)

    # 4. End-of-day: detect new breach → schedule for tomorrow
    if enable_threshold and i < n_days - 1:
        breached = find_threshold_triggers(drift_post, tolerances)
        if breached and cooldown_remaining <= 0:
            pending_threshold_breach = True

# Drift modes:
# Absolute: drift[tk] = |current_weight - target_weight|
# Relative: drift[tk] = |current_weight / target_weight - 1|""",

    "tax_engine": """\
# TaxEngine — optimizer_msba_v1_engine.py line 46
# ST rate default 35%, LT rate default 20%, LT threshold = 365 holding days.

def classify(self, open_date, close_date):
    days = (close_date - open_date).days
    return ("LT", self.lt_rate) if days >= self.lt_days else ("ST", self.st_rate)

def compute_tax(self, gain, gain_type):
    if gain < 0:
        self.st_loss_cf += abs(gain)   # losses → carry-forward only, never a refund
        return 0.0
    # Netting for ST gain: offset ST carryforward first, then LT spillover
    # Netting for LT gain: offset LT carryforward first, then ST spillover
    taxable = gain
    used = min(taxable, self.st_loss_cf); taxable -= used; self.st_loss_cf -= used
    used = min(taxable, self.lt_loss_cf); taxable -= used; self.lt_loss_cf -= used
    return taxable * self.st_rate   # (or lt_rate for LT gains)""",

    "lot_selection": """\
# TAX_OPTIMAL lot selection — Portfolio._sorted_lots_for_sell() line 150
# optimizer_msba_v1_engine.py
# Sorts open lots before selling to maximize tax efficiency:
for lot in lots:
    lot["_pnl"] = price - lot["cost_basis"]
    lot["_days"] = (date - lot["open_date"]).days
    lot["_is_loss"] = 1 if lot["_pnl"] < 0 else 0
    lot["_is_lt"] = 1 if lot["_days"] >= self.tax.lt_days else 0
lots.sort(key=lambda x: (-x["_is_loss"], x["_is_lt"], x["_pnl"]))
# Result order: [biggest ST loss → other ST losses → LT losses → smallest gain → biggest gain]
# FIFO alternative: sort by open_date ascending (oldest lots first)
# TAX_OPTIMAL harvests the most immediate tax benefit by realizing losses first.""",

    "tlh_loop": """\
# TLH daily loop — run_optimizer_simulation() line 444 (optimizer_msba_v1_engine.py)
# Runs AFTER dividends, BEFORE rebalancing, on every trading day.

if tlh_threshold > 0:
    for tk in tickers:
        for lot in pf._open_lots(tk):
            unrealized_pct = (prices_today[tk] - lot["cost_basis"]) / lot["cost_basis"]
            if unrealized_pct <= -tlh_threshold:   # e.g. -0.05 for 5% loss
                lots_to_harvest.append((lot["lot_id"], lot["shares"]))

    for lot_id, lot_shares in lots_to_harvest:
        pf.sell(dt, tk, lot_shares, prices_today[tk], lot_selection="TAX_OPTIMAL")
        pf.buy(dt, tk, lot_shares, prices_today[tk], source="TLH_REBUY")
        # Loss is locked in carry-forward; new lot has zero unrealized gain.
        # Both legs incur transaction costs (deducted from cash).""",

    "tx_costs": """\
# Transaction costs — optimizer_msba_v1_engine.py + portfolio_returns_engine.py

# DEFAULT_COST_CONFIG (optimizer engine):
{"commission_bps": 5.0, "slippage_bps": 5.0, "bid_ask_bps": 2.0}
_cost_rate = (5 + 5 + 2) / 10_000 = 0.0012  (12 bps total, one-way)

# On buy (Portfolio.buy, line 165):
exec_cost = gross * _cost_rate
total_cash_needed = gross + exec_cost   # cost_basis includes transaction cost
# On sell (Portfolio.sell, line 200):
net_proceeds = proceeds * (1 - _cost_rate)
# Tax on realized gain ALSO deducted from cash immediately.

# In calendar/threshold engines (portfolio_returns_engine.py):
# Costs are NOT deducted from NAV — shown as an estimate only:
est_cost = total_turnover_dollars * (commission_bps + slippage_bps + bid_ask_bps) / 10_000""",

    "drip": """\
# DRIP / Dividends — Portfolio.process_dividend() line 259 (optimizer_msba_v1_engine.py)
# 6-step sequence:
# 1. Count shares held for the ticker (across all open lots)
# 2. Compute gross dividend = shares * div_per_share
# 3. Add gross to cash (dividend tax not separately modeled in MSBA v1)
# 4. If reinvest=True: buy new shares at today's price
#    - exec_cost deducted, new lot created with today's price as cost_basis
#    - New lot has its own 365-day LT clock, independent of original lots
# 5. DRIP creates a new lot every time — a 5-year position with annual dividends
#    accumulates ~5 lots from DRIP alone.""",
}

# Keyword → snippet name mapping (checked in order; first match wins)
_SNIPPET_ROUTING: list[tuple[set, str]] = [
    ({"tlh", "tax-loss", "harvest", "loss harvest"}, "tlh_loop"),
    ({"turnover"}, "turnover"),
    ({"threshold", "drift", "cooldown", "breach", "drift-band", "drift band"}, "threshold"),
    ({"calendar", "rebalance date", "weekly", "monthly", "quarterly", "schedule"}, "rebal_dates"),
    ({"tax_optimal", "tax optimal", "fifo", "lot selection", "lot select"}, "lot_selection"),
    ({"tax engine", "taxengine", "st rate", "lt rate", "short-term rate", "long-term rate",
      "carry-forward", "loss carry", "classify"}, "tax_engine"),
    ({"transaction cost", "commission", "slippage", "bid-ask", "bid ask", "cost rate",
      "cost config", "execution cost"}, "tx_costs"),
    ({"drip", "dividend reinvest", "dividend"}, "drip"),
    ({"sharpe", "cagr", "volatility", "drawdown", "kurtosis", "skewness", "tracking error",
      "information ratio", "annualized", "metric", "performance metric"}, "metrics"),
]

# Function index — static, ~350 tokens, sent with ALL code questions
_FUNCTION_INDEX = """\
## Function Index

### portfolio_returns_engine.py (main Streamlit app, ~2225 lines)
| Line | Function | Purpose |
|------|----------|---------|
| 64   | to_excel_bytes(dfs) | Serialize dict of DataFrames → Excel bytes |
| 101  | ensure_data() | Download price parquet from Google Drive if not cached |
| 117  | validate_weights() | Deduplicate tickers, normalize weights |
| 154  | prepare_price_data(df) | Filter security_status 1/15, convert dates |
| 222  | calculate_portfolio_returns() | Core buy-and-hold: allocate shares, build holdings DataFrame |
| 324  | build_daily_series() | Time series: Portfolio Value, Cost Basis, per-ticker Return % |
| 387  | build_prices_wide() | Pivot long prices → (date × ticker) matrix |
| 419  | _get_rebalance_dates() | Calendar rebalance date detection by frequency |
| 476  | build_rebalanced_series() | V3 calendar rebalancing simulation |
| 584  | compute_weights() | ticker → current weight dict |
| 593  | compute_drift() | Absolute or relative drift per ticker |
| 623  | find_threshold_triggers() | Tickers exceeding drift tolerance |
| 641  | apply_rebalance_full() | Set all assets to exact target weights |
| 672  | apply_rebalance_partial() | Trade only breached tickers |
| 735  | build_threshold_rebalanced_series() | V4: drift-band + calendar, cooldown, next-day execution |
| 947  | compute_strategy_metrics() | 10 metrics: return, CAGR, vol, Sharpe, max/avg DD, skew, kurt, TE, IR |

### optimizer_msba_v1_engine.py (tax engine, ~499 lines)
| Line | Class/Function | Purpose |
|------|----------------|---------|
| 46   | TaxEngine | ST/LT classification + loss carry-forward netting |
| 101  | Portfolio | Lot-level accounting: cash, lots, trades, realized gains |
| 165  | Portfolio.buy() | Open lot; deduct exec_cost from cash |
| 200  | Portfolio.sell() | Dispose lots (TAX_OPTIMAL/FIFO); classify + compute tax |
| 150  | Portfolio._sorted_lots_for_sell() | TAX_OPTIMAL sort: losses first |
| 259  | Portfolio.process_dividend() | 6-step DRIP sequence |
| 329  | run_optimizer_simulation() | Daily loop: dividends → TLH → rebalancing → NAV |"""

# Chart styling — only included in chart prompts
_CHART_STYLE = """\
Dark theme: paper_bgcolor="#0b0e14", plot_bgcolor="#12161f", font_color="#d4dbe8", gridcolor="#232a3a"
Colors: accent="#4fffb0"(green), secondary="#7b8cff"(purple), warning="#ff8c61"(orange), bh="#1a73e8"(blue)
Palette: ["#4fffb0","#7b8cff","#ff8c61","#ffd166","#06d6a0","#ef476f"]
Layout: margin=dict(l=40,r=20,t=50,b=40), showlegend=True, descriptive title"""

# Data structure notes — only included in chart prompts
_DATA_NOTES = """\
Key structures:
- comparison_df: DATE=INDEX (not a column!), columns=strategy names, values=raw float portfolio $
- daily_df: "PRICEDATE"=column (not index), other cols: "Portfolio Value","Cost Basis","{Ticker} Return (%)"
- nav_static / nav_optimized: pd.Series, date=INDEX, float NAV values
- metrics_df, rank_df, cost_df, dd_df: STRING-formatted display tables (not numeric — don't plot directly)
- holdings_df: one row/ticker; Weight(float), Shares, Start/End Value, Return, Gain($), Gain(%)
- event_log_df: date, reason, breached_tickers, max_drift, turnover_dollars, strategy"""


# ── Query classification (pure Python, zero API calls) ────────────────────────

def classify_query(query: str) -> str:
    """
    Classify user intent using keyword matching. Returns 'chart', 'code', or 'general'.
    No API call — this is purely deterministic Python logic.
    """
    q = query.lower()
    # Chart wins if any strong chart keyword is present
    if any(kw in q for kw in _CHART_KW):
        return "chart"
    # Code wins if any code explanation keyword is present
    if any(kw in q for kw in _CODE_KW):
        return "code"
    return "general"


def get_snippet_for_query(query: str) -> str:
    """
    Match query keywords to the most relevant code snippet.
    Returns the snippet text, or an empty string if no specific match.
    """
    q = query.lower()
    for keywords, snippet_name in _SNIPPET_ROUTING:
        if any(kw in q for kw in keywords):
            return _SNIPPETS.get(snippet_name, "")
    return ""


# ── Minimal, query-specific system prompt ─────────────────────────────────────

_BASE_INSTRUCTION = """\
You are a technical AI assistant inside a Streamlit portfolio analytics dashboard \
(UT Austin MSBA capstone / Vise). The dashboard compares buy-and-hold, calendar \
rebalancing, threshold/drift-band rebalancing, and a tax-aware TLH optimizer."""


def build_prompt(query: str, data_ns: dict) -> str:
    """
    Build a minimal system prompt tailored to the query type.
    Token budget:
      code    → ~500–800 tokens  (index + 1 snippet)
      chart   → ~400–600 tokens  (data schema + chart rules)
      general → ~200–300 tokens  (instructions only)
    """
    intent = classify_query(query)

    if intent == "chart":
        data_desc = describe_namespace(data_ns)
        return f"""{_BASE_INSTRUCTION}

The user wants a chart. Write Python/Plotly code and wrap it in <CHART_CODE>...</CHART_CODE>.
Assign the final figure to `fig`. Do NOT use import statements — pd, np, go, px are pre-loaded.
Do NOT use open/os/sys/exec/eval. Check `if obj is not None and len(obj) > 0` before plotting.

Available data:
{data_desc}

{_DATA_NOTES}

Styling: {_CHART_STYLE}

If the data needed is not available, explain what the user must compute first."""

    if intent == "code":
        snippet = get_snippet_for_query(query)
        snippet_block = (
            f"\nRelevant implementation:\n```python\n{snippet}\n```"
            if snippet else
            "\n(No specific snippet matched — answer from the function index above.)"
        )
        return f"""{_BASE_INSTRUCTION}

The user has a code/logic question. Use the function index and snippet below to answer.
Cite file name, function name, and line number. Reference real variable names from the code.
Do not guess — if something isn't in the index or snippet, say so.

{_FUNCTION_INDEX}
{snippet_block}"""

    # general / ambiguous
    return f"""{_BASE_INSTRUCTION}

Answer the user's question about the dashboard. Be concise and technically accurate.
If it's a code question, ask them to rephrase with "how is X calculated" or "which file controls X".
If it's a chart request, ask them to describe what they want to see."""


# ── Data namespace (for chart exec) ───────────────────────────────────────────

def build_data_namespace(r: dict) -> dict:
    """
    Extract raw DataFrames and Series from session_state['_r'] for chart exec().
    Only includes non-empty objects useful for plotting.
    """
    ns: dict = {}

    daily = r.get("daily")
    if isinstance(daily, pd.DataFrame) and not daily.empty:
        ns["daily_df"] = daily

    holdings = r.get("holdings")
    if isinstance(holdings, pd.DataFrame) and not holdings.empty:
        ns["holdings_df"] = holdings

    rebal = r.get("rebal") or {}
    if rebal.get("has_strategies"):
        for src_key, ns_name in [
            ("comparison_df", "comparison_df"),
            ("metrics_df",    "metrics_df"),
            ("rank_df",       "rank_df"),
            ("cost_df",       "cost_df"),
            ("dd_df",         "dd_df"),
        ]:
            val = rebal.get(src_key)
            if isinstance(val, pd.DataFrame) and not val.empty:
                ns[ns_name] = val

        event_logs = rebal.get("event_logs") or {}
        dfs = [
            df.assign(strategy=name)
            for name, df in event_logs.items()
            if isinstance(df, pd.DataFrame) and not df.empty
        ]
        if dfs:
            ns["event_log_df"] = pd.concat(dfs, ignore_index=True)

    opt = r.get("opt") or {}
    for result_key, ns_name in [("static_result", "nav_static"), ("opt_result", "nav_optimized")]:
        result = opt.get(result_key) or {}
        nav = result.get("nav_series")
        if isinstance(nav, pd.Series) and len(nav) > 0:
            ns[ns_name] = nav

    tlh_df = opt.get("tlh_df")
    if isinstance(tlh_df, pd.DataFrame) and not tlh_df.empty:
        ns["tlh_df"] = tlh_df

    return ns


def describe_namespace(ns: dict) -> str:
    """Schema-only summary of available data objects. No raw values sent to LLM."""
    if not ns:
        return "  (none — run a calculation in the main dashboard first)"
    lines = []
    for name, obj in ns.items():
        if isinstance(obj, pd.DataFrame):
            idx = f"index.name='{obj.index.name}'" if obj.index.name else "RangeIndex"
            lines.append(f"  • `{name}`: DataFrame {obj.shape}, {idx}, cols={list(obj.columns)}")
        elif isinstance(obj, pd.Series):
            lines.append(f"  • `{name}`: Series len={len(obj)}, index.name='{obj.index.name}'")
        else:
            lines.append(f"  • `{name}`: {type(obj).__name__}")
    return "\n".join(lines)


# ── Chart code pipeline ────────────────────────────────────────────────────────

def extract_chart_code(text: str) -> str | None:
    m = _CHART_CODE_RE.search(text)
    return m.group(1).strip() if m else None


def clean_response_text(text: str) -> str:
    return _CHART_CODE_RE.sub("\n*(chart rendered below)*\n", text).strip()


def execute_chart_code(code: str, data_namespace: dict) -> tuple:
    """
    Execute chart code in a restricted namespace. Returns (fig, error) — one is None.
    Security: restricted builtins block import/open/os/sys/exec/eval.
    """
    exec_ns: dict = {
        "pd": pd, "np": np, "go": go, "px": px,
        "fig": None,
        "__builtins__": _SAFE_BUILTINS,
    }
    exec_ns.update(data_namespace)
    try:
        exec(code, exec_ns)  # noqa: S102
        fig = exec_ns.get("fig")
        if fig is None:
            return None, "Code ran but `fig` was not assigned."
        if not isinstance(fig, go.Figure):
            return None, f"Expected go.Figure, got {type(fig).__name__}."
        return fig, None
    except Exception:
        return None, traceback.format_exc()


# ── Legacy loaders (kept for compatibility / paid-API use) ────────────────────

def load_code_context() -> str:
    """Load full source files — only practical for paid APIs with large budgets."""
    parts = []
    for filename in ["portfolio_returns_engine.py", "optimizer_msba_v1_engine.py"]:
        fp = REPO_ROOT / filename
        if fp.exists():
            parts.append(f"### {filename}\n```python\n{fp.read_text()}\n```")
    return "\n\n---\n\n".join(parts)


def load_compact_context() -> str:
    """Return the function index + all snippets combined (~3K tokens). Legacy — prefer build_prompt()."""
    snippets = "\n\n".join(
        f"### {name}\n```python\n{code}\n```"
        for name, code in _SNIPPETS.items()
    )
    return f"{_FUNCTION_INDEX}\n\n---\n\n## Key Algorithm Snippets\n\n{snippets}"
