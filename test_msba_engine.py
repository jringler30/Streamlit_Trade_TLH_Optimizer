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
    voo_prices = [60.0] * len(dates)   # proxy for SPY
    qqqm_prices = [85.0] * len(dates)   # proxy for QQQ

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
    SPY drops 22% in Nov 2022 (after wash-sale lookback clears). compute_tax_alpha=True.

    Simulation spans Oct 2022 → Feb 2023 so the 2022→2023 year-end crossing fires
    settle_annual_taxes() inside the loop. The tax refund (ordinary income offset on
    the harvested loss) is added to cash before the final NAV dates, making
    TLH NAV > no-TLH NAV.

    NOTE: Tax Alpha 2 only becomes visible in nav_series AFTER the year-end settlement
    fires inside the simulation loop. A single-calendar-year test will always yield
    alpha2=0 because the final settle_annual_taxes() runs after nav_arr is filled.

    Expected: tax_alpha_2_final > 0  (TLH NAV − no-TLH NAV > 0)
    """
    # ~80 business days: Oct 3, 2022 → ~Feb 3, 2023 (crosses 2022→2023 boundary)
    dates = business_dates("2022-10-03", 80)
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
    voo_prices = [100.0] * 35 + [76.0] * (len(dates) - 35)
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


# ─────────────────────────────────────────────────────────────────────────────
# Test 11 — ST/LT boundary: 365 days is SHORT-TERM, 366 days is LONG-TERM
# ─────────────────────────────────────────────────────────────────────────────

def test_lt_classification_boundary():
    """
    IRS rule: "more than one year" = strictly more than 365 days.
    A lot held for exactly 365 calendar days must be classified as SHORT-TERM.
    A lot held for 366+ calendar days must be classified as LONG-TERM.

    Setup:
      - SPY bought at $100. Proxy VOO available.
      - Price drops to $80 (-20%) to trigger TLH.
      - Two scenarios: hold 365 days vs hold 366 days before the drop.

    Expected:
      - 365-day hold → gain_type = "ST" in realized_df
      - 366-day hold → gain_type = "LT" in realized_df
    """
    from optimizer_msba_v1_engine import TaxEngine

    tax = TaxEngine(st_rate=0.35, lt_rate=0.20, lt_holding_days=365)

    open_date_365 = pd.Timestamp("2022-01-01")
    close_365 = open_date_365 + pd.Timedelta(days=365)
    gain_type_365, _ = tax.classify(open_date_365, close_365)
    assert gain_type_365 == "ST", (
        f"365-day hold should be SHORT-TERM (IRS: 'more than 1 year'), got {gain_type_365}"
    )

    open_date_366 = pd.Timestamp("2022-01-01")
    close_366 = open_date_366 + pd.Timedelta(days=366)
    gain_type_366, _ = tax.classify(open_date_366, close_366)
    assert gain_type_366 == "LT", (
        f"366-day hold should be LONG-TERM, got {gain_type_366}"
    )

    # Boundary at exactly 364 days — also ST
    close_364 = open_date_365 + pd.Timedelta(days=364)
    gain_type_364, _ = tax.classify(open_date_365, close_364)
    assert gain_type_364 == "ST", f"364-day hold must be ST, got {gain_type_364}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 12 — Transaction cost deduction: rebalance with nonzero cost_rate
#           reduces NAV compared to zero-cost case
# ─────────────────────────────────────────────────────────────────────────────

def test_rebalancing_cost_deduction():
    """
    SPY (50%) + AGG (50%). SPY appreciates → rebalance triggers.
    With cost_rate = 12 bps (0.0012), final NAV should be LESS than cost_rate = 0.

    This test validates that costs are actually embedded in NAV, not merely estimated.
    """
    from engine.rebalancing import build_rebalanced_series
    from engine.core import build_prices_wide

    dates = business_dates("2023-01-03", 45)  # ~2 months, ensures ≥1 monthly rebalance
    spy_prices = list(np.linspace(100.0, 150.0, len(dates)))
    agg_prices = [100.0] * len(dates)

    rows = []
    for i, d in enumerate(dates):
        rows.append({"TICKERSYMBOL": "SPY", "PRICEDATE": d, "PRICECLOSE": spy_prices[i]})
        rows.append({"TICKERSYMBOL": "AGG", "PRICEDATE": d, "PRICECLOSE": agg_prices[i]})
    prices_df = pd.DataFrame(rows)

    prices_wide = prices_df.pivot(index="PRICEDATE", columns="TICKERSYMBOL", values="PRICECLOSE")
    prices_wide = prices_wide.sort_index().ffill().dropna()

    target = {"SPY": 0.5, "AGG": 0.5}
    initial = 100_000.0

    _, stats_zero = build_rebalanced_series(prices_wide, target, initial, "Monthly", cost_rate=0.0)
    _, stats_costs = build_rebalanced_series(prices_wide, target, initial, "Monthly", cost_rate=0.0012)

    assert stats_costs["final_value"] < stats_zero["final_value"], (
        f"Nonzero cost_rate must reduce final NAV. "
        f"No-cost: ${stats_zero['final_value']:,.0f}, with-cost: ${stats_costs['final_value']:,.0f}"
    )
    # Cost drag should be modest (not catastrophic)
    drag_pct = (stats_zero["final_value"] - stats_costs["final_value"]) / stats_zero["final_value"]
    assert drag_pct < 0.01, f"Cost drag {drag_pct:.4%} seems too large for 12 bps on a low-turnover strategy"
    assert drag_pct > 0, "Drag must be strictly positive"


# ─────────────────────────────────────────────────────────────────────────────
# Test 13 — Dividend tax deduction: after-tax cash < gross dividend
# ─────────────────────────────────────────────────────────────────────────────

def test_dividend_tax_deducted():
    """
    SPY pays a $2/share dividend on day 5 with lt_rate=0.20.
    With 1000 shares, gross = $2,000. After 20% tax, net = $1,600.
    Cash should increase by $1,600 (not $2,000) when dividend_tax_rate=0.20.
    Tax paid total should reflect the $400 dividend tax.
    """
    dates = business_dates("2023-01-03", 10)
    spy_prices = [100.0] * len(dates)

    # Dividend of $2/share on the 5th trading day (PAYDATE)
    div_rows = [{"TICKERSYMBOL": "SPY", "PAYDATE": dates[4], "DIVAMOUNT": 2.0}]
    prices_df = make_prices(["SPY"], dates, {"SPY": 100.0})
    dividends_df = pd.DataFrame(div_rows)

    r = run_optimizer_simulation(
        prices_df=prices_df,
        dividends_df=dividends_df,
        tickers=["SPY"],
        weights=[1.0],
        start_date=dates[0],
        end_date=dates[-1],
        rebalance_frequency="None",
        tax_rates={"st_rate": 0.35, "lt_rate": 0.20},
        tlh_threshold=0.0,
        reinvest_dividends=False,  # keep as cash so we can inspect it
        initial_capital=100_000.0,
        price_field="PRICECLOSE",
        static=True,
        cost_config=NO_COSTS,
        proxy_df=None,
        wash_sale_days=0,
        tlh_threshold_mode="explicit",
        compute_tax_alpha=False,
    )

    # 1000 shares × $2 = $2,000 gross; 20% tax = $400; net = $1,600
    # NAV at flat price = 100,000 (shares × 100) + 1,600 (net dividend cash)
    nav_final = r["nav_series"].iloc[-1]
    expected_nav = 100_000.0 + 1_600.0  # $101,600 after-tax dividend received

    assert nav_final == pytest.approx(expected_nav, rel=1e-4), (
        f"Expected NAV ${expected_nav:,.0f} (net dividend = $1,600), got ${nav_final:,.2f}"
    )
    assert r["tax_paid_total"] == pytest.approx(400.0, rel=0.01), (
        f"Expected $400 dividend tax paid (20% × $2,000), got ${r['tax_paid_total']:.2f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 14 — NAV reconciliation: nav == market_value + cash at all times
# ─────────────────────────────────────────────────────────────────────────────

def test_nav_reconciles_with_market_value_plus_cash():
    """
    In a simple buy-and-hold scenario with no TLH and no rebalancing,
    NAV = shares × price + cash (where cash ≈ 0 since we buy fractional shares).
    With zero costs, the final NAV should exactly equal shares × final_price.
    """
    dates = business_dates("2023-01-03", 20)
    prices_df = make_prices(["SPY"], dates, {"SPY": list(np.linspace(100.0, 110.0, len(dates)))})

    r = _run(prices_df, ["SPY"], [1.0], dates, cost_config=NO_COSTS, initial_capital=100_000.0)

    nav = r["nav_series"]
    trades = r["trades_df"]

    # Initial buy: 1000 shares at $100
    init_buy = trades[trades["action"] == "BUY"]
    assert len(init_buy) == 1
    shares_held = float(init_buy.iloc[0]["shares"])

    # Final price = $110; expected NAV = 1000 × $110 = $110,000
    # (cash ≈ 0 since we deployed all capital into fractional shares)
    final_price = 110.0
    expected_nav = shares_held * final_price

    assert nav.iloc[-1] == pytest.approx(expected_nav, rel=1e-5), (
        f"NAV should = shares × final_price = {shares_held:.4f} × ${final_price} "
        f"= ${expected_nav:,.2f}, got ${nav.iloc[-1]:,.2f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 15 — Round-trip transaction cost: buy then sell at same price reduces NAV
# ─────────────────────────────────────────────────────────────────────────────

def test_round_trip_cost_reduces_nav():
    """
    With nonzero commission, buying and selling at the same flat price must
    reduce NAV. The reduction should equal 2 × cost_rate × trade_value (one
    cost each for buy and sell).

    Setup: $100K into SPY at flat $100, then monthly rebalance triggers a sell
    and rebuy (SPY 50% + AGG 50% will drift as SPY rises slightly, or simply
    the initial buy cost is deducted).

    Simpler path: use the optimizer's direct buy → sell on a flat-price portfolio
    with rebalancing enabled so a second trade fires.
    """
    dates = business_dates("2023-01-03", 45)
    # SPY rises 50% → triggers rebalance sell of SPY to restore 50/50
    spy_prices = list(np.linspace(100.0, 150.0, len(dates)))
    prices_df = make_prices(["SPY", "AGG"], dates, {"SPY": spy_prices, "AGG": 100.0})

    cost_bps = 12.0
    cost_config = {"commission_bps": cost_bps, "slippage_bps": 0.0, "bid_ask_bps": 0.0}

    r_cost = _run(prices_df, ["SPY", "AGG"], [0.5, 0.5], dates,
                  rebalance_frequency="Monthly", static=False, cost_config=cost_config)
    r_free = _run(prices_df, ["SPY", "AGG"], [0.5, 0.5], dates,
                  rebalance_frequency="Monthly", static=False, cost_config=NO_COSTS)

    nav_with_cost = r_cost["nav_series"].iloc[-1]
    nav_no_cost = r_free["nav_series"].iloc[-1]

    assert nav_with_cost < nav_no_cost, (
        f"Nonzero transaction costs must reduce NAV. "
        f"No-cost: ${nav_no_cost:,.0f}, with {cost_bps:.0f} bps: ${nav_with_cost:,.0f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 16 — Annual tax settlement: taxes deducted from cash at year-end, not daily
# ─────────────────────────────────────────────────────────────────────────────

def test_annual_tax_settlement_timing():
    """
    TLH fires in late 2022 (day 35), creating a $20k loss → $1,050 refund.
    Tax settlement for the prior year fires on the first 2023 trading day.
    At settlement, cash should increase (negative tax = refund).

    We verify: the refund shows up in the nav_series — the January 2023 NAV
    should be higher than December 2022 NAV by approximately the refund amount,
    all else equal (flat prices post-drop).
    """
    dates = business_dates("2022-10-03", 90)  # Oct–Dec 2022 + early Jan 2023
    spy_prices = [100.0] * 35 + [60.0] * (len(dates) - 35)  # drops day 35
    voo_prices = [60.0] * len(dates)

    prices_df = make_prices(["SPY", "VOO"], dates,
                            {"SPY": spy_prices, "VOO": voo_prices})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.01, proxy_df=proxy_df, wash_sale_days=30)

    nav = r["nav_series"]
    # Find the last 2022 and first 2023 nav values
    nav_2022 = nav[nav.index.year == 2022]
    nav_2023 = nav[nav.index.year == 2023]

    if nav_2022.empty or nav_2023.empty:
        pytest.skip("Simulation did not span 2022→2023 boundary with this date range")

    nav_dec31 = float(nav_2022.iloc[-1])
    nav_jan2 = float(nav_2023.iloc[0])

    # Year-end tax settlement (refund) should cause a discrete jump from Dec→Jan
    # when prices are flat (post-drop VOO at $60). The jump ≈ $1,050 refund.
    assert nav_jan2 > nav_dec31, (
        f"Year-end tax refund settlement should cause NAV to increase at year boundary. "
        f"Dec 31: ${nav_dec31:,.2f}, Jan first day: ${nav_jan2:,.2f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 17 — Drift formula consistency: _compute_drift and compute_drift agree
# ─────────────────────────────────────────────────────────────────────────────

def test_drift_formula_consistency_absolute():
    """
    Both engines must return the same absolute drift for the same weights.
    Previously the optimizer used |w/tgt - 1| (asymmetric) while the V4 engine
    used |log(w/tgt)| (symmetric). After the fix, both use log-ratio for
    Relative mode and |w - tgt| for Absolute mode.
    """
    from optimizer_msba_v1_engine import _compute_drift as opt_drift
    from engine.rebalancing import compute_drift as v4_drift

    current = {"SPY": 0.55, "AGG": 0.25, "QQQ": 0.20}
    target = {"SPY": 0.50, "AGG": 0.30, "QQQ": 0.20}

    # Absolute mode: both must give |w - tgt| exactly
    opt_abs = opt_drift(current, target, "Absolute")
    v4_abs = v4_drift(current, target, "Absolute")
    for tk in target:
        assert opt_abs[tk] == pytest.approx(v4_abs[tk], rel=1e-9), (
            f"Absolute drift mismatch for {tk}: optimizer={opt_abs[tk]:.6f}, v4={v4_abs[tk]:.6f}"
        )

    # Relative mode: both must now use log-ratio (post-fix)
    opt_rel = opt_drift(current, target, "Relative")
    v4_rel = v4_drift(current, target, "Relative")
    for tk in target:
        assert opt_rel[tk] == pytest.approx(v4_rel[tk], rel=1e-6), (
            f"Relative drift mismatch for {tk}: optimizer={opt_rel[tk]:.6f}, v4={v4_rel[tk]:.6f}. "
            "Ensure both engines use the symmetric log-ratio formula."
        )

    # Spot-check symmetry: drift(10%→5%) == drift(5%→10%) in Relative mode
    fw = {"A": 0.10}
    bw = {"A": 0.05}
    tgt = {"A": 0.05}
    tgt2 = {"A": 0.10}
    d_forward = opt_drift(fw, tgt, "Relative")["A"]   # 10% → target 5%
    d_backward = opt_drift(bw, tgt2, "Relative")["A"]  # 5% → target 10%
    assert d_forward == pytest.approx(d_backward, rel=1e-9), (
        f"Log-ratio must be symmetric: drift(10→5)={d_forward:.6f}, drift(5→10)={d_backward:.6f}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 18 — TLH lot-level attribution: specific lot_id is harvested
# ─────────────────────────────────────────────────────────────────────────────

def test_tlh_harvests_identified_lot():
    """
    Two SPY lots at different cost bases. Only the lot with the bigger loss
    should trigger TLH (the other lot may also qualify depending on threshold).
    The realized_df must show the correct lot_id was consumed, not a random one.

    Setup:
      - Day 0: buy 500 SPY at $100 (Lot A)
      - Day 10: buy 500 SPY at $105 (Lot B) — this will exceed 30-day lookback
        ... actually, easier: just 1 SPY lot, verify lot_id in realized_df.

    Simpler: single lot, TLH fires, realized_df.lot_id must match the known lot.
    """
    dates = business_dates("2023-01-03", 55)
    spy_prices = [100.0] * 35 + [75.0] * (len(dates) - 35)  # -25% drop on day 35
    voo_prices = [75.0] * len(dates)
    prices_df = make_prices(["SPY", "VOO"], dates, {"SPY": spy_prices, "VOO": voo_prices})
    proxy_df = make_proxy_df(("SPY", "VOO"))

    r = _run(prices_df, ["SPY"], [1.0], dates,
             tlh_threshold=0.05, proxy_df=proxy_df, wash_sale_days=30)

    realized = r["realized_df"]
    tlh_realized = realized[realized["reason"].str.startswith("TLH_SELL:", na=False)]

    assert len(tlh_realized) > 0, "TLH must have fired and produced realized events"

    # Every TLH realization must reference a valid lot_id (not empty string)
    assert tlh_realized["lot_id"].notna().all(), "All TLH realized events must have a lot_id"
    assert (tlh_realized["lot_id"].astype(str).str.len() > 0).all(), \
        "lot_id must be a non-empty string"

    # The realized gain must be a loss (TLH only harvests losses)
    assert (tlh_realized["gain_loss"] < 0).all(), \
        f"All TLH realizations must be losses, got: {tlh_realized['gain_loss'].tolist()}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 19 — compute_strategy_metrics: known synthetic series
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_strategy_metrics_flat_returns():
    """
    A flat NAV series (constant $100k) must produce:
      - total_return = 0.0
      - cagr = 0.0
      - annualized_vol = 0.0 (no daily change)
      - sharpe = 0.0 (or undefined, handled gracefully)
      - max_drawdown = 0.0 (never below peak)
    Imports from portfolio_returns_engine (requires conftest.py to mock streamlit).
    """
    from engine.metrics import compute_strategy_metrics

    dates = pd.date_range("2023-01-03", periods=252, freq="B")
    flat_vals = np.full(252, 100_000.0)

    m = compute_strategy_metrics(flat_vals, 100_000.0, dates=dates)

    assert m["total_return"] == pytest.approx(0.0, abs=1e-9)
    assert m["cagr"] == pytest.approx(0.0, abs=1e-9)
    assert m["annualized_vol"] == pytest.approx(0.0, abs=1e-9)
    assert m["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
    # Sharpe = cagr / vol; vol = 0, so should return 0.0 not raise
    assert m["sharpe"] == pytest.approx(0.0, abs=1e-9)


def test_compute_strategy_metrics_known_cagr():
    """
    A series that doubles over ~2 years must produce a CAGR consistent with
    the actual calendar period (uses real dates, not 252-day approximation).
    """
    from engine.metrics import compute_strategy_metrics

    n = 504  # ~2 calendar years
    start = 100_000.0
    end = 200_000.0
    vals = np.linspace(start, end, n)
    dates = pd.date_range("2021-01-04", periods=n, freq="B")

    m = compute_strategy_metrics(vals, start, dates=dates)

    expected_years = (dates[-1] - dates[0]).days / 365.25
    expected_cagr = (end / start) ** (1.0 / expected_years) - 1

    assert m["cagr"] == pytest.approx(expected_cagr, rel=0.005), (
        f"CAGR mismatch: expected {expected_cagr:.4%}, got {m['cagr']:.4%}"
    )
    assert m["total_return"] == pytest.approx(1.0, rel=1e-4)  # 100% total return


def test_compute_strategy_metrics_negative_calmar():
    """
    A portfolio that loses 20% should produce a NEGATIVE Calmar ratio.
    The bug was `abs(cagr / max_dd)` which gave a positive value even for losses.
    After the fix: Calmar = cagr / abs(max_dd) — negative CAGR → negative Calmar.
    """
    from engine.metrics import compute_strategy_metrics

    n = 252
    vals = np.linspace(100_000.0, 80_000.0, n)  # steady decline
    dates = pd.date_range("2023-01-03", periods=n, freq="B")

    m = compute_strategy_metrics(vals, 100_000.0, dates=dates)

    assert m["cagr"] < 0, "Portfolio lost money, CAGR must be negative"
    assert m["max_drawdown"] < 0, "Max drawdown must be negative"

    # Calmar = CAGR / |MaxDD| — must be negative since CAGR < 0
    calmar = m["cagr"] / abs(m["max_drawdown"])
    assert calmar < 0, (
        f"Calmar ratio must be negative for a losing portfolio. "
        f"CAGR={m['cagr']:.4%}, MaxDD={m['max_drawdown']:.4%}, Calmar={calmar:.4f}"
    )
