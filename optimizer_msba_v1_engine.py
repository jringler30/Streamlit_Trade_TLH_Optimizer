"""
optimizer_msba_v1_engine.py
===========================
Tax-aware portfolio accounting engine with tax-loss harvesting (MSBA v1).

Public API:
    run_optimizer_simulation(...)  →  dict with nav_series, trades, gains, tax_paid

Adapted from portfolio_accounting_engine_v2.2.ipynb.
All logic is self-contained — no modifications to the Streamlit host required
beyond importing this module and calling the public function.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
import warnings

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# Schema constants
# ─────────────────────────────────────────────────────────────────────────────

LOT_COLUMNS = [
    "lot_id", "ticker", "open_date", "shares",
    "cost_basis", "total_cost", "source",
]

TRADE_COLUMNS = [
    "trade_id", "trade_date", "ticker", "action",
    "shares", "price", "gross_value", "net_cash_impact",
]

REALIZED_COLUMNS = [
    "event_id", "event_date", "ticker", "event_type",
    "shares", "proceeds", "cost_basis", "gain_loss",
    "holding_days", "gain_type", "tax_rate", "tax_owed", "lot_id",
]


# ─────────────────────────────────────────────────────────────────────────────
# Tax Engine
# ─────────────────────────────────────────────────────────────────────────────

class TaxEngine:
    """ST/LT capital gains tax with loss carry-forward netting."""

    def __init__(self, st_rate: float, lt_rate: float, lt_holding_days: int = 365):
        self.st_rate = st_rate
        self.lt_rate = lt_rate
        self.lt_days = lt_holding_days
        self.st_loss_cf: float = 0.0
        self.lt_loss_cf: float = 0.0

    def classify(self, open_date, close_date) -> Tuple[str, float]:
        days = (close_date - open_date).days
        if days >= self.lt_days:
            return "LT", self.lt_rate
        return "ST", self.st_rate

    def compute_tax(self, gain: float, gain_type: str) -> float:
        if gain < 0:
            if gain_type == "ST":
                self.st_loss_cf += abs(gain)
            else:
                self.lt_loss_cf += abs(gain)
            return 0.0
        if gain == 0:
            return 0.0

        taxable = gain
        if gain_type == "ST":
            used = min(taxable, self.st_loss_cf)
            taxable -= used; self.st_loss_cf -= used
            used = min(taxable, self.lt_loss_cf)
            taxable -= used; self.lt_loss_cf -= used
            return taxable * self.st_rate
        else:
            used = min(taxable, self.lt_loss_cf)
            taxable -= used; self.lt_loss_cf -= used
            used = min(taxable, self.st_loss_cf)
            taxable -= used; self.st_loss_cf -= used
            return taxable * self.lt_rate


# ─────────────────────────────────────────────────────────────────────────────
# Default transaction cost configuration
# commission_per_trade_bps: flat execution cost in bps of trade value
# slippage_bps: market impact / price improvement slippage in bps
# bid_ask_bps: half-spread cost in bps (round-trip is 2× for buys+sells)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_COST_CONFIG: Dict[str, float] = {
    "commission_bps": 5.0,    # ~$0.005/share on a $100 stock = 5 bps
    "slippage_bps": 5.0,      # 5 bps market impact
    "bid_ask_bps": 2.0,       # 2 bps half-spread (one-way)
}


class Portfolio:
    """Trade-driven portfolio with lot tracking, realized gain accounting, tax, and transaction costs."""

    def __init__(self, initial_cash: float, tax_engine: TaxEngine,
                 cost_config: Optional[Dict[str, float]] = None):
        self.cash = initial_cash
        self.tax = tax_engine

        # Merge provided config with defaults so callers only need to override what they change
        cfg = {**DEFAULT_COST_CONFIG, **(cost_config or {})}
        self._cost_rate = (cfg["commission_bps"] + cfg["slippage_bps"] + cfg["bid_ask_bps"]) / 10_000.0

        self._lot_ctr = 0
        self._trd_ctr = 0
        self._rel_ctr = 0

        # Lots stored as list-of-dicts for speed (no repeated DataFrame rebuild)
        self._lots: List[dict] = []
        self._lots_idx: Dict[str, List[int]] = {}  # ticker → list of _lots indices
        self._lot_id_map: Dict[str, int] = {}       # lot_id → index

        self._trades: List[dict] = []
        self._realized: List[dict] = []
        self._taxes: List[dict] = []
        self.total_tax_paid: float = 0.0
        self.total_commission_and_slippage: float = 0.0  # cumulative execution costs
        self.total_losses_harvested: float = 0.0          # absolute value of harvested losses

    # ── helpers ───────────────────────────────────────────────────────────────

    def _nid(self, prefix: str, counter_attr: str) -> str:
        val = getattr(self, counter_attr) + 1
        setattr(self, counter_attr, val)
        return f"{prefix}{val:06d}"

    def shares_held(self, ticker: str) -> float:
        return sum(
            self._lots[i]["shares"]
            for i in self._lots_idx.get(ticker, [])
            if self._lots[i]["shares"] > 1e-12
        )

    def _open_lots(self, ticker: str) -> List[dict]:
        return [
            self._lots[i]
            for i in self._lots_idx.get(ticker, [])
            if self._lots[i]["shares"] > 1e-12
        ]

    def _sorted_lots_for_sell(self, ticker: str, price: float, date) -> List[dict]:
        """TAX_OPTIMAL ordering: losses first (biggest ST loss first), then smallest gains."""
        lots = self._open_lots(ticker)
        if not lots:
            return lots
        for lot in lots:
            lot["_pnl"] = price - lot["cost_basis"]
            lot["_days"] = (date - lot["open_date"]).days
            lot["_is_loss"] = 1 if lot["_pnl"] < 0 else 0
            lot["_is_lt"] = 1 if lot["_days"] >= self.tax.lt_days else 0
        lots.sort(key=lambda x: (-x["_is_loss"], x["_is_lt"], x["_pnl"]))
        return lots

    # ── buy ───────────────────────────────────────────────────────────────────

    def buy(self, date, ticker: str, shares: float, price: float, source: str = "BUY"):
        gross = shares * price
        exec_cost = gross * self._cost_rate  # commission + slippage + bid-ask on buys
        total_cash_needed = gross + exec_cost
        if total_cash_needed > self.cash + 1e-6:
            # Back-solve shares from available cash including execution cost
            shares = self.cash / (price * (1 + self._cost_rate))
            gross = shares * price
            exec_cost = gross * self._cost_rate
            total_cash_needed = gross + exec_cost
        if shares < 1e-12:
            return

        self.cash -= total_cash_needed
        self.total_commission_and_slippage += exec_cost

        lid = self._nid("L", "_lot_ctr")
        lot = {
            "lot_id": lid, "ticker": ticker, "open_date": date,
            "shares": shares, "cost_basis": price, "total_cost": gross, "source": source,
        }
        idx = len(self._lots)
        self._lots.append(lot)
        self._lots_idx.setdefault(ticker, []).append(idx)
        self._lot_id_map[lid] = idx

        self._trades.append({
            "trade_id": self._nid("T", "_trd_ctr"), "trade_date": date,
            "ticker": ticker, "action": source, "shares": shares,
            "price": price, "gross_value": gross,
            "exec_cost": round(exec_cost, 4), "net_cash_impact": -(gross + exec_cost),
        })

    # ── sell ──────────────────────────────────────────────────────────────────

    def sell(self, date, ticker: str, shares: float, price: float, lot_selection: str = "TAX_OPTIMAL"):
        avail = self.shares_held(ticker)
        if shares > avail + 1e-9:
            shares = avail
        if shares < 1e-12:
            return

        gross_proceeds = shares * price
        exec_cost = gross_proceeds * self._cost_rate  # slippage + spread reduces net proceeds
        net_proceeds = gross_proceeds - exec_cost
        self.total_commission_and_slippage += exec_cost

        if lot_selection == "TAX_OPTIMAL":
            lots = self._sorted_lots_for_sell(ticker, price, date)
        else:
            lots = sorted(self._open_lots(ticker), key=lambda x: x["open_date"])

        remaining = shares
        for lot in lots:
            if remaining < 1e-12:
                break
            sold = min(lot["shares"], remaining)
            gain_type, tax_rate = self.tax.classify(lot["open_date"], date)
            lot_proceeds = sold * price
            lot_cost = sold * lot["cost_basis"]
            gain = lot_proceeds - lot_cost
            tax = self.tax.compute_tax(gain, gain_type)

            eid = self._nid("R", "_rel_ctr")
            self._realized.append({
                "event_id": eid, "event_date": date, "ticker": ticker,
                "event_type": "SALE", "shares": sold, "proceeds": lot_proceeds,
                "cost_basis": lot_cost, "gain_loss": gain,
                "holding_days": (date - lot["open_date"]).days,
                "gain_type": gain_type, "tax_rate": tax_rate,
                "tax_owed": tax, "lot_id": lot["lot_id"],
            })
            if tax > 0:
                self.cash -= tax
                self.total_tax_paid += tax
                self._taxes.append({"date": date, "event_id": eid, "amount": tax})
            if gain < 0:
                self.total_losses_harvested += abs(gain)

            lot["shares"] -= sold
            lot["total_cost"] = lot["shares"] * lot["cost_basis"]
            remaining -= sold

        self.cash += net_proceeds  # net of execution costs

        self._trades.append({
            "trade_id": self._nid("T", "_trd_ctr"), "trade_date": date,
            "ticker": ticker, "action": "SELL", "shares": shares,
            "price": price, "gross_value": gross_proceeds,
            "exec_cost": round(exec_cost, 4), "net_cash_impact": net_proceeds,
        })

    # ── dividend ──────────────────────────────────────────────────────────────

    def process_dividend(self, date, ticker: str, div_per_share: float,
                         price: float, reinvest: bool):
        held = self.shares_held(ticker)
        if held < 1e-12:
            return
        gross = held * div_per_share
        # No separate dividend tax in MSBA v1 — dividends treated as income
        self.cash += gross

        if reinvest and price > 0:
            drip_shares = gross / price
            self.buy(date, ticker, drip_shares, price, source="DRIP")

    # ── valuation ─────────────────────────────────────────────────────────────

    def market_value(self, prices: Dict[str, float]) -> float:
        mv = 0.0
        for lot in self._lots:
            if lot["shares"] > 1e-12:
                mv += lot["shares"] * prices.get(lot["ticker"], 0.0)
        return mv

    def nav(self, prices: Dict[str, float]) -> float:
        return self.market_value(prices) + self.cash

    # ── output accessors ──────────────────────────────────────────────────────

    def trades_df(self) -> pd.DataFrame:
        if not self._trades:
            return pd.DataFrame(columns=TRADE_COLUMNS)
        return pd.DataFrame(self._trades)

    def realized_df(self) -> pd.DataFrame:
        if not self._realized:
            return pd.DataFrame(columns=REALIZED_COLUMNS)
        return pd.DataFrame(self._realized)


# ─────────────────────────────────────────────────────────────────────────────
# Simulation Driver
# ─────────────────────────────────────────────────────────────────────────────

def _build_rebalance_set(trading_dates, freq: str):
    """Return set of dates on which to rebalance."""
    dates = pd.DatetimeIndex(trading_dates)
    if len(dates) < 2 or freq == "None":
        return set()
    if freq == "Daily":
        return set(dates[1:])
    rebal = set()
    prev_m, prev_y = dates[0].month, dates[0].year
    prev_w = dates[0].isocalendar()[1]
    for dt in dates[1:]:
        if freq == "Weekly":
            w = dt.isocalendar()[1]
            if w != prev_w or dt.year != prev_y:
                rebal.add(dt)
                prev_w = w; prev_y = dt.year
        elif freq == "Monthly":
            if dt.month != prev_m or dt.year != prev_y:
                rebal.add(dt)
                prev_m = dt.month; prev_y = dt.year
        elif freq == "Quarterly":
            if dt.month in {1, 4, 7, 10} and (dt.month != prev_m or dt.year != prev_y):
                rebal.add(dt)
            if dt.month != prev_m or dt.year != prev_y:
                prev_m = dt.month; prev_y = dt.year
    return rebal


def run_optimizer_simulation(
    prices_df: pd.DataFrame,
    dividends_df: Optional[pd.DataFrame],
    tickers: List[str],
    weights: List[float],
    start_date,
    end_date,
    rebalance_frequency: str,
    tax_rates: Dict[str, float],
    tlh_threshold: float,
    reinvest_dividends: bool,
    initial_capital: float = 100_000.0,
    price_field: str = "PRICECLOSE",
    static: bool = False,
    cost_config: Optional[Dict[str, float]] = None,
) -> dict:
    """
    Run the MSBA v1 tax-aware portfolio simulation.

    Parameters
    ----------
    prices_df          : long-format prices with TICKERSYMBOL, PRICEDATE, price_field
    dividends_df       : dividend data with TICKERSYMBOL, PAYDATE, DIVAMOUNT (or None)
    tickers            : list of ticker symbols
    weights            : target weights (same order as tickers)
    start_date/end_date: simulation window
    rebalance_frequency: "Daily" | "Weekly" | "Monthly" | "Quarterly" | "None"
    tax_rates          : {"st_rate": float, "lt_rate": float}
    tlh_threshold      : e.g. 0.05 means harvest if lot is down ≥ 5%
    reinvest_dividends : True → DRIP, False → keep as cash
    initial_capital    : dollar amount
    price_field        : column name for price in prices_df
    static             : if True, no rebalancing (buy-and-hold with TLH only)

    Returns
    -------
    dict with keys: nav_series, trades_df, realized_df, tax_paid_total
    """
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)

    # ── Build wide price matrix (once) ────────────────────────────────────────
    mask = (
        prices_df["TICKERSYMBOL"].isin(tickers)
        & (prices_df["PRICEDATE"] >= start_dt)
        & (prices_df["PRICEDATE"] <= end_dt)
    )
    sub = prices_df.loc[mask, ["TICKERSYMBOL", "PRICEDATE", price_field]].copy()
    sub = sub.drop_duplicates(subset=["TICKERSYMBOL", "PRICEDATE"])
    wide = sub.pivot(index="PRICEDATE", columns="TICKERSYMBOL", values=price_field)
    wide = wide.sort_index().ffill().bfill()

    missing = [t for t in tickers if t not in wide.columns]
    if missing:
        raise ValueError(f"Tickers missing from price data: {missing}")
    wide = wide[tickers]

    trading_dates = wide.index.tolist()
    if len(trading_dates) < 2:
        raise ValueError("Not enough trading dates for simulation.")

    # ── Pre-index dividends by (ticker, date) ─────────────────────────────────
    div_lookup: Dict[Tuple[str, pd.Timestamp], float] = {}
    if dividends_df is not None and not dividends_df.empty:
        ddf = dividends_df.copy()
        ddf["PAYDATE"] = pd.to_datetime(ddf["PAYDATE"], errors="coerce")
        if "TICKERSYMBOL" in ddf.columns:
            ddf["TICKERSYMBOL"] = ddf["TICKERSYMBOL"].astype(str).str.strip().str.upper()
            ddf = ddf[ddf["TICKERSYMBOL"].isin(tickers)]
            for _, row in ddf.iterrows():
                key = (row["TICKERSYMBOL"], row["PAYDATE"])
                div_lookup[key] = div_lookup.get(key, 0.0) + float(row["DIVAMOUNT"])

    # ── Rebalance schedule ────────────────────────────────────────────────────
    if static:
        rebal_dates = set()
    else:
        rebal_dates = _build_rebalance_set(trading_dates, rebalance_frequency)

    # ── Initialize portfolio ──────────────────────────────────────────────────
    tax_eng = TaxEngine(
        st_rate=tax_rates.get("st_rate", 0.35),
        lt_rate=tax_rates.get("lt_rate", 0.20),
    )
    pf = Portfolio(initial_capital, tax_eng, cost_config=cost_config)

    weight_map = dict(zip(tickers, weights))

    # ── Day 0: initial purchases ──────────────────────────────────────────────
    day0 = trading_dates[0]
    for tk in tickers:
        alloc = initial_capital * weight_map[tk]
        price = wide.loc[day0, tk]
        shares = alloc / price
        pf.buy(day0, tk, shares, price)

    # ── Pre-allocate NAV array ────────────────────────────────────────────────
    n_days = len(trading_dates)
    nav_arr = np.empty(n_days, dtype=np.float64)

    # Record day 0 NAV
    prices_d0 = {tk: wide.loc[day0, tk] for tk in tickers}
    nav_arr[0] = pf.nav(prices_d0)

    # ── Daily loop ────────────────────────────────────────────────────────────
    for i in range(1, n_days):
        dt = trading_dates[i]
        prices_today = {tk: wide.loc[dt, tk] for tk in tickers}

        # 1. Dividends
        for tk in tickers:
            div_amt = div_lookup.get((tk, dt))
            if div_amt is not None and div_amt > 0:
                pf.process_dividend(dt, tk, div_amt, prices_today[tk], reinvest_dividends)

        # 2. Tax-Loss Harvesting — check each lot
        if tlh_threshold > 0:
            for tk in tickers:
                lots_to_harvest = []
                for lot in pf._open_lots(tk):
                    if lot["shares"] < 1e-12:
                        continue
                    unrealized_pct = (prices_today[tk] - lot["cost_basis"]) / lot["cost_basis"]
                    if unrealized_pct <= -tlh_threshold:
                        lots_to_harvest.append((lot["lot_id"], lot["shares"]))

                for lot_id, lot_shares in lots_to_harvest:
                    # Sell the lot
                    pf.sell(dt, tk, lot_shares, prices_today[tk], lot_selection="TAX_OPTIMAL")
                    # Immediately rebuy (reset cost basis)
                    pf.buy(dt, tk, lot_shares, prices_today[tk], source="TLH_REBUY")

        # 3. Rebalancing
        if dt in rebal_dates:
            total_val = pf.nav(prices_today)
            if total_val > 0:
                # Sell overweight positions first
                for tk in tickers:
                    current_val = pf.shares_held(tk) * prices_today[tk]
                    target_val = total_val * weight_map[tk]
                    if current_val > target_val + 1.0:  # sell excess
                        sell_shares = (current_val - target_val) / prices_today[tk]
                        pf.sell(dt, tk, sell_shares, prices_today[tk], lot_selection="TAX_OPTIMAL")

                # Recalculate NAV after sells (cash increased)
                total_val = pf.nav(prices_today)

                # Buy underweight positions
                for tk in tickers:
                    current_val = pf.shares_held(tk) * prices_today[tk]
                    target_val = total_val * weight_map[tk]
                    if target_val > current_val + 1.0:
                        buy_shares = (target_val - current_val) / prices_today[tk]
                        pf.buy(dt, tk, buy_shares, prices_today[tk])

        # 4. Record NAV
        nav_arr[i] = pf.nav(prices_today)

    # ── Build output ──────────────────────────────────────────────────────────
    nav_series = pd.Series(nav_arr, index=trading_dates, name="NAV")
    nav_series.index.name = "PRICEDATE"

    cfg = {**DEFAULT_COST_CONFIG, **(cost_config or {})}
    return {
        "nav_series": nav_series,
        "trades_df": pf.trades_df(),
        "realized_df": pf.realized_df(),
        "tax_paid_total": pf.total_tax_paid,
        "losses_harvested": round(pf.total_losses_harvested, 2),
        "transaction_costs_total": round(pf.total_commission_and_slippage, 2),
        "cost_config": cfg,
    }