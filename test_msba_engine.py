"""
test_msba_engine.py
===================
Pytest suite for optimizer_msba_v1_engine.py.

All tests use synthetic price data — no external CSVs needed.
Each test knows the correct answer ahead of time and asserts it.

Key conventions:
  - Sells (TLH, rebalance) all use action="SELL" in trades_df.
    Type is identified by the `reason` field:
      TLH sell   → reason starts with "TLH_SELL:"
      Rebal sell → reason contains "REBAL_SELL_FOR:"
  - TLH rebuys use action="TLH_REBUY" (from source= param in buy()).
  - Rebalance buys use action="BUY" with reason containing "REBAL_BUY_FOR:".

  - Wash-sale LOOKBACK: if SPY was bought within 30 calendar days before a
    loss sale, the loss sale is blocked. Tests that need TLH to fire use a
    35-business-day flat-price prefix so this 30-day window clears before the
    price drop.

  - Wash-sale FORWARD BLOCK: after TLH-selling SPY, SPY is blocked for 30
    days. _resolve_buy_symbol() then uses the proxy (VOO), which is what
    tests 3, 4, and 10 verify.

  - Tax carryforward is committed to loss_carryforward_st only at year-end
    (first taxable event of the new year). Tests that check carryforward use
    a multi-year simulation with a 2023 taxable event.

Run:
    pytest test_msba_engine.py -v
"""

import pytest
import pandas as pd
import numpy as np
from optimizer_msba_v1_engine import run_optimizer_simulation

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

TAX_RATES = {"st_rate": 0.35, "lt_rate": 0.20}
NO_COSTS = {"commission_bps": 0.0, "slippage_bps": 0.0, "bid_ask_bps": 0.0}


def make_prices(tickers: list, dates: list, prices_dict: dict) -> pd.DataFrame:
    """
    Build a long-format price DataFrame.
    prices_dict: {ticker: list_of_prices}  OR  {ticker: float (constant)}
    """
    rows = []
    for tk in tickers:
        val = prices_dict[tk]
        for i, d in enumerate(dates):
            price = val[i] if isinstance(val, (list, np.ndarray)) else float(val)
            rows.append({"TICKERSYMBOL": tk, "PRICEDATE": pd.Timestamp(d), "PRICECLOSE": price})
    return pd.DataFrame(rows)


def make_proxy_df(*mappings) -> pd.DataFrame:
    """
    Build a proxy DataFrame from (symbol, lookup_symbol) pairs.
    E.g. make_proxy_df(("SPY","VOO"), ("QQQ","QQQM"))
    """
    rows = [
        {"symbol": sym, "lookup_type": "SUBSTITUTE", "lookup_symbol": proxy, "order": 1}
        for sym, proxy in mappings
    ]
    return pd.DataFrame(rows)


def business_dates(start: str, n: int) -> list:
    """Return n business days starting from start."""
    return list(pd.bdate_range(start=start, periods=n))


def _run(prices_df, tickers, weights, dates,
         tlh_threshold=0.0, proxy_df=None,
         tax_rates=None, rebalance_frequency="None", cost_config=None,
         compute_tax_alpha=False, initial_capital=100_000.0, static=True,
         wash_sale_days=30):
    """Thin wrapper with sensible test defaults."""
    return run_optimizer_simulation(
        prices_df=prices_df,
        dividends_df=None,
        tickers=tickers,
        weights=weights,
        start_date=dates[0],
        end_date=dates[-1],
        rebalance_frequency=rebalance_frequency,
        tax_rates=tax_rates or TAX_RATES,
        tlh_threshold=tlh_threshold,
        reinvest_dividends=False,
        initial_capital=initial_capital,
        price_field="PRICECLOSE",
        static=static,
        cost_config=cost_config or NO_COSTS,
        proxy_df=proxy_df,
        wash_sale_days=wash_sale_days,
        tlh_threshold_mode="explicit",
        compute_tax_alpha=compute_tax_alpha,
    )


def _tlh_sells(trades_df: pd.DataFrame) -> pd.DataFrame:
    """TLH-triggered sells: action=SELL, reason starts with TLH_SELL:"""
    return trades_df[
        (trades_df["action"] == "SELL") &
        (trades_df["reason"].str.contains("TLH_SELL:", na=False))
    ]


def _tlh_rebuys(trades_df: pd.DataFrame) -> pd.DataFrame:
    """TLH rebuys: action=TLH_REBUY"""
    return trades_df[trades_df["action"] == "TLH_REBUY"]


