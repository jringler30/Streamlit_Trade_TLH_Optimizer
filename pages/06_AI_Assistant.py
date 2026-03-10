"""
pages/06_AI_Assistant.py — In-app AI Assistant for the TLH Portfolio Dashboard.

Capabilities:
  1. Code explanation: reads actual source files, cites file + function + line
  2. Dynamic visualization: generates Plotly charts from dashboard DataFrames

Architecture:
  - No LangChain / no vector DB. Full source code in system prompt (fits in context).
  - Anthropic Claude API via `anthropic` SDK.
  - Chart code executed safely via restricted exec() in agent_tools.execute_chart_code().
  - Reads st.session_state["_r"] to access the same DataFrames the dashboard uses.
"""
import os
import sys
from pathlib import Path

import streamlit as st

# ── Ensure repo root is importable ────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ── Optional dependencies ──────────────────────────────────────────────────────
try:
    import anthropic as _anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

try:
    from ui_style import inject_site_css, render_footer
    _HAS_UI = True
except ImportError:
    _HAS_UI = False

from agent_tools import (
    build_data_namespace,
    clean_response_text,
    describe_namespace,
    execute_chart_code,
    extract_chart_code,
    load_code_context,
)

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Assistant — TLH Dashboard",
    page_icon="🤖",
    layout="wide",
)
if _HAS_UI:
    inject_site_css()

# ── Session state init ─────────────────────────────────────────────────────────
if "ai_chat" not in st.session_state:
    st.session_state["ai_chat"] = []  # list of {role, content, fig?}

# Load and cache source code once per session (files don't change mid-session)
if "ai_code_ctx" not in st.session_state:
    with st.spinner("Loading source context…"):
        st.session_state["ai_code_ctx"] = load_code_context()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### AI Assistant")

    # API key — prefer environment variable, fall back to text input
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = st.text_input(
            "Anthropic API Key",
            type="password",
            placeholder="sk-ant-…",
            help="Get a key at console.anthropic.com. Stored in memory only for this session.",
        )
    else:
        st.success("API key loaded from environment")

    st.divider()

    # Data status
    _r_sidebar = st.session_state.get("_r") or {}
    _rebal_ready = (_r_sidebar.get("rebal") or {}).get("has_strategies", False)
    _opt_ready   = bool((_r_sidebar.get("opt") or {}).get("opt_result"))

    if _r_sidebar:
        st.success("Dashboard data ready")
        if _rebal_ready:
            st.caption("✓ Rebalancing strategies computed")
        if _opt_ready:
            st.caption("✓ TLH optimizer results computed")
    else:
        st.warning(
            "No data yet — run a calculation in the main dashboard "
            "to enable charting."
        )

    st.divider()

    model_choice = st.selectbox(
        "Model",
        ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
        index=0,
    )
    max_tokens = st.slider("Max response tokens", 512, 8192, 4096, 512)

    if st.button("Clear Chat", use_container_width=True):
        st.session_state["ai_chat"] = []
        st.rerun()

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="margin-bottom:1.5rem">
      <p style="font-family:'DM Mono',monospace;font-size:0.7rem;letter-spacing:0.15em;
                color:#4fffb0;text-transform:uppercase;margin:0">
        Portfolio Analytics Engine
      </p>
      <h1 style="font-family:'Fraunces',serif;font-size:2rem;color:#eef2ff;margin:0.2rem 0 0.4rem">
        AI Assistant
      </h1>
      <p style="color:#6b7794;font-size:0.9rem;margin:0">
        Ask how the dashboard works, trace a metric to its source, or request a chart from live data.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Example prompts ────────────────────────────────────────────────────────────
