![CI](https://github.com/jringler30/portfolio-tlh-optimizer/actions/workflows/ci.yml/badge.svg)

# Portfolio Returns Calculator

### Tax-Aware Portfolio Simulation & Optimization Engine

![Dashboard](Dashboard.png)

> Bloomberg-terminal dark UI — Portfolio Returns Calculator with tax-aware optimizer (v4, March 2026)

---

## Overview

The Portfolio Returns Calculator is an interactive portfolio analytics platform designed to simulate investment performance under rebalancing and tax-loss harvesting strategies.

Developed as part of the **UT Austin MSBA Vise Capstone**, this tool provides a production-style interface for testing portfolio allocation decisions, evaluating tax-aware optimization logic, and visualizing return dynamics across multiple strategy configurations.

Unlike notebook-based workflows, this system exposes portfolio logic through a structured UI intended to resemble institutional portfolio analytics tooling.

---

## Changelog

### v4 — March 2026 (Current)
- Threshold/drift-band rebalancing with configurable cooldown period
- Full rebalance event log with trade-level detail
- Enhanced performance metrics: skewness, kurtosis, tracking error, information ratio
- AI Assistant page (page 06) — Claude-powered chat with full source code context, capable of generating custom charts
- Transaction cost configuration: commission, slippage, and bid-ask spread inputs in sidebar
- Excel export for all key tables via paired download buttons
- Bloomberg-terminal dark theme with `#4fffb0` green accent

### v3 — Early 2026
- Calendar-based rebalancing: Daily / Weekly / Monthly / Quarterly
- Explainer documentation pages (pages 01–05): Core Engine, Tax Engine ST/LT, Sell Handling & TLH, Dividends & Cashflows, Valuation & Performance
- MSBA v1 optimizer: full cost deduction from cash on buy/sell, `DEFAULT_COST_CONFIG` parameter

### v2 — Late 2025
- Daily rebalancing engine
- Short-term vs. long-term capital gains classification (ST=35%, LT=20%)
- Loss carry-forward netting across tax years
- DRIP dividend reinvestment with per-lot tracking
- TAX_OPTIMAL lot selection (FIFO / LIFO / Tax-Optimal)

### v1 — Initial Release
- Buy-and-hold portfolio with daily price series
- Multi-asset support (ETFs, equities, bonds)
- Historical price loading from Google Drive (parquet)
- Dividend mapping via `dividend_data.csv`
- Basic Streamlit UI with portfolio weight editor

---

## Core Capabilities

### Portfolio Simulation Engine

* Historical price-based portfolio return calculation
* Multi-asset support (ETFs, equities, bonds)
* Weighted allocation modeling

### Rebalancing Framework

Supports configurable rebalance frequencies:

* Daily
* Weekly
* Monthly
* Quarterly
* Threshold/drift-band with cooldown

Automatically tracks:

* Holdings drift
* Trade execution
* Allocation restoration

---

### Tax-Loss Harvesting Logic

The optimizer includes a tax-aware simulation layer that:

* Detects unrealized losses exceeding a configurable threshold
* Simulates sell-and-replace TLH actions with immediate rebuy
* Tracks realized gains/losses with ST/LT classification
* Updates cost basis after trades
* Carries forward unused losses across periods

This allows users to evaluate the potential tax impact of systematic rebalancing decisions.

---

### Cashflow & Dividend Handling

* Dividend mapping via external dataset
* Cash accumulation between events
* Dividend reinvestment (DRIP) into portfolio weights — each reinvestment creates a new lot

---

### AI Assistant (Page 06)

* Claude-powered chat interface embedded in the Streamlit app
* Full source code injected into system prompt for context-aware answers
* Capable of generating and executing custom Plotly charts on live portfolio data
* Requires `ANTHROPIC_API_KEY` environment variable or sidebar input

---

### Interactive Dashboard (Streamlit)

The UI includes:

* Portfolio configuration panel (tickers, weights, dates)
* Holdings weight editor
* Strategy toggle controls
* Rebalancing frequency and threshold controls
* Transaction cost configuration (commission, slippage, bid-ask)
* Return comparison outputs with performance metrics
* Performance visualization (Portfolio Value vs Cost Basis, drawdown, etc.)
* Excel download for all key result tables

Custom styling includes a **Bloomberg-terminal inspired dark theme**.

---

## Architecture

```
User Inputs (Tickers / Weights / Dates)
        ↓
Portfolio Engine (portfolio_returns_engine.py)
        ↓
Rebalancing Logic (V1–V4)
        ↓
Tax Optimization Layer (optimizer_msba_v1_engine.py)
        ↓
Cashflow / Dividend Processing (DRIP)
        ↓
Performance Calculation (Sharpe, IR, drawdown, etc.)
        ↓
Streamlit Visualization Layer + AI Assistant
```

---

## Repository Structure

```
portfolio-tlh-optimizer/
│
├── portfolio_returns_engine.py   # Main Streamlit app + simulation logic
├── optimizer_msba_v1_engine.py   # Tax-aware optimization engine
├── agent_tools.py                # AI assistant utilities
├── ui_style.py                   # Bloomberg dark theme + helper functions
├── dividend_data.csv             # Dividend reference dataset
├── requirements.txt              # Python dependencies
│
├── pages/
│   ├── 01_Core_Engine_Overview.py
│   ├── 02_Tax_Engine_ST_vs_LT.py
│   ├── 03_Sell_Handling_and_TLH.py
│   ├── 04_Dividends_and_Cashflows.py
│   ├── 05_Valuation_and_Performance.py
│   └── 06_AI_Assistant.py
│
├── .streamlit/
│   └── config.toml               # Dark theme, accent #4fffb0
│
└── Backtest/
    └── portfolio_backtest_vise.md
```

---

## Installation

Clone repository:

```bash
git clone https://github.com/jringler30/portfolio-tlh-optimizer.git
cd portfolio-tlh-optimizer
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Running the Application

```bash
streamlit run portfolio_returns_engine.py
```

The dashboard will launch in your browser.

---

## Example Workflow

1. Select number of holdings
2. Enter ticker symbols
3. Assign portfolio weights
4. Choose investment horizon
5. Configure rebalancing strategy and transaction costs
6. Toggle TLH optimizer
7. Run simulation

The engine will compute:

* Portfolio value trajectory
* Strategy comparison outputs (Buy & Hold vs. Rebalanced vs. Optimized)
* Trade counts and transaction costs
* Gain/loss tracking with tax impact

---

## Technology Stack

* Python
* Streamlit
* Pandas / NumPy
* Plotly
* Anthropic Claude API (AI Assistant)
* openpyxl (Excel export)

---

## Intended Use

This system is designed for:

* Portfolio strategy experimentation
* Tax-aware optimization simulation
* Financial analytics demonstrations
* Capstone research deliverables

This project is **not intended for live trading execution**.

---

## Author

Joshua Ringler
MS Business Analytics — University of Texas at Austin

---

## License

MIT License