def _rebal_sells(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Calendar rebalance sells: reason contains REBAL_SELL_FOR:"""
    return trades_df[trades_df["reason"].str.contains("REBAL_SELL_FOR:", na=False)]


def _rebal_buys(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Calendar rebalance buys: reason contains REBAL_BUY_FOR:"""
    return trades_df[trades_df["reason"].str.contains("REBAL_BUY_FOR:", na=False)]


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Basic buy-and-hold: flat price, no TLH
# ─────────────────────────────────────────────────────────────────────────────

def test_basic_buy_and_hold():
    """
    Flat $100 price for 30 days, zero costs, no TLH.
    NAV should equal initial capital exactly throughout.
    Exactly 1 trade (initial buy). No realized events. No tax.

    Expected:
      - Buy 1000 SPY at $100 = $100,000
      - Price flat → NAV stays $100,000 every day
    """
    dates = business_dates("2023-01-03", 30)
    prices_df = make_prices(["SPY"], dates, {"SPY": 100.0})

    r = _run(prices_df, ["SPY"], [1.0], dates)

    nav = r["nav_series"]
    assert nav.iloc[-1] == pytest.approx(100_000.0, rel=1e-6), \
        f"Expected NAV=$100,000 but got ${nav.iloc[-1]:.2f}"
    assert (nav - 100_000.0).abs().max() < 1e-3, \
        "NAV drifted from initial capital on a flat price with zero costs"

    trades = r["trades_df"]
    assert len(trades) == 1, \
        f"Expected exactly 1 trade (initial buy), got {len(trades)}"
    assert trades.iloc[0]["action"] == "BUY", \
        f"First trade should be BUY, got {trades.iloc[0]['action']}"

    assert len(r["realized_df"]) == 0, \
        "Expected 0 realized events on buy-and-hold"
    assert r["tax_paid_total"] == pytest.approx(0.0, abs=1e-6), \
        f"Expected zero tax paid, got ${r['tax_paid_total']:.4f}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — NAV tracks price appreciation proportionally
# ─────────────────────────────────────────────────────────────────────────────

def test_nav_tracks_price_appreciation():
    """
    SPY ramps from $100 to $120 (+20%) over 30 days, zero costs.
    With 100k capital we buy 1000 shares at $100.
    At $120 final price, NAV = 1000 × $120 = $120,000 exactly.

    Expected ratio: 120,000 / 100,000 = 1.200
    """
    dates = business_dates("2023-01-03", 30)
    prices = np.linspace(100.0, 120.0, len(dates)).tolist()
    prices_df = make_prices(["SPY"], dates, {"SPY": prices})

    r = _run(prices_df, ["SPY"], [1.0], dates)

    nav = r["nav_series"]
    ratio = nav.iloc[-1] / nav.iloc[0]

    assert ratio == pytest.approx(1.20, rel=0.005), \
        f"Expected NAV ratio 1.20 (price 100→120), got {ratio:.4f}"

    assert len(_tlh_sells(r["trades_df"])) == 0, \
        "No TLH sells should occur on an appreciating asset"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — TLH fires on sufficient price drop
# ─────────────────────────────────────────────────────────────────────────────

def test_tlh_fires_on_drop():
    """
    SPY drops from $100 to $78 (-22%) on day 35 (after the 30-day lookback clears).
    TLH threshold = 5%. Proxy: VOO at $90.

    Setup: 35 business days flat at $100 (≈49 calendar days > 30-day lookback),
    then instant drop to $78. The lookback is clear so TLH fires.
    After TLH sell, SPY is buy-blocked for 30 days → rebuy routes to VOO.

    Expected:
      - At least one TLH sell for SPY
      - All TLH rebuys go into VOO (not SPY)
      - losses_harvested > 0
      - tax_paid_total < 0 (ordinary income offset gives $3k × 35% = $1,050 refund)
    """
    dates = business_dates("2023-01-03", 55)
    spy_prices = [100.0] * 35 + [78.0] * (len(dates) - 35)
    prices_df = make_prices(["SPY", "VOO"], dates, {"SPY": spy_prices, "VOO": 90.0})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.05, proxy_df=proxy_df, wash_sale_days=30)

    trades = r["trades_df"]
    assert len(_tlh_sells(trades)) > 0, \
        "Expected at least one TLH sell (SPY down 22% > 5% threshold, lookback cleared)"
    assert len(_tlh_rebuys(trades)) > 0, \
        "Expected at least one TLH rebuy into VOO"
    assert (_tlh_rebuys(trades)["ticker"] == "VOO").all(), \
        f"All TLH rebuys should go into proxy VOO, got: {_tlh_rebuys(trades)['ticker'].unique()}"

    assert r["losses_harvested"] > 0, \
        f"Expected positive losses_harvested, got {r['losses_harvested']}"
    assert r["tax_paid_total"] < 0, \
        f"Expected net tax refund (negative total), got ${r['tax_paid_total']:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — TLH rebuy uses dollar value, not share count
