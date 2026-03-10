"""
ui_style.py
===========
Shared style helper for the Portfolio Accounting Engine Streamlit app.
VISE Bloomberg-terminal dark theme.

Embeds the full CSS design system and injects it into Streamlit via
st.markdown(unsafe_allow_html=True).

Usage:
    from ui_style import inject_site_css, render_hero, section_sep, ...
"""

import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# CSS — VISE terminal theme for Streamlit
# ─────────────────────────────────────────────────────────────────────────────

_SITE_CSS = r"""
<style>
/* ── Google Fonts ── */
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,600;0,9..144,700;1,9..144,300&family=DM+Sans:wght@300;400;500&display=swap');

:root {
    --bg: #0b0e14;
    --surface: #12161f;
    --surface2: #1a1f2e;
    --border: #232a3a;
    --accent: #4fffb0;
    --accent2: #7b8cff;
    --accent3: #ff8c61;
    --text: #d4dbe8;
    --text-muted: #6b7794;
    --text-dim: #3d4866;
    --heading: #eef2ff;
    --input-bg: #141924;
    --input-border: #2a3148;
    --input-focus: rgba(79,255,176,0.35);
}

/* ════════════════════════════════════════════════════════════════
   A. STREAMLIT CHROME — footer and deploy menu hidden; header kept
   ════════════════════════════════════════════════════════════════ */

footer { display: none !important; }
.reportview-container .main footer { display: none !important; }

button[title="View app in Streamlit Community Cloud"] { display: none !important; }

/* ════════════════════════════════════════════════════════════════
   B. APP & SIDEBAR BACKGROUNDS
   ════════════════════════════════════════════════════════════════ */

.stApp,
[data-testid="stAppViewContainer"],
.main,
.main .block-container,
[data-testid="stAppViewBlockContainer"] {
    background-color: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 300 !important;
}

section[data-testid="stSidebar"] {
    background-color: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
}

section[data-testid="stSidebar"] > div:first-child {
    background-color: var(--surface) !important;
}

section[data-testid="stSidebar"] * {
    color: var(--text) !important;
}

section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stDateInput label,
section[data-testid="stSidebar"] .stTextInput label,
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stSlider label {
    color: var(--text-muted) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.85rem !important;
}

section[data-testid="stSidebar"] h1 {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.72rem !important;
    color: var(--accent) !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase !important;
    font-weight: 500 !important;
}

section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    color: var(--heading) !important;
}

/* ════════════════════════════════════════════════════════════════
   C. ALL INPUT WIDGETS — DARK
   ════════════════════════════════════════════════════════════════ */

[data-baseweb="input"],
[data-baseweb="select"] > div,
[data-baseweb="base-input"],
[data-baseweb="popover"] > div {
    background-color: var(--input-bg) !important;
    border-color: var(--input-border) !important;
    color: var(--text) !important;
}

[data-baseweb="input"] input,
[data-baseweb="base-input"] input,
.stTextInput input,
.stNumberInput input {
    background-color: var(--input-bg) !important;
    color: var(--text) !important;
    border-color: var(--input-border) !important;
    caret-color: var(--accent) !important;
    -webkit-text-fill-color: var(--text) !important;
}

[data-baseweb="select"] [data-baseweb="tag"],
[data-baseweb="select"] span,
.stSelectbox [data-baseweb="select"] > div {
    background-color: var(--input-bg) !important;
    color: var(--text) !important;
}

[data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] ul,
[data-baseweb="select"] [role="listbox"] {
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
}

[data-baseweb="menu"] li,
[data-baseweb="select"] [role="option"],
[role="listbox"] [role="option"] {
    background-color: var(--surface) !important;
    color: var(--text) !important;
}

[data-baseweb="menu"] li:hover,
[data-baseweb="select"] [role="option"]:hover,
[role="listbox"] [role="option"]:hover {
    background-color: var(--surface2) !important;
    color: var(--accent) !important;
}

[data-baseweb="menu"] li[aria-selected="true"],
[role="option"][aria-selected="true"] {
    background-color: rgba(79,255,176,0.08) !important;
    color: var(--accent) !important;
}

[data-baseweb="input"] input::placeholder,
[data-baseweb="base-input"] input::placeholder,
.stTextInput input::placeholder,
.stNumberInput input::placeholder {
    color: var(--text-dim) !important;
    -webkit-text-fill-color: var(--text-dim) !important;
}

[data-baseweb="input"]:focus-within,
[data-baseweb="base-input"]:focus-within,
[data-baseweb="select"]:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 1px var(--input-focus) !important;
}

.stNumberInput button {
    background-color: var(--surface2) !important;
    border-color: var(--input-border) !important;
    color: var(--text-muted) !important;
}

.stNumberInput button:hover {
    background-color: var(--border) !important;
    color: var(--accent) !important;
}

.stDateInput [data-baseweb="input"] {
    background-color: var(--input-bg) !important;
}

[data-baseweb="calendar"],
[data-baseweb="datepicker"] {
    background-color: var(--surface) !important;
    color: var(--text) !important;
}

[data-baseweb="tag"] {
    background-color: rgba(79,255,176,0.1) !important;
    color: var(--accent) !important;
    border: 1px solid rgba(79,255,176,0.25) !important;
}

.stCheckbox span[data-baseweb="checkbox"] {
    background-color: var(--input-bg) !important;
    border-color: var(--input-border) !important;
}

.stSlider [data-baseweb="slider"] [role="slider"] {
    background-color: var(--accent) !important;
}

.stSlider [data-baseweb="slider"] > div > div {
    background: var(--border) !important;
}

.stRadio [role="radiogroup"] label {
    color: var(--text) !important;
}

label, .stFormLabel {
    color: var(--text-muted) !important;
}

.stTooltipIcon, [data-testid="stTooltipHoverTarget"] {
    color: var(--text-dim) !important;
}

[data-baseweb="toggle"] [role="checkbox"] {
    background-color: var(--input-border) !important;
}

[data-baseweb="toggle"] [role="checkbox"][aria-checked="true"] {
    background-color: var(--accent) !important;
}

/* ════════════════════════════════════════════════════════════════
   D. HEADERS & TYPOGRAPHY
   ════════════════════════════════════════════════════════════════ */

h1, h2, h3 {
    font-family: 'Fraunces', serif !important;
    color: var(--heading) !important;
}

h1 { font-weight: 700 !important; }
h2 { font-weight: 600 !important; }
h3 { font-weight: 600 !important; }

p, li, span, div { color: var(--text); }

.stCaption, [data-testid="stCaptionContainer"] {
    color: var(--text-muted) !important;
}

/* ════════════════════════════════════════════════════════════════
   E. METRIC CARDS — with subtle accent glow
   ════════════════════════════════════════════════════════════════ */

[data-testid="stMetric"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    padding: 1rem 1.25rem !important;
    box-shadow: 0 0 20px -6px rgba(79,255,176,0.06);
    transition: border-color 0.2s, box-shadow 0.2s;
}

[data-testid="stMetric"]:hover {
    border-color: rgba(79,255,176,0.3) !important;
    box-shadow: 0 0 25px -4px rgba(79,255,176,0.12);
}

[data-testid="stMetricLabel"] {
    color: var(--text-muted) !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 0.72rem !important;
    letter-spacing: 0.08em !important;
    text-transform: uppercase !important;
}

[data-testid="stMetricValue"] {
    color: var(--heading) !important;
    font-family: 'Fraunces', serif !important;
    font-weight: 600 !important;
}

[data-testid="stMetricDelta"] {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.78rem !important;
}

/* ════════════════════════════════════════════════════════════════
   F. DATAFRAMES & TABLES
   ════════════════════════════════════════════════════════════════ */

[data-testid="stDataFrame"],
.stDataFrame {
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    overflow: hidden !important;
}

[data-testid="stDataFrame"] [role="grid"],
[data-testid="stDataFrame"] canvas {
    background-color: var(--surface) !important;
}

.stDataFrame > div,
[data-testid="stDataFrameResizable"] {
    background-color: var(--surface) !important;
}

.stTable table,
.stTable thead,
.stTable tbody {
    background-color: var(--surface) !important;
    color: var(--text) !important;
}

.stTable th {
    background-color: var(--surface2) !important;
    color: var(--text-muted) !important;
    border-bottom: 1px solid var(--border) !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 0.78rem !important;
}

.stTable td {
    border-bottom: 1px solid rgba(35,42,58,0.5) !important;
    color: var(--text) !important;
}

/* ════════════════════════════════════════════════════════════════
   G. CHARTS — Vega-Lite (st.line_chart / st.area_chart)
   The critical fix for white chart backgrounds
   ════════════════════════════════════════════════════════════════ */

[data-testid="stVegaLiteChart"] {
    border-radius: 8px !important;
    overflow: hidden !important;
    background-color: transparent !important;
}

[data-testid="stVegaLiteChart"] > div {
    background-color: transparent !important;
}

.vega-embed,
.vega-embed > div,
.vega-embed summary {
    background: transparent !important;
    background-color: transparent !important;
}

.vega-embed canvas {
    background: transparent !important;
}

.vega-embed svg {
    background: transparent !important;
}

/* Plotly containers (if Plotly ever used) */
[data-testid="stPlotlyChart"],
[data-testid="stPlotlyChart"] > div,
.js-plotly-plot,
.plotly,
.plot-container {
    background-color: transparent !important;
}

.js-plotly-plot .plotly .main-svg {
    background: transparent !important;
}

/* Arrow Vega (Streamlit >= 1.28) */
[data-testid="stArrowVegaLiteChart"] {
    background-color: transparent !important;
    border-radius: 8px !important;
}

[data-testid="stArrowVegaLiteChart"] > div {
    background-color: transparent !important;
}

/* ════════════════════════════════════════════════════════════════
   H. BUTTONS
   ════════════════════════════════════════════════════════════════ */

/* Secondary / default buttons */
.stButton > button,
div[data-testid="stButton"] > button {
    background-color: var(--surface2) !important;
    color: var(--text) !important;
    border: 1px solid var(--border) !important;
    font-family: 'DM Sans', sans-serif !important;
    transition: all 0.15s !important;
}

.stButton > button:hover,
div[data-testid="stButton"] > button:hover {
    background-color: var(--border) !important;
    color: var(--heading) !important;
    border-color: var(--text-dim) !important;
}

/* Primary button — high-contrast dark text on accent green */
.stButton > button[kind="primary"],
div[data-testid="stButton"] > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"],
div[data-testid="stButton"] > button[data-testid="baseButton-primary"] {
    background: var(--accent) !important;
    color: #0b0e14 !important;
    -webkit-text-fill-color: #0b0e14 !important;
    font-family: 'DM Mono', monospace !important;
    font-weight: 700 !important;
    font-size: 0.88rem !important;
    letter-spacing: 0.06em !important;
    border: 1px solid rgba(79,255,176,0.5) !important;
    box-shadow: 0 0 12px -2px rgba(79,255,176,0.25),
                inset 0 1px 0 rgba(255,255,255,0.1) !important;
    text-shadow: none !important;
}

.stButton > button[kind="primary"]:hover,
div[data-testid="stButton"] > button[kind="primary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover,
div[data-testid="stButton"] > button[data-testid="baseButton-primary"]:hover {
    background: #5cffbe !important;
    color: #0b0e14 !important;
    -webkit-text-fill-color: #0b0e14 !important;
    border-color: rgba(79,255,176,0.7) !important;
    box-shadow: 0 0 18px -2px rgba(79,255,176,0.35),
                inset 0 1px 0 rgba(255,255,255,0.15) !important;
}

/* FORCE PRIMARY BUTTON TEXT DARK — STREAMLIT WRAPPER FIX */
.stButton > button[kind="primary"] *,
div[data-testid="stButton"] > button[kind="primary"] *,
button[data-testid="baseButton-primary"] * {
    color: #0b0e14 !important;
    -webkit-text-fill-color: #0b0e14 !important;
}


/* Also catch any p/span inside the button that Streamlit may wrap text in */
.stButton > button[kind="primary"] p,
.stButton > button[kind="primary"] span,
div[data-testid="stButton"] > button[kind="primary"] p,
div[data-testid="stButton"] > button[kind="primary"] span,
.stButton > button[data-testid="baseButton-primary"] p,
.stButton > button[data-testid="baseButton-primary"] span {
    color: #0b0e14 !important;
    -webkit-text-fill-color: #0b0e14 !important;
    font-weight: 700 !important;
}

.stDownloadButton > button {
    background-color: var(--surface2) !important;
    color: var(--accent2) !important;
    border: 1px solid var(--border) !important;
}

.stDownloadButton > button:hover {
    border-color: var(--accent2) !important;
}

/* ════════════════════════════════════════════════════════════════
   I. TABS
   ════════════════════════════════════════════════════════════════ */

.stTabs [data-baseweb="tab-list"] {
    border-bottom: 1px solid var(--border) !important;
    gap: 0 !important;
    background: transparent !important;
}

.stTabs [data-baseweb="tab"] {
    font-family: 'DM Mono', monospace !important;
    font-size: 0.75rem !important;
    letter-spacing: 0.06em !important;
    color: var(--text-muted) !important;
    padding: 0.75rem 1.25rem !important;
    background: transparent !important;
}

.stTabs [data-baseweb="tab"]:hover {
    color: var(--text) !important;
}

.stTabs [aria-selected="true"] {
    color: var(--accent) !important;
    border-bottom-color: var(--accent) !important;
}

.stTabs [data-baseweb="tab-panel"] {
    background: transparent !important;
}

/* ════════════════════════════════════════════════════════════════
   J. EXPANDERS
   ════════════════════════════════════════════════════════════════ */

[data-testid="stExpander"] {
    border: 1px solid var(--border) !important;
    border-radius: 8px !important;
    background: var(--surface) !important;
}

[data-testid="stExpander"] summary {
    color: var(--text-muted) !important;
    font-family: 'DM Sans', sans-serif !important;
}

[data-testid="stExpander"] summary:hover {
    color: var(--text) !important;
}

[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    background: var(--surface) !important;
}

/* ════════════════════════════════════════════════════════════════
   K. ALERTS & INFO
   ════════════════════════════════════════════════════════════════ */

[data-testid="stAlert"] {
    background: var(--surface) !important;
    border: 1px solid var(--border) !important;
    border-radius: 6px !important;
    color: var(--text) !important;
}

.stSuccess, .stInfo, .stWarning, .stError {
    background-color: var(--surface) !important;
}

/* ════════════════════════════════════════════════════════════════
   L. MISC — hr, scrollbar, spinner, code, progress
   ════════════════════════════════════════════════════════════════ */

hr {
    border: none !important;
    border-top: 1px solid var(--border) !important;
}

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-dim); }

.stSpinner > div {
    border-top-color: var(--accent) !important;
}

[data-testid="stStatusWidget"] {
    background: var(--surface) !important;
}

.stCodeBlock, pre, code {
    background-color: var(--surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text) !important;
}

/* ════════════════════════════════════════════════════════════════
   M. MULTIPAGE SIDEBAR NAV
   ════════════════════════════════════════════════════════════════ */

[data-testid="stSidebarNav"] {
    background-color: var(--surface) !important;
}

[data-testid="stSidebarNav"] a {
    font-family: 'DM Sans', sans-serif !important;
    color: var(--text-muted) !important;
    font-size: 0.88rem !important;
}

[data-testid="stSidebarNav"] a:hover {
    color: var(--accent) !important;
}

[data-testid="stSidebarNav"] [aria-current="page"] {
    background: var(--surface2) !important;
    border-left: 2px solid var(--accent) !important;
}

[data-testid="stSidebarNav"] [aria-current="page"] a {
    color: var(--accent) !important;
}

[data-testid="stSidebarNav"] li {
    background: transparent !important;
}


/* ════════════════════════════════════════════════════════════════════
   CUSTOM COMPONENT CLASSES (used in st.markdown HTML blocks)
   ════════════════════════════════════════════════════════════════════ */

.vise-hero {
    padding: 2.5rem 0 2rem;
    position: relative;
}

.vise-hero-eyebrow {
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    color: var(--accent);
    letter-spacing: 0.2em;
    text-transform: uppercase;
    margin-bottom: 1rem;
}

.vise-hero-title {
    font-family: 'Fraunces', serif;
    font-size: clamp(2rem, 4vw, 3.2rem);
    font-weight: 700;
    color: var(--heading);
    line-height: 1.1;
    margin-bottom: 1rem;
}

.vise-hero-title em {
    font-style: italic;
    font-weight: 300;
    color: var(--accent);
}

.vise-hero-sub {
    font-size: 1rem;
    color: var(--text-muted);
    max-width: 560px;
    line-height: 1.7;
    margin-bottom: 1.5rem;
}

.vise-formula {
    display: inline-flex;
    align-items: center;
    gap: 0.75rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    padding: 0.85rem 1.25rem;
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    color: var(--heading);
    border-radius: 4px;
}

.vise-formula span { color: var(--accent2); }

.vise-sep {
    display: flex;
    align-items: center;
    gap: 1.25rem;
    margin: 2.5rem 0 1.5rem;
}

.vise-sep-line { flex: 1; height: 1px; background: var(--border); }

.vise-sep-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    color: var(--text-dim);
    letter-spacing: 0.15em;
    text-transform: uppercase;
    white-space: nowrap;
}

.vise-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: var(--accent);
    letter-spacing: 0.2em;
    text-transform: uppercase;
    margin-bottom: 0.5rem;
}

.vise-section-title {
    font-family: 'Fraunces', serif;
    font-size: clamp(1.6rem, 3vw, 2.4rem);
    font-weight: 600;
    color: var(--heading);
    line-height: 1.2;
    margin-bottom: 1rem;
}

.vise-section-intro {
    font-size: 1rem;
    color: var(--text-muted);
    max-width: 640px;
    margin-bottom: 2rem;
    line-height: 1.75;
}

.vise-card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1px;
    background: var(--border);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 2rem;
}

.vise-card {
    background: var(--surface);
    padding: 1.75rem;
    transition: background 0.2s;
}

.vise-card:hover { background: var(--surface2); }

.vise-card-num {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    color: var(--text-dim);
    letter-spacing: 0.15em;
    margin-bottom: 0.6rem;
}

.vise-card-title {
    font-family: 'Fraunces', serif;
    font-size: 1.15rem;
    font-weight: 600;
    color: var(--heading);
    margin-bottom: 0.4rem;
}

.vise-card-tag {
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 0.62rem;
    padding: 0.2rem 0.6rem;
    border-radius: 3px;
    margin-bottom: 0.6rem;
}

.tag-stateful { background: rgba(79,255,176,0.1); color: var(--accent); }
.tag-stateless { background: rgba(123,140,255,0.1); color: var(--accent2); }
.tag-orchestrator { background: rgba(255,140,97,0.1); color: var(--accent3); }

.vise-card p { font-size: 0.86rem; color: var(--text-muted); line-height: 1.6; margin: 0; }

.vise-tax-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1.25rem;
    margin-bottom: 2rem;
}

.vise-tax-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1.5rem;
    position: relative;
    overflow: hidden;
}

.vise-tax-card::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; }
.vise-tax-card.st::before { background: var(--accent3); }
.vise-tax-card.lt::before { background: var(--accent); }
.vise-tax-card.div::before { background: var(--accent2); }

.vise-tax-rate { font-family: 'Fraunces', serif; font-size: 2.2rem; font-weight: 700; line-height: 1; margin-bottom: 0.35rem; }
.vise-tax-card.st .vise-tax-rate { color: var(--accent3); }
.vise-tax-card.lt .vise-tax-rate { color: var(--accent); }
.vise-tax-card.div .vise-tax-rate { color: var(--accent2); }

.vise-tax-name { font-family: 'DM Mono', monospace; font-size: 0.68rem; color: var(--text-muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 0.6rem; }
.vise-tax-desc { font-size: 0.84rem; color: var(--text-muted); line-height: 1.6; }

.vise-principle { display: flex; gap: 0.9rem; align-items: flex-start; padding: 1rem 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 0.6rem; transition: border-color 0.2s; }
.vise-principle:hover { border-color: var(--accent); }
.vise-principle-icon { flex-shrink: 0; width: 28px; height: 28px; border-radius: 50%; background: rgba(79,255,176,0.1); border: 1px solid rgba(79,255,176,0.3); display: flex; align-items: center; justify-content: center; font-size: 0.75rem; color: var(--accent); font-family: 'DM Mono', monospace; margin-top: 0.1rem; }
.vise-principle-title { font-weight: 500; color: var(--heading); font-size: 0.88rem; margin-bottom: 0.15rem; }
.vise-principle-body { font-size: 0.82rem; color: var(--text-muted); line-height: 1.6; }

.vise-steps { position: relative; padding-left: 2.5rem; }
.vise-steps::before { content: ''; position: absolute; left: 14px; top: 20px; bottom: 20px; width: 1px; background: linear-gradient(to bottom, var(--accent), var(--accent2), transparent); opacity: 0.3; }
.vise-step { display: flex; gap: 1rem; align-items: flex-start; padding: 0.9rem 0; position: relative; }
.vise-step-num { position: absolute; left: -2.5rem; width: 30px; height: 30px; border-radius: 50%; background: var(--surface2); border: 1px solid var(--border); display: flex; align-items: center; justify-content: center; font-family: 'DM Mono', monospace; font-size: 0.7rem; color: var(--accent); z-index: 1; }
.vise-step-title { font-weight: 500; color: var(--heading); font-size: 0.9rem; margin-bottom: 0.2rem; }
.vise-step-detail { font-family: 'DM Mono', monospace; font-size: 0.76rem; color: var(--text-muted); line-height: 1.6; }

.vise-code { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 1.1rem 1.25rem; font-family: 'DM Mono', monospace; font-size: 0.78rem; color: var(--text-muted); line-height: 1.7; overflow-x: auto; margin-bottom: 1rem; white-space: pre-wrap; }
.vise-code .kw { color: var(--accent2); }
.vise-code .s { color: var(--accent); }
.vise-code .cm { color: var(--text-dim); }
.vise-code .fn { color: var(--accent3); }

.vise-guardrails { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 2rem; }
.vise-guardrail { background: var(--surface); padding: 1.25rem; transition: background 0.2s; }
.vise-guardrail:hover { background: var(--surface2); }
.vise-guardrail-trigger { font-family: 'DM Mono', monospace; font-size: 0.72rem; color: var(--accent3); margin-bottom: 0.4rem; }
.vise-guardrail-response { font-size: 0.82rem; color: var(--text-muted); line-height: 1.55; }
.vise-guardrail-response strong { color: var(--accent); font-weight: 500; }

.vise-classref { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--accent2); border-radius: 6px; padding: 1.1rem 1.25rem; margin-bottom: 1.25rem; }
.vise-classref-title { font-family: 'DM Mono', monospace; font-size: 0.76rem; color: var(--accent2); margin-bottom: 0.6rem; letter-spacing: 0.05em; }
.vise-classref-method { display: flex; gap: 0.6rem; padding: 0.4rem 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 0.78rem; align-items: flex-start; flex-wrap: wrap; }
.vise-classref-method:last-child { border-bottom: none; }
.vise-crm-sig { font-family: 'DM Mono', monospace; color: var(--accent2); font-size: 0.72rem; flex-shrink: 0; min-width: 180px; }
.vise-crm-desc { color: var(--text-muted); line-height: 1.5; font-size: 0.8rem; }

.vise-outputs { display: flex; flex-direction: column; gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 2rem; }
.vise-output-item { display: grid; grid-template-columns: 200px 1fr; background: var(--surface); transition: background 0.2s; }
.vise-output-item:hover { background: var(--surface2); }
.vise-output-name { padding: 1.1rem 1.25rem; border-right: 1px solid var(--border); font-family: 'Fraunces', serif; font-size: 0.92rem; font-weight: 600; color: var(--heading); display: flex; align-items: center; }
.vise-output-desc { padding: 1.1rem 1.25rem; font-size: 0.84rem; color: var(--text-muted); line-height: 1.6; display: flex; align-items: center; }

.vise-config-block { background: var(--surface); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 1rem; }
.vise-config-header { padding: 0.85rem 1.25rem; border-bottom: 1px solid var(--border); font-family: 'DM Mono', monospace; font-size: 0.7rem; color: var(--text-muted); letter-spacing: 0.1em; text-transform: uppercase; }
.vise-config-row { padding: 0.7rem 1.25rem; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 0.8rem; display: flex; gap: 1rem; align-items: baseline; }
.vise-config-row:last-child { border-bottom: none; }
.vise-config-key { font-family: 'DM Mono', monospace; color: var(--accent2); font-size: 0.76rem; min-width: 180px; }
.vise-config-val { font-family: 'DM Mono', monospace; color: var(--accent); font-size: 0.78rem; min-width: 60px; }
.vise-config-desc { color: var(--text-muted); font-size: 0.78rem; }

.vise-two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-bottom: 2rem; }
@media (max-width: 768px) {
    .vise-two-col { grid-template-columns: 1fr; }
    .vise-output-item { grid-template-columns: 1fr; }
    .vise-output-name { border-right: none; border-bottom: 1px solid var(--border); }
}

.vise-footer { border-top: 1px solid var(--border); padding: 1.5rem 0; display: flex; justify-content: space-between; align-items: center; margin-top: 3rem; }
.vise-footer-brand { font-family: 'DM Mono', monospace; font-size: 0.68rem; color: var(--text-dim); letter-spacing: 0.1em; }

.vise-code-inline { font-family: 'DM Mono', monospace; font-size: 0.8em; background: var(--surface2); border: 1px solid var(--border); padding: 0.1em 0.4em; border-radius: 3px; color: var(--accent2); }

.vise-example-box { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 2rem; }
.vise-example-header { display: grid; grid-template-columns: 1fr 1fr; border-bottom: 1px solid var(--border); }
.vise-example-col { padding: 1.25rem 1.5rem; }
.vise-example-col:first-child { border-right: 1px solid var(--border); }
.vise-example-col-label { font-family: 'DM Mono', monospace; font-size: 0.64rem; color: var(--text-dim); letter-spacing: 0.15em; text-transform: uppercase; margin-bottom: 0.75rem; }
.vise-example-line { display: flex; justify-content: space-between; align-items: baseline; padding: 0.3rem 0; font-size: 0.8rem; border-bottom: 1px solid rgba(255,255,255,0.04); }
.vise-example-line:last-child { border-bottom: none; }
.vise-example-line .label { color: var(--text-muted); }
.vise-example-line .value { font-family: 'DM Mono', monospace; color: var(--heading); }
.vise-example-line .value.pos { color: var(--accent); }
.vise-example-line .value.neg { color: var(--accent3); }
.vise-example-result { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid var(--border); }
.vise-result-item { padding: 1.25rem 1.5rem; border-right: 1px solid var(--border); }
.vise-result-item:last-child { border-right: none; }
.vise-result-label { font-family: 'DM Mono', monospace; font-size: 0.64rem; color: var(--text-muted); letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 0.4rem; }
.vise-result-value { font-family: 'Fraunces', serif; font-size: 1.4rem; font-weight: 600; color: var(--heading); }
.vise-result-value.accent { color: var(--accent); }
.vise-result-value.warn { color: var(--accent3); }

.vise-formula-visual { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; text-align: center; margin-bottom: 1.5rem; }
.vise-formula-big { font-family: 'Fraunces', serif; font-size: 1.4rem; font-weight: 600; color: var(--heading); line-height: 1.3; margin-bottom: 0.75rem; }
.vise-formula-big em { color: var(--accent); font-style: italic; }
.vise-formula-big span { color: var(--accent2); }
</style>
"""