with st.expander("Example questions", expanded=False):
    st.markdown(
        """
        **Code explanation**
        - *How is turnover calculated?*
        - *Which file controls the rebalance threshold logic?*
        - *How are transaction costs incorporated?*
        - *How does TLH work — walk me through the daily loop?*
        - *What is TAX_OPTIMAL lot selection and how does it differ from FIFO?*
        - *How is the Sharpe ratio computed?*

        **Dynamic visualization** *(requires a calculation to be run first)*
        - *Plot cumulative portfolio value for all rebalancing strategies*
        - *Show drawdowns by strategy over time*
        - *Compare transaction costs across strategies*
        - *Plot the buy-and-hold portfolio value vs cost basis*
        - *Show a bar chart of holdings by end value*
        - *Visualize NAV for static (TLH only) vs optimized (rebal+TLH)*
        """
    )

st.markdown("---")

# ── Guard: missing anthropic library ──────────────────────────────────────────
if not _HAS_ANTHROPIC:
    st.error(
        "The `anthropic` package is not installed.\n\n"
        "```\npip install anthropic\n```\n\n"
        "Add it to `requirements.txt` and restart the app."
    )
    st.stop()

if not api_key:
    st.info("Enter your Anthropic API key in the sidebar to start chatting.")
    st.stop()

# ── Build data namespace from latest session state ─────────────────────────────
# Re-built on each render so the agent always sees the most current data.
_r_current = st.session_state.get("_r") or {}
_data_ns    = build_data_namespace(_r_current) if _r_current else {}
_data_desc  = describe_namespace(_data_ns)

# ── System prompt ──────────────────────────────────────────────────────────────
# Rebuilt each render to reflect the latest data state.
# Code context is read from session cache (loaded once at startup).
_SYSTEM = f"""You are a technical AI assistant embedded inside a Streamlit portfolio analytics dashboard, built as part of a UT Austin MSBA capstone project in collaboration with Vise.

This dashboard is a tax-loss harvesting (TLH) and portfolio rebalancing optimization tool. It lets users compare buy-and-hold, calendar rebalancing (Daily/Weekly/Monthly/Quarterly), and threshold/drift-band rebalancing strategies — with a full tax-aware lot-level simulation engine for TLH and dividends.

## Your Two Capabilities

### 1. Code Explanation
You have the COMPLETE source code of this dashboard at the bottom of this prompt.
When asked how something works:
- Inspect the actual source code below — do not guess
- Cite the file name, function name, and approximate line number
- Reference real variable names, formulas, and data structures from the code
- Explain the actual logic, not a generic description
- If the user asks about something not in the source, say so clearly

### 2. Dynamic Visualization
When asked for a chart, write Python/Plotly code and wrap it in `<CHART_CODE>...</CHART_CODE>` tags.
The code runs in a sandboxed exec() namespace.

**Available data objects right now:**
{_data_desc}

**Namespace pre-loaded:** `pd` (pandas), `np` (numpy), `go` (plotly.graph_objects), `px` (plotly.express)

**Critical data structure notes — read carefully before writing chart code:**
- `comparison_df`: DATE is the **INDEX** (index.name="PRICEDATE"), columns are strategy names with raw float portfolio values.
  - Correct: `comparison_df.index` for dates, `comparison_df["Buy & Hold"]` for values
  - Wrong: `comparison_df["PRICEDATE"]` — this column does NOT exist
- `daily_df`: "PRICEDATE" is a **column** (not the index). Other columns: "Portfolio Value", "Cost Basis", one "{{Ticker}} Return (%)" column per holding.
- `nav_static`, `nav_optimized`: pd.Series with date INDEX, raw float NAV values
- `metrics_df`, `rank_df`, `cost_df`, `dd_df`: formatted display tables — values are STRINGS like "10.5%", "$1,234". Good for display, NOT directly for numeric plotting.
- `event_log_df`: columns include `date`, `reason`, `breached_tickers`, `max_drift`, `turnover_dollars`, `strategy`
- `holdings_df`: one row per ticker, columns: Ticker, Weight (float), Start Price, End Price, Shares, Start Value, End Value, Return, Gain ($), Gain (%), Start Date, End Date

**Chart styling rules (match the Bloomberg-dark dashboard theme):**
- Dark theme: `paper_bgcolor="#0b0e14"`, `plot_bgcolor="#12161f"`, `font_color="#d4dbe8"`
- Grid lines: `gridcolor="#232a3a"`
- Primary accent: `"#4fffb0"` (green)
- Secondary: `"#7b8cff"` (purple)
- Warning / negative: `"#ff8c61"` (orange)
- Buy & Hold baseline: `"#1a73e8"` (blue)
- Strategy palette (in order): `["#4fffb0","#7b8cff","#ff8c61","#ffd166","#06d6a0","#ef476f"]`
- Set `margin=dict(l=40, r=20, t=50, b=40)` for clean spacing
- Use `showlegend=True` and a clean, descriptive title

**Chart code rules:**
- The final figure MUST be assigned to a variable named exactly `fig`
- Do NOT write `import` statements — all libraries are pre-loaded
- Do NOT use `open()`, `os`, `sys`, `exec`, `eval`, or `__import__`
- Check for missing data before plotting: `if obj is not None and len(obj) > 0:`
- If the data needed for a chart is not available, explain what the user needs to compute first
  (e.g., "Enable rebalancing in the sidebar and re-run to get comparison_df")

## Behavioral Guidelines
- For code questions: always inspect the source below and cite specifically
- For chart requests: generate working code if the right data is available
- You may combine a text explanation + a chart in a single response
- Be precise and technical — this is a capstone project that will be defended to reviewers
- Do not bluff — if something is not in the code or data, say so directly

## Complete Source Code
{st.session_state["ai_code_ctx"]}
"""