# ─────────────────────────────────────────────────────────────────────────────

def test_tlh_rebuy_is_dollar_value():
    """
    SPY drops from $100 to $80 (-20%) on day 35. Proxy VOO at $90.
    Lookback clears after 35 business days; after TLH sell SPY is buy-blocked
    so rebuy goes into VOO.

    Expected math (zero transaction costs):
      - Buy 1000 SPY at $100 = $100,000 initial
      - SPY falls to $80 → TLH_SELL: 1000 shares × $80 = $80,000 proceeds
      - TLH_REBUY in VOO: $80,000 / $90 = 888.89 VOO shares

    Key assertions:
      - TLH_SELL gross_value ≈ TLH_REBUY gross_value (within 1%) — same dollars
      - VOO shares ≈ 888.89, NOT 1000 (share counts differ because prices differ)
    """
    dates = business_dates("2023-01-03", 50)
    spy_prices = [100.0] * 35 + [80.0] * (len(dates) - 35)
    prices_df = make_prices(["SPY", "VOO"], dates, {"SPY": spy_prices, "VOO": 90.0})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.05, proxy_df=proxy_df, wash_sale_days=30)

    trades = r["trades_df"]
    tlh_sell = _tlh_sells(trades)
    tlh_rebuy = _tlh_rebuys(trades)

    assert len(tlh_sell) > 0, "TLH sell must fire (SPY down 20%, lookback cleared)"
    assert len(tlh_rebuy) > 0, "TLH rebuy must fire into VOO"

    sell_gross = tlh_sell["gross_value"].sum()    # 1000 × $80 = $80,000
    rebuy_gross = tlh_rebuy["gross_value"].sum()  # 888.89 × $90 = $80,000

    assert sell_gross == pytest.approx(rebuy_gross, rel=0.01), \
        f"Dollar parity: TLH_SELL=${sell_gross:.2f}, TLH_REBUY=${rebuy_gross:.2f} (should match)"

    spy_shares_sold = tlh_sell["shares"].sum()      # 1000
    voo_shares_bought = tlh_rebuy["shares"].sum()   # 888.89

    # Dollar parity at different prices means different share counts
    expected_voo = spy_shares_sold * 80.0 / 90.0   # 888.89
    assert voo_shares_bought == pytest.approx(expected_voo, rel=0.01), \
        f"VOO shares: expected {expected_voo:.2f} (dollar parity at $80/$90), got {voo_shares_bought:.2f}"

    assert spy_shares_sold != pytest.approx(voo_shares_bought, rel=0.01), \
        "Share counts should differ because SPY price ($80) ≠ VOO price ($90)"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — No TLH when no proxy is configured
# ─────────────────────────────────────────────────────────────────────────────

def test_no_tlh_without_proxy():
    """
    SPY drops 22% but proxy_df=None. Engine requires a proxy to TLH.
    Expected: zero TLH activity regardless of price drop.
    """
    dates = business_dates("2023-01-03", 40)
    spy_prices = [100.0] * 35 + [78.0] * (len(dates) - 35)
    prices_df = make_prices(["SPY"], dates, {"SPY": spy_prices})

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.05, proxy_df=None, wash_sale_days=0)

    trades = r["trades_df"]
    assert len(_tlh_sells(trades)) == 0, "TLH sell should not fire without proxy"
    assert len(_tlh_rebuys(trades)) == 0, "TLH rebuy should not fire without proxy"
    assert r["losses_harvested"] == 0.0, \
        f"losses_harvested should be 0 without proxy, got {r['losses_harvested']}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Calendar rebalancing restores weights
# ─────────────────────────────────────────────────────────────────────────────

