# Portfolio Comparative Analysis Report
### 12-ETF Institutional Portfolios vs 3-ETF Test Portfolio (SPY / TLT / GLD)
**UT Austin MSBA — Vise Capstone**
*Analysis Date: April 9, 2026*

---

## Portfolio Definitions

### 12-ETF Group (Institutional Model Portfolios)
Three portfolios derived from the Vise Target Allocation ETF model (2025-05-20):

| Portfolio | Description | # Tickers |
|---|---|---|
| 40/60 (TA ETF) | 40% equity / 60% bond allocation | 18 |
| 100/0 (TA ETF) | 100% equity allocation | 9 |
| 2-ETF (IVV/EFA) | Concentrated stress-test (US equity + Intl equity) | 2 |

### 3-ETF Test Portfolio
A deliberately cross-asset portfolio designed to test comparative analysis behavior across uncorrelated exposures:

| Ticker | Weight | Asset Class |
|---|---|---|
| SPY | 40% | US Broad Equity (S&P 500) |
| TLT | 40% | US Long-Term Treasury Bonds (20+ yr) |
| GLD | 20% | Gold — inflation hedge / crisis diversifier |

---

## Simulation Parameters

| Parameter | Value |
|---|---|
| Initial Capital | $1,000,000 |
| Market Periods | Bear (2007–2008), Baseline (2010–2019), Bull (2023–2024) |
| Strategies | Monthly, Quarterly, Yearly rebal + Abs/Rel threshold bands |
| TLH Threshold | 10% loss |
| Tax Rates | ST: 35% / LT: 20% |
| Transaction Costs | 12 bps (5 commission + 5 slippage + 2 bid-ask) |
| Total Simulation Runs | 192 (144 for 12-ETF group, 48 for 3-ETF) |

---

## Section 1 — Overall Performance (All Strategies, All Periods)

| Metric | 12-ETF | 3-ETF | Advantage |
|---|---|---|---|
| Avg CAGR | 0.97% | **7.77%** | 3-ETF +6.80% |
| Avg Sharpe Ratio | 0.42 | **0.93** | 3-ETF +0.51 |
| Avg Max Drawdown | -24.05% | **-12.91%** | 3-ETF (shallower by 11%) |
| Avg Volatility (Ann) | 16.58% | **9.37%** | 3-ETF (lower by 7.2%) |
| Avg Total Return | 20.65% | **29.02%** | 3-ETF +8.4% |
| Avg Tracking Error | 0.75% | **0.71%** | 3-ETF (marginally tighter) |
| Avg Information Ratio | -0.95 | **+0.19** | 3-ETF (positive vs negative) |

> **Key takeaway:** On a simple average across all periods and strategies, the 3-ETF portfolio outperforms on every risk-adjusted metric. However, this aggregate view is heavily influenced by the Bear Market period — the picture changes significantly when broken out by regime.

---

## Section 2 — Performance by Market Regime

### Bear Market (2007–2008)

| Metric | 12-ETF | 3-ETF |
|---|---|---|
| Avg CAGR | **-19.95%** | **+2.88%** |
| Avg Sharpe Ratio | -0.79 | +0.25 |
| Avg Max Drawdown | -44.8% | -19.4% |
| Avg Volatility | 26.9% | 11.7% |
| Best Strategy (Sharpe) | Quarterly Rebal + TLH (–0.62) | Rel Threshold 50% + TLH (+0.41) |

**Verdict: 3-ETF wins decisively.** TLT and GLD both surged during the 2007–2008 financial crisis. The 12-ETF portfolios held international equity and emerging market exposure which were all hit simultaneously — no true diversification. The 3-ETF's 44.8 ppt max drawdown advantage is the single largest gap in the entire analysis.

---

### Baseline Market (2010–2019)

| Metric | 12-ETF | 3-ETF |
|---|---|---|
| Avg CAGR | 7.04% | **9.51%** |
| Avg Sharpe Ratio | 0.65 | **1.41** |
| Avg Max Drawdown | -17.4% | **-8.2%** |
| Avg Volatility | 11.8% | **6.8%** |
| Best Strategy (Sharpe) | Yearly Rebal + TLH (1.06) | Quarterly Rebal + TLH **(1.90)** |

