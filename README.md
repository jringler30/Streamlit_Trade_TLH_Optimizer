![CI](https://github.com/jringler30/portfolio-tlh-optimizer/actions/workflows/ci.yml/badge.svg)

# Portfolio Returns Calculator

### Tax-Aware Portfolio Simulation & Optimization Engine

![Dashboard](Dashboard.png)

---

## Project Overview

The **Portfolio Returns Calculator** is a portfolio analytics and tax-loss harvesting simulation engine built with Streamlit. It allows users to test portfolio allocations, evaluate rebalancing strategies, and measure the impact of tax-aware optimization logic across historical price data.

Developed as part of the **UT Austin MSBA Vise Capstone**, this tool is designed to resemble an institutional-style analytics interface — purpose-built for simulation and backtesting rather than live trading execution.

Core capabilities:

- Historical price-based portfolio simulation with multi-asset support
- Tax-loss harvesting engine with short-term / long-term gain classification
- Flexible rebalancing strategies (buy-and-hold through threshold drift-band)
- Strategy comparison and performance analytics
- Interactive Streamlit dashboard with Bloomberg-terminal dark theme

---

## Live Application

To run locally:

```bash
streamlit run portfolio_returns_engine.py
```

The dashboard will open in your browser at `http://localhost:8501`.

---

## Key Features

| Feature | Description |
|---|---|
| Portfolio Simulation Engine | Historical return calculation across multi-asset portfolios |
| Tax-Loss Harvesting Logic | Detects unrealized losses, simulates sell-and-rebuy, tracks ST/LT gains |
| Flexible Rebalancing Strategies | Monthly, Quarterly, Yearly, and threshold drift-band |
| Comparative Analysis Engine | 96-run backtest across 3 market regimes × 2 portfolios × 8 strategies |
| Strategy Playbook | Ranked TLH strategy recommendations per market condition |
| Interactive Dashboard | Streamlit UI with sidebar controls and live chart updates |
| Strategy Comparison | Side-by-side metrics: CAGR, Sharpe, max drawdown, tracking error, IR |
| Excel Export | Playbook, value-add deltas, and raw data tabs |
| Transaction Cost Modeling | Commission, slippage, and bid-ask spread inputs |

---

## Project Architecture

```
portfolio-tlh-optimizer/
│
├── portfolio_returns_engine.py        # Main Streamlit app + simulation logic
├── optimizer_msba_v1_engine.py        # Tax-aware TLH engine (lot tracking, wash-sale)
├── ui_style.py                        # Bloomberg dark theme + CSS helpers
├── dividend_data.csv                  # Dividend reference dataset
├── requirements.txt                   # Python dependencies
│
├── Backtest/
│   ├── vise_comparative_analysis.ipynb   # Main analysis: 96-run TLH strategy backtest
│   ├── strategy_playbook.xlsx            # Ranked strategy recommendations (output)
│   └── comparative_analysis_results.csv  # Full results, all 96 runs (output)
│
├── pages/
│   └── 01_Engine_Documentation.py     # Tabbed engine documentation
│
├── tests/
│   └── test_msba_engine.py            # Unit tests for tax engine
│
├── assets/
│   └── Dashboard.png                  # UI screenshot
│
├── archive/                           # Prior exploratory notebooks
│   ├── vise_tlh_backtest_monthly_v2.ipynb
│   ├── vise_rebalancing_recommendation.ipynb
│   └── tlh_multi_security_prototype.ipynb
│
└── docs/
    └── CONTRIBUTING.md
```

---

## Installation

**Clone the repository:**

```bash
git clone https://github.com/jringler30/portfolio-tlh-optimizer.git
cd portfolio-tlh-optimizer
```

**Create and activate a virtual environment:**

```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

**Install dependencies:**

```bash
pip install -r requirements.txt
```

---

## Running the Application

```bash
streamlit run portfolio_returns_engine.py
```

---

## Comparative Analysis

The main deliverable is `Backtest/vise_comparative_analysis.ipynb` — a 96-run combinatorial backtest comparing TLH strategies across:

- **3 market regimes**: Bear (2007–2008), Baseline (2010–2019), Bull (2023–2024)
- **2 portfolios**: 40/60 and 100/0 (Target Allocation ETF family)
- **8 rebalancing strategies**: Monthly, Quarterly, Yearly, Abs 5%/10%/20%, Rel 25%/50%
- **TLH on vs. off** for each strategy

Output: `strategy_playbook.xlsx` with ranked recommendations per regime, TLH value-add deltas, and full raw data.

---

## Engine Documentation

The **Engine Documentation** page (accessible from the sidebar) covers five topics in a tabbed layout:

1. **Core Engine Overview** — simulation loop, V1–V4 engine progression
2. **Tax Engine ST vs LT** — short/long-term classification, carry-forward netting
3. **Sell Handling & TLH** — lot selection modes (FIFO / LIFO / TAX_OPTIMAL), TLH logic
4. **Dividends & Cashflows** — DRIP reinvestment, lot creation, cash handling
5. **Valuation & Performance** — daily NAV calculation, metrics, edge cases

---

## Technology Stack

| Layer | Libraries |
|---|---|
| UI | Streamlit, custom CSS (Bloomberg dark theme) |
| Data | Pandas, NumPy, PyArrow |
| Analytics | SciPy, Plotly |
| Export | openpyxl |
| CI | GitHub Actions (Python 3.11, flake8) |

---

## Author

Joshua Ringler
MS Business Analytics — University of Texas at Austin

---

## License

[MIT License](LICENSE)