def test_rebalancing_restores_weights():
    """
    SPY (50%) + AGG (50%), 100k. SPY goes $100 → $150 (+50%). AGG flat at $100.
    After monthly rebalance:
      - SPY is overweight → REBAL_SELL_FOR:SPY
      - AGG is underweight → REBAL_BUY_FOR:AGG

    Expected:
      - At least one sell with reason containing REBAL_SELL_FOR:SPY
      - At least one buy with reason containing REBAL_BUY_FOR:AGG
    """
    dates = business_dates("2023-01-03", 45)  # ~2 months for ≥1 monthly rebalance
    spy_prices = list(np.linspace(100.0, 150.0, len(dates)))
    prices_df = make_prices(["SPY", "AGG"], dates,
                            {"SPY": spy_prices, "AGG": 100.0})

    r = _run(prices_df, ["SPY", "AGG"], [0.5, 0.5], dates,
             rebalance_frequency="Monthly", static=False)

    trades = r["trades_df"]
    sells = _rebal_sells(trades)
    buys = _rebal_buys(trades)

    assert len(sells) > 0, "Expected at least one REBAL_SELL_FOR trade"
    assert len(buys) > 0, "Expected at least one REBAL_BUY_FOR trade"

    assert sells["reason"].str.contains("SPY").any(), \
        f"SPY should be sold (overweight); sell reasons: {sells['reason'].tolist()}"
    assert buys["reason"].str.contains("AGG").any(), \
        f"AGG should be bought (underweight); buy reasons: {buys['reason'].tolist()}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — $3k ordinary income offset cap + year-end carryforward
# ─────────────────────────────────────────────────────────────────────────────

def test_tax_engine_ordinary_offset_cap_and_carryforward():
    """
    Two tickers, spanning 2022→2023. Two TLH events — one per year.

    2022 (day 35, Nov): SPY ($100→$60, -40% loss) → TLH fires
      Loss = 500 shares × ($60-$100) = -$20,000
      Tax: $3k ordinary offset → refund = $3,000 × 35% = $1,050
      Excess $17,000 held in YTD state (not yet committed to carryforward)

    2023 (day 80, Jan): QQQ ($100→$85, -15% loss) → TLH fires
      First 2023 taxable event triggers TaxEngine year rollover:
        → commits 2022 excess $17,000 to loss_carryforward_st
        → resets 2023 YTD
      2023 QQQ loss = 500 × ($85-$100) = -$7,500
        → another $3k ordinary offset → another $1,050 refund

    Lookback: SPY bought day 0 (Oct 3, 2022). TLH fires day 35 (≈Nov 21).
      Nov 21 > Oct 3 + 30 days → lookback CLEARED ✓
    Same for QQQ: bought day 0, TLH fires day 80 (≈Jan 24, 2023, > 30 days) ✓

    After year rollover:
      loss_carryforward_st ≈ $17,000
      ordinary_income_offset_used_ytd_final ≈ $3,000 (2023 QQQ offset)
      tax_paid_total ≈ -$2,100  (= -$1,050 × 2, one per year)
    """
    dates = business_dates("2022-10-03", 105)  # Oct 2022 – Mar 2023
    spy_prices = [100.0] * 35 + [60.0] * (len(dates) - 35)   # drops on day 35 (2022)
    qqq_prices = [100.0] * 80 + [85.0] * (len(dates) - 80)   # drops on day 80 (2023)
    voo_prices  = [60.0]  * len(dates)   # proxy for SPY
    qqqm_prices = [85.0]  * len(dates)   # proxy for QQQ

    prices_df = make_prices(
        ["SPY", "QQQ", "VOO", "QQQM"], dates,
        {"SPY": spy_prices, "QQQ": qqq_prices,
         "VOO": voo_prices, "QQQM": qqqm_prices}
    )
    proxy_df = make_proxy_df(("SPY", "VOO"), ("QQQ", "QQQM"))

    r = _run(prices_df, ["SPY", "QQQ"], [0.5, 0.5], dates,
             tlh_threshold=0.01, proxy_df=proxy_df, wash_sale_days=30)

    trades = r["trades_df"]

    spy_tlh_sells = _tlh_sells(trades)[_tlh_sells(trades)["reason"].str.contains("SPY")]
    qqq_tlh_sells = _tlh_sells(trades)[_tlh_sells(trades)["reason"].str.contains("QQQ")]
    assert len(spy_tlh_sells) > 0, "SPY TLH must fire in 2022 (down 40%)"
    assert len(qqq_tlh_sells) > 0, "QQQ TLH must fire in 2023 (down 15%) to trigger year rollover"

    # $3k ordinary income offset cap per year
    offset = r["ordinary_income_offset_used_ytd_final"]  # 2023 YTD after QQQ event
    assert offset <= 3_000.0 + 1e-6, \
        f"Ordinary income offset should be ≤ $3,000, got ${offset:.2f}"

    # Carryforward committed at 2022→2023 year boundary
    # 2022 loss = $20,000 (SPY), ordinary offset = $3,000 → excess = $17,000
    cf_st = r["loss_carryforward_st"]
    assert cf_st > 0, \
        f"Expected ST loss carryforward > 0 (2022 excess committed at year end), got ${cf_st:.2f}"
    assert cf_st == pytest.approx(17_000.0, rel=0.05), \
        f"Expected carryforward ≈ $17,000 ($20k loss − $3k ordinary offset), got ${cf_st:,.2f}"

    # Total tax refunds: -$1,050 per year × 2 years = -$2,100
    assert r["tax_paid_total"] == pytest.approx(-2_100.0, rel=0.05), \
        f"Expected tax_paid_total ≈ -$2,100 (two ordinary offset refunds), got ${r['tax_paid_total']:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Tax Alpha 2: TLH NAV exceeds no-TLH baseline
