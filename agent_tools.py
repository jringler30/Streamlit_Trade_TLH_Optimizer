"""
agent_tools.py — Utilities for the TLH Dashboard AI Assistant.

Responsibilities:
  - load_code_context(): read source files into a single string for the system prompt
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


# ── Data namespace ─────────────────────────────────────────────────────────────

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