**Verdict: 3-ETF wins.** The decade-long post-crisis recovery favored equities, but SPY's growth combined with TLT's steady income and GLD's stability produced a smoother, higher Sharpe path. The 12-ETF portfolios had more dispersion across many correlated equity sleeves.

---

### Bull Market (2023–2024)

| Metric | 12-ETF | 3-ETF |
|---|---|---|
| Avg CAGR | **15.83%** | 10.94% |
| Avg Sharpe Ratio | **1.40** | 1.13 |
| Avg Max Drawdown | **-9.95%** | -11.16% |
| Avg Volatility | 10.96% | 9.66% |
| Best Strategy (Sharpe) | Abs Threshold 10% / No TLH **(1.73)** | Abs Threshold 20% + TLH (1.27) |

**Verdict: 12-ETF wins.** This is the only regime where the 12-ETF outperforms. In a strong equity bull market, TLT and GLD become dead weight — TLT underperformed significantly in the 2023–2024 rate environment. The 12-ETF's equity concentration (especially the 100/0 portfolio) captures full upside. The 5% CAGR gap here is meaningful.

---

## Section 3 — Tax-Loss Harvesting Analysis

*All figures based on TLH-enabled runs only.*

### Core TLH Metrics

| Metric | 12-ETF | 3-ETF | Winner |
|---|---|---|---|
| Avg Tax Paid | +$5,213 | **-$844** | 3-ETF |
| Avg Losses Harvested | **$77,654** | $46,452 | 12-ETF (raw volume) |
| Avg TLH Event Count | **10.96** | 2.38 | 12-ETF (more events) |
| Avg Execution Costs | $3,649 | **$2,346** | 3-ETF |
| Avg Tax Alpha 2 | $740 | **$9,624** | **3-ETF (13x higher)** |
| Avg Loss Carryforward ST | $3,345 | **$15,486** | 3-ETF |

### Harvest Efficiency

The 12-ETF harvests more dollars but converts them to tax value less effectively:

| Portfolio | Losses Harvested | Tax Paid | Efficiency Ratio |
|---|---|---|---|
| 12-ETF | $77,654 | +$5,213 | 14.9x |
| **3-ETF** | $46,452 | **-$844** | **55.0x** |

The 3-ETF's net tax paid is **negative** — harvested losses fully offset all realized gains and carry $15,486 forward as future tax shields. The 12-ETF still writes a check to the IRS despite harvesting nearly twice as much.

### CAGR Lift from TLH (TLH On minus TLH Off)

| Market Period | 12-ETF Lift | 3-ETF Lift |
|---|---|---|
| Bear Market | -1.07% | **+1.00%** |
| Baseline Market | -0.38% | **+4.62%** |
| Bull Market | -0.59% | **+0.40%** |
| **Overall Average** | **-0.68%** | **+2.00%** |

> **Critical finding:** TLH hurts performance in the 12-ETF portfolios across every single market period. For the 3-ETF, TLH adds value in every period — most dramatically in the Baseline decade (+4.62% CAGR lift).

### Why TLH Works Better in the 3-ETF

1. **Fewer events, higher quality.** With only 2–3 TLH harvests per run vs 11 for the 12-ETF, each harvest is more impactful and generates less cost drag.
2. **No wash-sale interference.** SPY, TLT, and GLD are in different asset classes — harvesting one has no risk of triggering a wash sale on the others. The 12-ETF's many equity tickers (IVV, IVW, IVE, OEF, EFA, EFV, EFG) are highly correlated and wash sales limit harvest effectiveness.
3. **Lower execution costs.** 3-ETF: $2,346 vs 12-ETF: $3,649 average per run.

### Top TLH Runs by Tax Alpha 2

**12-ETF Group:**

| Portfolio | Period | Strategy | Tax Alpha 2 | Losses Harvested | TLH Events |
|---|---|---|---|---|---|
| 2-ETF (IVV/EFA) | Bear Market | Yearly Rebal + TLH | $17,449 | $66,004 | 2 |
| 2-ETF (IVV/EFA) | Bear Market | Abs Threshold 10% + TLH | $17,269 | $65,076 | 1 |
| 2-ETF (IVV/EFA) | Bear Market | Abs Threshold 20% + TLH | $17,269 | $65,076 | 1 |

**3-ETF Portfolio:**