# ─────────────────────────────────────────────────────────────────────────────

def test_tax_alpha_2_positive_with_tlh():
    """
    SPY drops 22% (after lookback clears) with proxy VOO. compute_tax_alpha=True.

    TLH realizes a loss → ordinary income refund ($1,050) → more cash → higher NAV.
    The no-TLH baseline has the same price performance but no refund.

    Expected: tax_alpha_2_final > 0  (TLH NAV − no-TLH NAV > 0)
    """
    dates = business_dates("2023-01-03", 55)
    spy_prices = [100.0] * 35 + [78.0] * (len(dates) - 35)
    prices_df = make_prices(["SPY", "VOO"], dates,
                            {"SPY": spy_prices, "VOO": 78.0})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.05, proxy_df=proxy_df, wash_sale_days=30,
             compute_tax_alpha=True)

    assert r.get("tax_alpha_2_final") is not None, \
        "tax_alpha_2_final must be present when compute_tax_alpha=True"
    assert r.get("nav_no_tlh") is not None, \
        "nav_no_tlh series must be present when compute_tax_alpha=True"

    alpha2 = r["tax_alpha_2_final"]
    assert alpha2 > 0, \
        f"Tax Alpha 2 should be positive (TLH tax refund boosts NAV), got ${alpha2:.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — Zero threshold: no TLH even on large drop
# ─────────────────────────────────────────────────────────────────────────────

def test_no_tlh_zero_threshold():
    """
    SPY drops 30%. tlh_threshold=0.0 means TLH is disabled entirely.
    Expected: zero harvesting activity.
    """
    dates = business_dates("2023-01-03", 40)
    spy_prices = [100.0] * 35 + [70.0] * (len(dates) - 35)
    prices_df = make_prices(["SPY", "VOO"], dates, {"SPY": spy_prices, "VOO": 70.0})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.0, proxy_df=proxy_df, wash_sale_days=0)

    trades = r["trades_df"]
    assert len(_tlh_sells(trades)) == 0, "TLH sell should not fire when tlh_threshold=0"
    assert len(_tlh_rebuys(trades)) == 0, "TLH rebuy should not fire when tlh_threshold=0"
    assert r["losses_harvested"] == 0.0, \
        f"losses_harvested should be 0 when threshold=0, got {r['losses_harvested']}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — Wash-sale: proxy used instead of original after TLH sell
# ─────────────────────────────────────────────────────────────────────────────

def test_wash_sale_proxy_used_not_original():
    """
    SPY drops after the 30-day lookback window clears (day 35).
    TLH fires → sells SPY → records loss sale → SPY is now buy-blocked for 30 days.
    _resolve_buy_symbol("SPY") sees SPY is blocked → returns VOO (the proxy).

    All TLH_REBUY trades should be for VOO, never for SPY.
    """
    dates = business_dates("2023-01-03", 60)
    spy_prices = [100.0] * 35 + [75.0] * (len(dates) - 35)
    voo_prices  = [100.0] * 35 + [76.0] * (len(dates) - 35)
    prices_df = make_prices(["SPY", "VOO"], dates,
                            {"SPY": spy_prices, "VOO": voo_prices})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.05, proxy_df=proxy_df, wash_sale_days=30)

    trades = r["trades_df"]
    assert len(_tlh_sells(trades)) > 0, \
        "TLH sell should fire (SPY down 25%, lookback cleared after 35 business days)"
    assert len(_tlh_rebuys(trades)) > 0, \
        "TLH rebuy should fire"

    rebuys = _tlh_rebuys(trades)
    spy_rebuys = rebuys[rebuys["ticker"] == "SPY"]
    assert len(spy_rebuys) == 0, \
        f"Wash-sale violated: {len(spy_rebuys)} rebuy(s) into SPY within 30-day window"
    assert (rebuys["ticker"] == "VOO").all(), \
        f"All TLH rebuys should go to proxy VOO, got: {rebuys['ticker'].unique()}"