# ── Render existing chat history ───────────────────────────────────────────────
for msg in st.session_state["ai_chat"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("fig") is not None:
            st.plotly_chart(msg["fig"], use_container_width=True)

# ── Chat input ─────────────────────────────────────────────────────────────────
user_input = st.chat_input(
    "Ask about the code or request a chart… "
    "e.g. 'How is turnover calculated?' or 'Plot cumulative returns by strategy'"
)

if user_input:
    # ── Append and display user message ───────────────────────────────────────
    st.session_state["ai_chat"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Build message list for the API (text content only — no figures)
    api_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state["ai_chat"]
    ]

    # ── Call Claude ────────────────────────────────────────────────────────────
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                client = _anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model=model_choice,
                    max_tokens=max_tokens,
                    system=_SYSTEM,
                    messages=api_messages,
                )
                raw_text: str = response.content[0].text
            except _anthropic.AuthenticationError:
                st.error(
                    "Authentication failed — check that your API key is correct."
                )
                st.stop()
            except _anthropic.RateLimitError:
                st.error("Rate limit hit. Wait a moment and try again.")
                st.stop()
            except Exception as exc:
                st.error(f"API error: {exc}")
                st.stop()

        # ── Parse response ─────────────────────────────────────────────────────
        chart_code   = extract_chart_code(raw_text)
        display_text = clean_response_text(raw_text)

        st.markdown(display_text)

        # ── Execute and render chart (if present) ──────────────────────────────
        rendered_fig = None
        if chart_code:
            # Always pull the freshest data from session state at execution time
            current_r  = st.session_state.get("_r") or {}
            current_ns = build_data_namespace(current_r) if current_r else {}

            fig, err = execute_chart_code(chart_code, current_ns)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)
                rendered_fig = fig
            else:
                st.error(
                    f"**Chart execution failed.** The agent's code raised an error:\n"
                    f"```\n{err}\n```\n"
                    f"You can ask the agent to fix it by replying: *'Fix the chart error above.'*"
                )

    # ── Persist to chat history ────────────────────────────────────────────────
    entry: dict = {"role": "assistant", "content": display_text}
    if rendered_fig is not None:
        entry["fig"] = rendered_fig
    st.session_state["ai_chat"].append(entry)

# ── Footer ─────────────────────────────────────────────────────────────────────
if _HAS_UI:
    render_footer()