| Portfolio | Period | Strategy | Tax Alpha 2 | Losses Harvested | TLH Events |
|---|---|---|---|---|---|
| 3-ETF (SPY/TLT/GLD) | Bear Market | Quarterly Rebal + TLH | $15,744 | $47,984 | 5 |
| 3-ETF (SPY/TLT/GLD) | Bear Market | Abs Threshold 5% + TLH | $15,075 | $58,490 | 3 |
| 3-ETF (SPY/TLT/GLD) | Bear Market | Rel Threshold 25% + TLH | $14,380 | $55,954 | 3 |

> Note: The best individual TLH runs in the 12-ETF group come from the concentrated 2-ETF (IVV/EFA) sub-portfolio, not the diversified 40/60 or 100/0 models — further evidence that fewer, larger positions produce better TLH outcomes.

---

## Section 4 — Best Strategy by Period

| Period | Portfolio | Best Strategy | Sharpe | CAGR |
|---|---|---|---|---|
| Bear Market | 3-ETF | Rel Threshold 50% + TLH | 0.41 | 4.71% |
| Bear Market | 12-ETF | Quarterly Rebal + TLH | -0.62 | -19.81% |
| Baseline Market | 3-ETF | Quarterly Rebal + TLH | **1.90** | 12.33% |
| Baseline Market | 12-ETF | Yearly Rebal + TLH | 1.06 | 6.66% |
| Bull Market | 12-ETF | Abs Threshold 10% / No TLH | **1.73** | 22.13% |
| Bull Market | 3-ETF | Abs Threshold 20% + TLH | 1.27 | 11.96% |

---

## Section 5 — Summary Scorecard

| Category | Winner | Margin |
|---|---|---|
| Crisis protection (Bear Market) | **3-ETF** | Large |
| Long-run risk-adjusted return (Baseline) | **3-ETF** | Large |
| Bull market return capture | **12-ETF** | Moderate |
| Volatility (lower is better) | **3-ETF** | Large |
| Max drawdown (shallower is better) | **3-ETF** | Large |
| Raw losses harvested | **12-ETF** | Moderate |
| TLH efficiency (Tax Alpha 2) | **3-ETF** | Very Large (13x) |
| TLH CAGR impact | **3-ETF** | 3-ETF +2.00% vs 12-ETF -0.68% |
| Net tax outcome | **3-ETF** | Negative tax paid vs +$5,213 |
| Execution costs | **3-ETF** | Moderate |

**Final Score: 3-ETF wins 9 of 10 categories.** The 12-ETF only wins on raw losses harvested and bull-market return capture.

---

## Section 6 — Key Takeaways

1. **The 3-ETF is a superior TLH vehicle.** Despite harvesting fewer raw dollars, it converts losses into real after-tax value at 55x efficiency vs 14.9x for the 12-ETF. TLH adds +2% CAGR to the 3-ETF; it subtracts -0.68% from the 12-ETF.

2. **Portfolio concentration improves TLH quality.** The 12-ETF's best TLH results come from its most concentrated sub-portfolio (2-ETF: IVV/EFA), not from the broader institutional models. More tickers = more wash-sale risk and cost drag.

3. **Asset class diversity matters more than ticker diversity.** The 3-ETF holds 3 tickers but 3 genuinely uncorrelated asset classes. The 12-ETF holds many tickers but they are largely correlated equity and bond sub-factors.

4. **Crisis regime defines the long-run winner.** The 3-ETF's Bear Market performance (+2.88% CAGR vs -19.95%) dominates the aggregate comparison. Protecting capital in a crash compounds forward far more powerfully than marginal bull-market outperformance.

5. **The 12-ETF is better suited for investors with pure equity bull-market conviction.** Its 22% CAGR in the best Bull Market run beats the 3-ETF's 12% — but that advantage reverses dramatically in any crisis.

6. **TLH in the 12-ETF needs redesign.** The consistent negative CAGR drag from TLH across all periods suggests the current thresholds and wash-sale handling are sub-optimal at scale. Fewer, higher-conviction harvests would likely perform better.

---

*Generated from `vise_comparative_analysis.ipynb` | Vise Capstone — UT Austin MSBA*
*Data: Vise price_data.parquet + dividend_data.csv | Engine: optimizer_msba_v1_engine.py*