def inject_site_css():
    """Inject the full VISE terminal-dark design system CSS into a Streamlit page."""
    st.markdown(_SITE_CSS, unsafe_allow_html=True)


def render_hero(eyebrow: str, title: str, subtitle: str = "", formula: str = ""):
    """Render a hero block matching the reference site."""
    html = f'<div class="vise-hero">'
    html += f'<div class="vise-hero-eyebrow">{eyebrow}</div>'
    html += f'<div class="vise-hero-title">{title}</div>'
    if subtitle:
        html += f'<div class="vise-hero-sub">{subtitle}</div>'
    if formula:
        html += f'<div class="vise-formula">{formula}</div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def section_sep(number: str, label: str):
    """Render a section separator with number and label."""
    st.markdown(
        f'<div class="vise-sep">'
        f'<div class="vise-sep-line"></div>'
        f'<div class="vise-sep-label">{number} — {label}</div>'
        f'<div class="vise-sep-line"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def section_header(label: str, title: str, intro: str = ""):
    """Render label + title + intro paragraph."""
    html = f'<div class="vise-label">{label}</div>'
    html += f'<div class="vise-section-title">{title}</div>'
    if intro:
        html += f'<div class="vise-section-intro">{intro}</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_footer():
    """Render a site footer."""
    st.markdown(
        '<div class="vise-footer">'
        '<div class="vise-footer-brand">PORTFOLIO ACCOUNTING ENGINE</div>'
        '<div class="vise-footer-brand">UTexas MSBA // VISE</div>'
        '</div>',
        unsafe_allow_html=True,
    )