"""
pages/06_AI_Assistant.py — AI Assistant for the TLH Portfolio Dashboard.

Efficiency architecture:
  - classify_query() → pure Python intent detection, ZERO extra API calls
  - build_prompt()   → query-specific minimal system prompt (~500–800 tokens)
  - History capped   → last 3 exchanges (6 messages) max
  - ONE API call per user message, always
  - No large static context sent regardless of question type
"""
import os
import sys
import time
from pathlib import Path

import streamlit as st

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from google import genai as _genai
    from google.genai import types as _gtypes
    _HAS_GEMINI = True
except ImportError:
    _HAS_GEMINI = False

try:
    from ui_style import inject_site_css, render_footer
    _HAS_UI = True
except ImportError:
    _HAS_UI = False

from agent_tools import (
    build_data_namespace,
    build_prompt,
    clean_response_text,
    classify_query,
    describe_namespace,
    execute_chart_code,
    extract_chart_code,
)

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="AI Assistant — TLH Dashboard", page_icon="🤖", layout="wide")
if _HAS_UI:
    inject_site_css()

# ── Session state ──────────────────────────────────────────────────────────────
if "ai_chat" not in st.session_state:
    st.session_state["ai_chat"] = []   # [{role, content, fig?}, ...]

# Max conversation turns to include in API history (caps token growth)
_MAX_HISTORY_TURNS = 3   # = 6 messages max sent to API

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### AI Assistant")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        api_key = st.text_input(
            "Gemini API Key", type="password", placeholder="AIza…",
            help="Free key from aistudio.google.com — no credit card required.",
        )
    else:
        st.success("API key loaded from environment")

    st.divider()

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
        st.warning("No data yet — run a calculation in the main dashboard to enable charting.")

    st.divider()

    model_choice = st.selectbox(
        "Model",
        ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"],
        index=0,
        help="gemini-1.5-flash: free tier, stable. gemini-2.0-flash: may be unavailable on some accounts.",
    )
    max_tokens = st.slider("Max response tokens", 512, 4096, 2048, 256)

    if st.button("Clear Chat", use_container_width=True):
        st.session_state["ai_chat"] = []
        st.rerun()

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <div style="margin-bottom:1.5rem">
      <p style="font-family:'DM Mono',monospace;font-size:0.7rem;letter-spacing:0.15em;
                color:#4fffb0;text-transform:uppercase;margin:0">Portfolio Analytics Engine</p>
      <h1 style="font-family:'Fraunces',serif;font-size:2rem;color:#eef2ff;margin:0.2rem 0 0.4rem">
        AI Assistant</h1>
      <p style="color:#6b7794;font-size:0.9rem;margin:0">
        Ask how the dashboard works, trace a metric to its source, or request a chart from live data.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.expander("Example questions", expanded=False):
    st.markdown("""
    **Code explanation**
    - *How is turnover calculated?*
    - *Which file controls the rebalance threshold logic?*
    - *How are transaction costs incorporated?*
    - *How does TLH work — walk me through the daily loop?*
    - *What is TAX_OPTIMAL lot selection vs FIFO?*
    - *How is the Sharpe ratio computed?*

    **Dynamic visualization** *(run a calculation first)*
    - *Plot cumulative portfolio value for all rebalancing strategies*
    - *Show drawdowns by strategy over time*
    - *Compare transaction costs across strategies*
    - *Plot buy-and-hold portfolio value vs cost basis*
    - *Show a bar chart of holdings by end value*
    - *Visualize NAV: static TLH vs optimized*
    """)

st.markdown("---")

# ── Guards ─────────────────────────────────────────────────────────────────────
if not _HAS_GEMINI:
    st.error("`google-genai` not installed. Run: `pip install google-genai`")
    st.stop()
if not api_key:
    st.info("Enter your Gemini API key in the sidebar. Free key at **aistudio.google.com**.")
    st.stop()

# ── Render chat history ────────────────────────────────────────────────────────
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
    st.session_state["ai_chat"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # ── Build data namespace from latest session state ─────────────────────────
    _r_current = st.session_state.get("_r") or {}
    _data_ns   = build_data_namespace(_r_current) if _r_current else {}

    # ── Build minimal, query-specific system prompt (pure Python — no API call) ─
    _system = build_prompt(user_input, _data_ns)

    # ── Cap history: only send last N turns to the API ─────────────────────────
    # Prevents unbounded token growth across long conversations.
    _all_msgs = st.session_state["ai_chat"]
    _history_msgs = _all_msgs[-(2 * _MAX_HISTORY_TURNS + 1):-1]  # last N turns, exclude current

    history = [
        _gtypes.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[_gtypes.Part(text=m["content"])],
        )
        for m in _history_msgs
    ]

    # ── Gemini API call with exponential backoff ───────────────────────────────
    _api_version = "v1beta" if "2." in model_choice else "v1"
    _contents = history + [_gtypes.Content(role="user", parts=[_gtypes.Part(text=user_input)])]
    _config = _gtypes.GenerateContentConfig(
        system_instruction=_system,
        max_output_tokens=max_tokens,
        temperature=0.2,
    )

    raw_text = ""
    _last_exc = None
    _MAX_RETRIES = 3

    with st.chat_message("assistant"):
        for _attempt in range(_MAX_RETRIES):
            try:
                with st.spinner(
                    "Thinking…" if _attempt == 0
                    else f"Rate limit — retrying ({_attempt}/{_MAX_RETRIES - 1})…"
                ):
                    client = _genai.Client(
                        api_key=api_key,
                        http_options=_gtypes.HttpOptions(api_version=_api_version),
                    )
                    response = client.models.generate_content(
                        model=model_choice, contents=_contents, config=_config,
                    )
                    raw_text = response.text
                break

            except Exception as exc:
                _last_exc = exc
                _s = str(exc)
                if ("429" in _s or "quota" in _s.lower() or "resource_exhausted" in _s.lower()) \
                        and _attempt < _MAX_RETRIES - 1:
                    time.sleep(5 * (2 ** _attempt))
                    continue
                break

        if not raw_text:
            _s = str(_last_exc) if _last_exc else "Unknown error"
            if "401" in _s or "invalid" in _s.lower():
                st.error(f"**Auth failed** — check your API key.\n\n`{_s}`")
            elif "404" in _s or "not found" in _s.lower():
                st.error(f"**Model not found** — switch to `gemini-1.5-flash`.\n\n`{_s}`")
            elif "429" in _s or "quota" in _s.lower() or "resource_exhausted" in _s.lower():
                st.error(f"**Rate limit** after {_MAX_RETRIES} attempts. Wait ~1 min.\n\n`{_s}`")
            else:
                st.error(f"**Gemini error:**\n\n`{_s}`")
            st.stop()

        # ── Parse and display ──────────────────────────────────────────────────
        chart_code   = extract_chart_code(raw_text)
        display_text = clean_response_text(raw_text)
        st.markdown(display_text)

        # ── Execute chart if present ───────────────────────────────────────────
        rendered_fig = None
        if chart_code:
            fig, err = execute_chart_code(chart_code, _data_ns)
            if fig is not None:
                st.plotly_chart(fig, use_container_width=True)
                rendered_fig = fig
            else:
                st.error(
                    f"**Chart execution failed:**\n```\n{err}\n```\n"
                    f"Reply *'Fix the chart error above'* and the agent will correct it."
                )

    entry = {"role": "assistant", "content": display_text}
    if rendered_fig is not None:
        entry["fig"] = rendered_fig
    st.session_state["ai_chat"].append(entry)

if _HAS_UI:
    render_footer()
