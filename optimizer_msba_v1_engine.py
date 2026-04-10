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
from typing import Dict, List, Tuple, Optional, Literal
import warnings

# ─────────────────────────────────────────────────────────────────────────────
# Schema constants
# ─────────────────────────────────────────────────────────────────────────────

LOT_COLUMNS = [
    "lot_id", "ticker", "open_date", "shares",
    "cost_basis", "total_cost", "source",
]

TRADE_COLUMNS = [
    "trade_id", "trade_date", "ticker", "action",
    "shares", "price", "gross_value", "exec_cost", "net_cash_impact", "reason",
]

REALIZED_COLUMNS = [
    "event_id", "event_date", "ticker", "event_type",
    "shares", "proceeds", "cost_basis", "gain_loss",
    "holding_days", "gain_type", "tax_rate", "tax_owed", "lot_id", "reason",
]

PROXY_COLUMNS = ["symbol", "lookup_type", "lookup_symbol", "order"]


# ─────────────────────────────────────────────────────────────────────────────
# Tax Engine
# ─────────────────────────────────────────────────────────────────────────────

class TaxEngine:
    """
    ST/LT capital gains tax engine with:
    - year-aware accounting
    - loss carry-forward
    - up-to-$3,000 ordinary income offset per year (treated at st_rate)
    - incremental tax settlement (tax/refund applied immediately to cash)

    Notes:
    - This is a simplified implementation meant for simulation/backtesting.
    - It supports negative incremental tax (refund) when YTD liability decreases.
    """

    def __init__(self, st_rate: float, lt_rate: float, lt_holding_days: int = 365):
        self.st_rate = st_rate
        self.lt_rate = lt_rate
        self.lt_days = lt_holding_days
        self.st_loss_cf: float = 0.0
        self.lt_loss_cf: float = 0.0

        # Year state
        self._year: Optional[int] = None
        self._st_total: float = 0.0
        self._lt_total: float = 0.0
        self._ordinary_offset_used: float = 0.0
        self._ytd_liability: float = 0.0

    def classify(self, open_date, close_date) -> Tuple[str, float]:
        days = (close_date - open_date).days
        # IRS "more than one year" = strictly more than 365 days (366+ days = LT).
        # Using >= 365 was an off-by-one error: a 365-day hold is still short-term.
        if days > self.lt_days:
            return "LT", self.lt_rate
        return "ST", self.st_rate

    def _reset_year(self, year: int):
        self._year = year
        self._st_total = 0.0
        self._lt_total = 0.0
        self._ordinary_offset_used = 0.0
        self._ytd_liability = 0.0

    def _compute_ytd_liability(self) -> Tuple[float, float, float, float, float, float]:
        """
        Compute current-year cumulative tax liability given:
        - realized totals (st/lt)
        - carryforwards (st/lt)
        - netting and $3k ordinary offset cap

        FIX 1 — Capital loss carryforward character:
        Returns character-split excess losses so the year-end rollover can push
        ST excess into st_loss_cf and LT excess into lt_loss_cf separately.
        Under IRS rules, the character (ST vs LT) of a capital loss must be
        preserved indefinitely — LT losses must NOT be reclassified as ST.

        Returns:
          (liability, ordinary_offset_used, excess_st_loss, excess_lt_loss,
           remaining_st_cf, remaining_lt_cf)
        """
        st = float(self._st_total)
        lt = float(self._lt_total)
        st_cf = float(self.st_loss_cf)
        lt_cf = float(self.lt_loss_cf)

        # Apply carryforward losses to same-type gains first, then cross-type.
        # IRS netting order: ST CF vs ST gains, LT CF vs LT gains, then cross.
        if st > 0 and st_cf > 0:
            used = min(st, st_cf)
            st -= used
            st_cf -= used
        if lt > 0 and lt_cf > 0:
            used = min(lt, lt_cf)
            lt -= used
            lt_cf -= used
        if st > 0 and lt_cf > 0:
            used = min(st, lt_cf)
            st -= used
            lt_cf -= used
        if lt > 0 and st_cf > 0:
            used = min(lt, st_cf)
            lt -= used
            st_cf -= used

        # Net ST vs LT within the year.
        # After cross-netting, at most one bucket can remain negative.
        if st > 0 and lt < 0:
            off = min(st, -lt)
            st -= off
            lt += off
        elif lt > 0 and st < 0:
            off = min(lt, -st)
            lt -= off
            st += off

        taxable_st = max(0.0, st)
        taxable_lt = max(0.0, lt)
        net = st + lt

        # FIX 1: Compute excess loss split by character.
        # After all netting, at most one of (st, lt) is negative; the other is 0.
        # The $3,000 ordinary offset is applied to the loss bucket that remains,
        # preserving the character of the resulting carryforward.
        ordinary_offset = 0.0
        excess_st_loss = 0.0
        excess_lt_loss = 0.0
        if net < 0:
            if st < 0:
                # Net loss is entirely in the ST bucket (lt == 0 here)
                ordinary_offset = min(3000.0, -st)
                excess_st_loss = max(0.0, -st - ordinary_offset)
            else:
                # Net loss is entirely in the LT bucket (st == 0 here)
                ordinary_offset = min(3000.0, -lt)
                # LT excess stays long-term — NOT reclassified as ST
                excess_lt_loss = max(0.0, -lt - ordinary_offset)

        liability = taxable_st * self.st_rate + taxable_lt * self.lt_rate - ordinary_offset * self.st_rate
        liability = float(liability)
        return liability, ordinary_offset, excess_st_loss, excess_lt_loss, st_cf, lt_cf

    def step(self, date, gain: float, gain_type: str, *, count_for_tax: bool = True) -> float:
        """
        Apply a realized gain/loss to the current-year tax ledger and return
        the incremental tax amount (positive=tax owed, negative=refund).

        FIX 2 — Annual tax settlement:
        The incremental tax delta is returned so the caller can decide whether
        to deduct it immediately (old behaviour) or accumulate it for year-end
        settlement. Portfolio.sell() now accumulates into _pending_tax_liability
        instead of deducting from cash immediately; settlement fires at year-end.

        If count_for_tax=False, the event is ignored for tax purposes (used for
        the tax-alpha shadow counterfactual).
        """
        year = pd.Timestamp(date).year
        if self._year is None:
            self._reset_year(year)
        elif year != self._year:
            # FIX 1: Preserve character when rolling excess losses forward.
            # rem_st_cf / rem_lt_cf = prior-year CF not consumed by this year's gains.
            # excess_st_loss / excess_lt_loss = this year's net loss beyond $3k offset.
            # Both are accumulated into the SAME-CHARACTER carryforward bucket.
            _, _, excess_st, excess_lt, rem_st_cf, rem_lt_cf = self._compute_ytd_liability()
            self.st_loss_cf = rem_st_cf + excess_st   # ST losses stay ST
            self.lt_loss_cf = rem_lt_cf + excess_lt   # LT losses stay LT
            self._reset_year(year)

        prev_liab = self._ytd_liability

        if count_for_tax and abs(gain) > 1e-12:
            if gain_type == "ST":
                self._st_total += gain
            else:
                self._lt_total += gain

        liab, ordinary_offset, _, _, _, _ = self._compute_ytd_liability()
        self._ordinary_offset_used = ordinary_offset
        self._ytd_liability = liab

        return float(self._ytd_liability - prev_liab)

    @property
    def ordinary_offset_used_ytd(self) -> float:
        return float(self._ordinary_offset_used)

    @property
    def current_year(self) -> Optional[int]:
        return self._year


class ProxyResolver:
    """Resolve original symbols to proxy alternatives in priority order."""

    def __init__(self, proxy_df: Optional[pd.DataFrame]):
        self._map: Dict[str, List[str]] = {}
        if proxy_df is None or proxy_df.empty:
            return
        df = proxy_df.copy()
        missing = [c for c in PROXY_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"proxy_df missing columns: {missing}")
        df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
        df["lookup_symbol"] = df["lookup_symbol"].astype(str).str.strip().str.upper()
        df["order"] = pd.to_numeric(df["order"], errors="coerce").astype("Int64")
        df = df.dropna(subset=["symbol", "lookup_symbol", "order"]).copy()
        df = df.sort_values(["symbol", "order", "lookup_symbol"])
        for sym, g in df.groupby("symbol", sort=False):
            self._map[sym] = g["lookup_symbol"].tolist()

    def proxies_for(self, symbol: str) -> List[str]:
        return self._map.get(str(symbol).strip().upper(), [])

    def sleeve_all_tickers(self, symbol: str) -> List[str]:
        sym = str(symbol).strip().upper()
        return [sym] + self.proxies_for(sym)


class WashSaleTracker:
    """
    Tracks purchase and loss-sale dates per symbol to enforce IRS wash-sale rules.

    Wash-sale window = [-30 days before loss, +30 days after loss]
    - Cannot buy within 30 days BEFORE a loss sale (lookback)
    - Cannot buy within 30 days AFTER a loss sale (forward block)
    """

    def __init__(self, wash_sale_days: int = 30):
        self.wash_sale_days = int(wash_sale_days)
        self._last_buy: Dict[str, pd.Timestamp] = {}      # Last purchase date per symbol
        self._last_loss: Dict[str, pd.Timestamp] = {}     # Last loss-sale date per symbol

    def record_buy(self, symbol: str, date):
        """Record a purchase (needed for 30-day lookback check)."""
        self._last_buy[str(symbol).strip().upper()] = pd.Timestamp(date)

    def record_loss_sale(self, symbol: str, date):
        """Record any loss sale (TLH, rebalancing, or other)."""
        self._last_loss[str(symbol).strip().upper()] = pd.Timestamp(date)

    def record_tlh_loss_sale(self, symbol: str, date):
        """Deprecated: alias for record_loss_sale (backward compatibility)."""
        self.record_loss_sale(symbol, date)

    def is_loss_sale_blocked(self, symbol: str, date) -> bool:
        """
        Check if selling at a loss is blocked (violation of 30-day lookback rule).

        IRS wash-sale: Cannot deduct a loss if you bought substantially identical
        security within 30 days BEFORE the loss sale.

        Returns True if blocked (loss sale would be disqualified).
        """
        if self.wash_sale_days <= 0:
            return False
        sym = str(symbol).strip().upper()
        last_buy = self._last_buy.get(sym)
        if last_buy is None:
            return False
        dt = pd.Timestamp(date)
        # Blocked if purchase was within last 30 days
        return dt <= (last_buy + pd.Timedelta(days=self.wash_sale_days))

    def is_buy_blocked(self, symbol: str, date) -> bool:
        """
        Check if purchasing is blocked (violation of 30-day forward rule).

        IRS wash-sale: Cannot buy substantially identical security within 30 days
        AFTER a loss sale.

        Returns True if blocked (purchase would trigger wash-sale disqualification).
        """
        if self.wash_sale_days <= 0:
            return False
        sym = str(symbol).strip().upper()
        last_loss = self._last_loss.get(sym)
        if last_loss is None:
            return False
        dt = pd.Timestamp(date)
        # Blocked if loss sale was within last 30 days
        return dt <= (last_loss + pd.Timedelta(days=self.wash_sale_days))


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

        # FIX 2 — Annual tax settlement:
        # Accumulate incremental tax deltas throughout the year; settle to cash at year-end.
        # This prevents phantom leverage from daily tax refunds inflating TLH performance.
        self._pending_tax_liability: float = 0.0

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
        """
        TAX_OPTIMAL ordering for selling:
          1. ST losses  (highest tax benefit — offset 35% income)
          2. LT losses  (offset 20% income)
          3. LT gains   (lowest tax cost — taxed at 20%)
          4. ST gains   (highest tax cost — taxed at 35%, sell last)
        Within each group, most negative P&L first (biggest losses / smallest gains).
        """
        lots = self._open_lots(ticker)
        if not lots:
            return lots
        for lot in lots:
            lot["_pnl"] = price - lot["cost_basis"]
            lot["_days"] = (date - lot["open_date"]).days
            lot["_is_loss"] = 1 if lot["_pnl"] < 0 else 0
            lot["_is_lt"] = 1 if lot["_days"] >= self.tax.lt_days else 0
        lots.sort(key=lambda x: (
            -x["_is_loss"],                                       # losses before gains
            x["_is_lt"] if x["_is_loss"] else -x["_is_lt"],      # losses: ST first; gains: LT first
            x["_pnl"],                                            # most negative P&L first
        ))
        return lots

    # ── buy ───────────────────────────────────────────────────────────────────

    def buy(self, date, ticker: str, shares: float, price: float, source: str = "BUY", reason: str = "",
            *, on_buy_executed: Optional[callable] = None):
        """
        Execute a buy order, create lot, record trade.

        on_buy_executed: optional callback(ticker=str, date=timestamp) to notify tracker of purchase.
        """
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
            "reason": reason,
        })

        # NEW: Notify wash-sale tracker of purchase
        if on_buy_executed is not None:
            on_buy_executed(ticker=ticker, date=date)

    # ── sell ──────────────────────────────────────────────────────────────────

    def sell(self, date, ticker: str, shares: float, price: float, lot_selection: str = "TAX_OPTIMAL",
             reason: str = "", *, tax_count_for_this_sale: Optional[callable] = None,
             on_loss_realized: Optional[callable] = None,
             check_wash_sale_lookback: Optional[callable] = None,
             lot_ids: Optional[List[str]] = None):
        """
        Execute a sell order with lot selection, realize gains/losses, pay taxes.

        lot_ids: if provided, only consume from these specific lots (in order given).
            Used by TLH to enforce exact lot-level harvesting. When None, falls back
            to lot_selection ordering (TAX_OPTIMAL or FIFO).
        on_loss_realized: optional callback(ticker=str, loss_amount=float) when a loss is realized.
        check_wash_sale_lookback: optional callback(ticker=str, date=timestamp) -> bool to validate
                                   30-day lookback (returns True if sale would violate wash-sale rule).
        """
        avail = self.shares_held(ticker)
        if shares > avail + 1e-9:
            shares = avail
        if shares < 1e-12:
            return

        # NEW: Pre-flight check for wash-sale lookback (would this sale violate the rule?)
        if check_wash_sale_lookback is not None:
            if check_wash_sale_lookback(ticker=ticker, date=date):
                # This sale would violate 30-day lookback; skip entirely
                return

        gross_proceeds = shares * price
        exec_cost = gross_proceeds * self._cost_rate  # slippage + spread reduces net proceeds
        net_proceeds = gross_proceeds - exec_cost
        self.total_commission_and_slippage += exec_cost

        if lot_ids is not None:
            # Precise lot-level selection: only sell from the specified lots, in order.
            # Used by TLH to guarantee the identified loss lots are actually harvested.
            lots = [
                self._lots[self._lot_id_map[lid]]
                for lid in lot_ids
                if lid in self._lot_id_map and self._lots[self._lot_id_map[lid]]["shares"] > 1e-12
            ]
        elif lot_selection == "TAX_OPTIMAL":
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
            count_for_tax = True
            if tax_count_for_this_sale is not None:
                count_for_tax = bool(tax_count_for_this_sale(gain=gain, gain_type=gain_type))
            tax = self.tax.step(date, gain, gain_type, count_for_tax=count_for_tax)

            eid = self._nid("R", "_rel_ctr")
            self._realized.append({
                "event_id": eid, "event_date": date, "ticker": ticker,
                "event_type": "SALE", "shares": sold, "proceeds": lot_proceeds,
                "cost_basis": lot_cost, "gain_loss": gain,
                "holding_days": (date - lot["open_date"]).days,
                "gain_type": gain_type, "tax_rate": tax_rate,
                "tax_owed": tax, "lot_id": lot["lot_id"], "reason": reason,
            })
            if abs(tax) > 1e-12:
                # FIX 2: Accumulate rather than deduct immediately; settled at year-end.
                self._pending_tax_liability += tax
                self._taxes.append({"date": date, "event_id": eid, "amount": tax})
            if gain < 0:
                self.total_losses_harvested += abs(gain)
                # NEW: Notify wash-sale tracker of loss realization
                if on_loss_realized is not None:
                    on_loss_realized(ticker=ticker, loss_amount=abs(gain))

            lot["shares"] -= sold
            lot["total_cost"] = lot["shares"] * lot["cost_basis"]
            remaining -= sold

        self.cash += net_proceeds  # net of execution costs

        self._trades.append({
            "trade_id": self._nid("T", "_trd_ctr"), "trade_date": date,
            "ticker": ticker, "action": "SELL", "shares": shares,
            "price": price, "gross_value": gross_proceeds,
            "exec_cost": round(exec_cost, 4), "net_cash_impact": net_proceeds,
            "reason": reason,
        })

    # ── dividend ──────────────────────────────────────────────────────────────

    def process_dividend(self, date, ticker: str, div_per_share: float,
                         price: float, reinvest: bool, *,
                         on_buy_executed: Optional[callable] = None,
                         dividend_tax_rate: float = 0.0):
        """
        Process dividend: tax it, add after-tax amount to cash, optionally reinvest (DRIP).

        dividend_tax_rate: fractional tax rate on dividend income (e.g. 0.20).
            Most ETF and equity dividends qualify for the long-term capital gains rate.
            Defaults to 0.0 for backward compatibility; callers should pass lt_rate for
            realistic after-tax modeling.
        on_buy_executed: optional callback to notify wash-sale tracker of DRIP purchases.
        """
        held = self.shares_held(ticker)
        if held < 1e-12:
            return
        gross = held * div_per_share
        tax = gross * dividend_tax_rate
        net = gross - tax
        self.cash += net
        if abs(tax) > 1e-12:
            self.total_tax_paid += tax

        if reinvest and price > 0:
            # DRIP: reinvest after-tax dividend proceeds (not gross)
            drip_shares = net / price
            self.buy(date, ticker, drip_shares, price, source="DRIP",
                     on_buy_executed=on_buy_executed)

    # ── valuation ─────────────────────────────────────────────────────────────

    def market_value(self, prices: Dict[str, float]) -> float:
        mv = 0.0
        for lot in self._lots:
            if lot["shares"] > 1e-12:
                mv += lot["shares"] * prices.get(lot["ticker"], 0.0)
        return mv

    def nav(self, prices: Dict[str, float]) -> float:
        return self.market_value(prices) + self.cash

    # ── tax settlement ────────────────────────────────────────────────────────

    def settle_annual_taxes(self) -> float:
        """
        FIX 2 — Annual tax settlement.
        Debit (or credit) the accumulated pending tax liability against cash.
        Called at the start of each new calendar year and once at simulation end.

        Returns the net amount settled (positive = taxes paid, negative = net refund).
        """
        amount = self._pending_tax_liability
        if abs(amount) > 1e-12:
            self.cash -= amount
            self.total_tax_paid += amount
        self._pending_tax_liability = 0.0
        return amount

    # ── liquidation ─────────────────────────────────────────────────────────

    def liquidation_value(self, prices: Dict[str, float], date) -> Dict[str, float]:
        """
        Compute after-tax liquidation value if all positions were sold today.

        Accounts for:
        - Unrealized ST and LT gains across all open lots
        - Loss carryforwards (ST and LT) that offset liquidation gains
        - Current-year realized gains already in the tax ledger
        - IRS netting rules and $3k ordinary income offset

        Returns dict with:
          liquidation_nav       : after-tax cash after selling everything
          unrealized_gain_st    : total unrealized short-term P&L
          unrealized_gain_lt    : total unrealized long-term P&L
          liquidation_tax       : additional tax owed from full liquidation
        """
        unrealized_st = 0.0
        unrealized_lt = 0.0
        for lot in self._lots:
            if lot["shares"] < 1e-12:
                continue
            px = prices.get(lot["ticker"], 0.0)
            gain = (px - lot["cost_basis"]) * lot["shares"]
            gain_type, _ = self.tax.classify(lot["open_date"], date)
            if gain_type == "ST":
                unrealized_st += gain
            else:
                unrealized_lt += gain

        # Temporarily add unrealized gains to tax engine to compute liability
        saved_st = self.tax._st_total
        saved_lt = self.tax._lt_total
        self.tax._st_total += unrealized_st
        self.tax._lt_total += unrealized_lt
        new_liab = self.tax._compute_ytd_liability()[0]
        # Restore state (no mutation)
        self.tax._st_total = saved_st
        self.tax._lt_total = saved_lt

        current_liab = self.tax._compute_ytd_liability()[0]
        liquidation_tax = new_liab - current_liab

        # Execution costs on selling all positions
        mv = self.market_value(prices)
        liq_exec_cost = mv * self._cost_rate

        nav = self.nav(prices)
        liq_nav = nav - self._pending_tax_liability - liquidation_tax - liq_exec_cost

        return {
            "liquidation_nav": round(liq_nav, 2),
            "unrealized_gain_st": round(unrealized_st, 2),
            "unrealized_gain_lt": round(unrealized_lt, 2),
            "liquidation_tax": round(liquidation_tax, 2),
            "liquidation_exec_cost": round(liq_exec_cost, 2),
        }

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
        elif freq == "Yearly":
            if dt.year != prev_y:
                rebal.add(dt)
                prev_m = dt.month; prev_y = dt.year
    return rebal


def _compute_drift(current_weights: dict, target_weights: dict, mode: str) -> dict:
    """
    Compute per-asset drift between current and target weights.

    Parameters
    ----------
    current_weights : dict
        Current portfolio weights {ticker: weight}
    target_weights : dict
        Target portfolio weights {ticker: weight}
    mode : str
        "Absolute": |w_current - w_target|  (percentage-point deviation)
        "Relative": |log(w_current / w_target)|  (symmetric log-ratio)
            The log-ratio is used instead of |w/tgt - 1| because it is symmetric:
            a drift from 10% → 5% has the same magnitude as 5% → 10%.
            This matches the formula used by the V4 threshold engine.

    Returns
    -------
    dict of {ticker: drift_value}
    """
    drift = {}
    for tk, w_tgt in target_weights.items():
        w_cur = current_weights.get(tk, 0.0)
        if mode == "Relative":
            # Symmetric log-ratio — consistent with compute_drift() in portfolio_returns_engine.py
            if w_tgt >= 1e-12 and w_cur > 1e-12:
                drift[tk] = abs(np.log(w_cur / w_tgt))
            elif w_tgt >= 1e-12:
                drift[tk] = abs(np.log(1e-12 / w_tgt))
            else:
                drift[tk] = abs(w_cur)
        else:
            drift[tk] = abs(w_cur - w_tgt)
    return drift


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
    proxy_df: Optional[pd.DataFrame] = None,
    wash_sale_days: int = 30,
    tlh_threshold_mode: Literal["explicit", "rule_of_thumb"] = "explicit",
    compute_tax_alpha: bool = True,
    drift_tolerance: Optional[float] = None,
    drift_mode: str = "Absolute",
    drift_cooldown: int = 0,
    forced_rebalance_dates: Optional[set] = None,
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
    rebalance_frequency: "Daily" | "Weekly" | "Monthly" | "Quarterly" | "Yearly" | "None"
    tax_rates          : {"st_rate": float, "lt_rate": float}
    tlh_threshold      : e.g. 0.05 means harvest if lot is down ≥ 5%
    reinvest_dividends : True → DRIP, False → keep as cash
    initial_capital    : dollar amount
    price_field        : column name for price in prices_df
    static             : if True, no rebalancing (buy-and-hold with TLH only)
    drift_tolerance    : if not None, enable drift-band rebalancing when any asset
                         drifts beyond this tolerance (e.g. 0.05 = 5%)
    drift_mode         : "Absolute" (percentage points) | "Relative" (proportional)
    drift_cooldown     : days to suppress drift re-triggers after a drift rebalance
    forced_rebalance_dates : optional set of dates to force rebalancing (e.g. from
                        pre-computed threshold triggers); merged with rebal_dates

    Returns
    -------
    dict with keys: nav_series, trades_df, realized_df, tax_paid_total
    """
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)

    # Normalize tickers
    tickers = [str(t).strip().upper() for t in tickers]

    proxy_resolver = ProxyResolver(proxy_df)
    wash_tracker = WashSaleTracker(wash_sale_days=wash_sale_days)

    # Determine all tickers needed for pricing/valuation (originals + proxies)
    all_needed = set(tickers)
    for tk in tickers:
        for ptk in proxy_resolver.proxies_for(tk):
            all_needed.add(ptk)
    all_needed_list = sorted(all_needed)

    # Threshold rule-of-thumb if requested
    if tlh_threshold_mode == "rule_of_thumb":
        # Daily rule: 15%, Monthly rule: 10% (fallback: 10%)
        if str(rebalance_frequency) == "Daily":
            tlh_threshold = 0.15
        elif str(rebalance_frequency) == "Monthly":
            tlh_threshold = 0.10
        else:
            tlh_threshold = 0.10

    # ── Build wide price matrix (once) ────────────────────────────────────────
    mask = (
        prices_df["TICKERSYMBOL"].isin(all_needed_list)
        & (prices_df["PRICEDATE"] >= start_dt)
        & (prices_df["PRICEDATE"] <= end_dt)
    )
    sub = prices_df.loc[mask, ["TICKERSYMBOL", "PRICEDATE", price_field]].copy()
    sub = sub.drop_duplicates(subset=["TICKERSYMBOL", "PRICEDATE"])
    wide = sub.pivot(index="PRICEDATE", columns="TICKERSYMBOL", values=price_field)
    wide = wide.sort_index().ffill()
    # Drop rows where any ticker still lacks a price (i.e., its history starts after
    # the requested start date). Backward fill is intentionally omitted — it would
    # propagate future prices into earlier dates, introducing lookahead bias.
    wide = wide.dropna()
    if wide.empty:
        raise ValueError(
            "No common trading dates found for all tickers after forward-fill. "
            "One or more tickers may lack price history at the requested start date."
        )

    missing = [t for t in all_needed_list if t not in wide.columns]
    if missing:
        raise ValueError(f"Tickers missing from price data: {missing}")
    wide = wide[all_needed_list]

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
            ddf = ddf[ddf["TICKERSYMBOL"].isin(all_needed_list)]
            for _, row in ddf.iterrows():
                key = (row["TICKERSYMBOL"], row["PAYDATE"])
                div_lookup[key] = div_lookup.get(key, 0.0) + float(row["DIVAMOUNT"])

    # ── Rebalance schedule ────────────────────────────────────────────────────
    if static:
        rebal_dates = set()
    else:
        rebal_dates = _build_rebalance_set(trading_dates, rebalance_frequency)
    if forced_rebalance_dates:
        rebal_dates = rebal_dates | {pd.Timestamp(d) for d in forced_rebalance_dates}

    # ── Drift-band rebalancing state ───────────────────────────────────────────
    drift_cooldown_remaining = 0
    drift_enabled = drift_tolerance is not None

    # ── Initialize portfolio ──────────────────────────────────────────────────
    tax_eng = TaxEngine(
        st_rate=tax_rates.get("st_rate", 0.35),
        lt_rate=tax_rates.get("lt_rate", 0.20),
    )
    pf = Portfolio(initial_capital, tax_eng, cost_config=cost_config)

    weight_map = dict(zip(tickers, weights))

    def _resolve_buy_symbol(original_symbol: str, date) -> str:
        sym = str(original_symbol).strip().upper()
        if not wash_tracker.is_buy_blocked(sym, date):
            return sym
        for alt in proxy_resolver.proxies_for(sym):
            if alt in wide.columns:
                return alt
        return sym  # fallback (may be missing/blocked; caller should guard)

    def _sleeve_value_and_holdings(original_symbol: str, prices_today: Dict[str, float]) -> Tuple[float, List[Tuple[str, float]]]:
        """Return (total_value, [(ticker, shares_held)...]) for original + proxies."""
        total = 0.0
        held: List[Tuple[str, float]] = []
        for tk in proxy_resolver.sleeve_all_tickers(original_symbol):
            sh = pf.shares_held(tk)
            if sh > 1e-12:
                held.append((tk, sh))
                total += sh * prices_today.get(tk, 0.0)
        return total, held

    tickers_set = set(tickers)

    def _attributed_value_and_holdings(original_symbol: str, prices_today: Dict[str, float]) -> Tuple[float, List[Tuple[str, float]]]:
        """
        Like _sleeve_value_and_holdings but prevents double-counting when
        portfolio tickers are each other's proxies (e.g., IVV ↔ OEF both in
        the portfolio). Skips proxy tickers that are separate portfolio allocations.

        Used by rebalancing and drift calculation where non-overlapping
        attribution is required.
        """
        total = 0.0
        held: List[Tuple[str, float]] = []
        for tk in proxy_resolver.sleeve_all_tickers(original_symbol):
            if tk != original_symbol and tk in tickers_set:
                continue  # managed as its own portfolio allocation
            sh = pf.shares_held(tk)
            if sh > 1e-12:
                held.append((tk, sh))
                total += sh * prices_today.get(tk, 0.0)
        return total, held

    # ── Callbacks for wash-sale tracking (general, no date dependency) ──────────
    def _on_buy_executed(ticker: str, date):
        """Track purchase for 30-day lookback validation."""
        wash_tracker.record_buy(ticker, date)

    def _check_wash_sale_lookback(ticker: str, date) -> bool:
        """Check if this loss sale would violate 30-day lookback rule. Returns True if blocked."""
        return wash_tracker.is_loss_sale_blocked(ticker, date)

    def _execute_rebalance(dt, reason_prefix: str):
        """
        Execute a rebalance: sell overweight assets, then buy underweight assets.

        Uses _attributed_value_and_holdings (non-overlapping) to prevent
        double-counting when portfolio tickers are each other's proxies.

        Rebalancing sells always proceed (never blocked by wash-sale lookback).
        If a sell would realize a wash-sale-disallowed loss, the sale executes
        but the tax deduction is suppressed via tax_count_for_this_sale.
        """
        total_val = pf.nav(prices_today)
        if total_val <= 0:
            return
        # Sell overweight positions first
        for tk in tickers:
            current_val, held = _attributed_value_and_holdings(tk, prices_today)
            target_val = total_val * weight_map[tk]
            if current_val > target_val + 1.0:  # sell excess
                dollars_to_sell = current_val - target_val
                sleeve = proxy_resolver.sleeve_all_tickers(tk)
                priority = {sym: idx for idx, sym in enumerate(sleeve)}
                held_sorted = sorted(held, key=lambda x: priority.get(x[0], 10_000), reverse=True)
                for held_tk, held_sh in held_sorted:
                    if dollars_to_sell <= 1.0:
                        break
                    px = prices_today.get(held_tk, 0.0)
                    if px <= 0:
                        continue
                    sh_to_sell = min(held_sh, dollars_to_sell / px)
                    # Wash-sale aware: sell always proceeds, but suppress tax
                    # deduction for losses that would be IRS-disallowed.
                    def _ws_tax_filter(gain, gain_type, _htk=held_tk):
                        if gain >= 0:
                            return True  # gains always count
                        return not wash_tracker.is_loss_sale_blocked(_htk, dt)
                    pf.sell(dt, held_tk, sh_to_sell, px, lot_selection="TAX_OPTIMAL",
                            reason=f"{reason_prefix}_SELL_FOR:{tk}",
                            on_loss_realized=_on_loss_realized,
                            tax_count_for_this_sale=_ws_tax_filter)
                    dollars_to_sell -= sh_to_sell * px

        # Recalculate NAV after sells (cash increased)
        total_val = pf.nav(prices_today)

        # Buy underweight positions (use attributed values for accurate targets)
        for tk in tickers:
            current_val, _ = _attributed_value_and_holdings(tk, prices_today)
            target_val = total_val * weight_map[tk]
            if target_val > current_val + 1.0:
                buy_sym = _resolve_buy_symbol(tk, dt)
                px = prices_today.get(buy_sym, 0.0)
                if px > 0:
                    buy_shares = (target_val - current_val) / px
                    pf.buy(dt, buy_sym, buy_shares, px, reason=f"{reason_prefix}_BUY_FOR:{tk}",
                           on_buy_executed=_on_buy_executed)

    # ── Day 0: initial purchases ──────────────────────────────────────────────
    day0 = trading_dates[0]

    # Day 0 loss realized callback (has access to day0)
    def _on_loss_realized_day0(ticker: str, loss_amount: float):
        """Track loss sale for 30-day forward block (Day 0)."""
        wash_tracker.record_loss_sale(ticker, day0)

    for tk in tickers:
        alloc = initial_capital * weight_map[tk]
        buy_tk = _resolve_buy_symbol(tk, day0)
        price = wide.loc[day0, buy_tk]
        shares = alloc / price
        # Do NOT record initial buys in wash-sale tracker — portfolio construction
        # is not a "repurchase" that should block TLH for the first 30 days.
        pf.buy(day0, buy_tk, shares, price, reason=f"INIT:{tk}")

    # ── Pre-allocate NAV array ────────────────────────────────────────────────
    n_days = len(trading_dates)
    nav_arr = np.empty(n_days, dtype=np.float64)

    # Record day 0 NAV
    prices_d0 = {tk: float(wide.loc[day0, tk]) for tk in all_needed_list}
    nav_arr[0] = pf.nav(prices_d0)

    # FIX 2: Track current simulation year for annual tax settlement detection.
    _sim_current_year = trading_dates[0].year

    # ── Daily loop ────────────────────────────────────────────────────────────
    for i in range(1, n_days):
        dt = trading_dates[i]
        prices_today = {tk: float(wide.loc[dt, tk]) for tk in all_needed_list}

        # FIX 2: Settle prior-year tax liability at the first trading day of each new year.
        # This mirrors annual tax payment (e.g., Dec 31 settlement) — cash leaves the portfolio
        # once per year rather than on each trade day, eliminating phantom leverage.
        if dt.year != _sim_current_year:
            pf.settle_annual_taxes()
            _sim_current_year = dt.year
        tlh_fired_today = False  # reset each day; suppresses drift check if TLH ran

        # Daily callbacks (capture current dt from loop)
        def _on_loss_realized(ticker: str, loss_amount: float):
            """Track loss sale for 30-day forward block (current day)."""
            wash_tracker.record_loss_sale(ticker, dt)

        # 1. Dividends — taxed at lt_rate (qualified dividend assumption)
        for tk in all_needed_list:
            div_amt = div_lookup.get((tk, dt))
            if div_amt is not None and div_amt > 0:
                pf.process_dividend(dt, tk, div_amt, prices_today.get(tk, 0.0), reinvest_dividends,
                                    on_buy_executed=_on_buy_executed,
                                    dividend_tax_rate=tax_rates.get("lt_rate", 0.20))

        # 2. Tax-Loss Harvesting — check each lot (original AND proxy lots)
        if tlh_threshold > 0:
            for tk in tickers:
                # SKIP TLH for tickers without proxies to avoid cash drag
                # (If no proxy available, TLH forces 30-day cash position, which offsets tax benefit)
                if proxy_df is not None and not proxy_df.empty:
                    has_proxy = (proxy_df["symbol"] == tk).any()
                    if not has_proxy:
                        continue  # Skip TLH for this ticker
                else:
                    # No proxy_df loaded, skip TLH for all tickers
                    continue

                # Check ALL sleeve tickers (original + proxies) for harvestable lots
                sleeve_tickers = proxy_resolver.sleeve_all_tickers(tk)
                lots_to_harvest = []  # (sell_ticker, lot_id, lot_shares)
                for stk in sleeve_tickers:
                    for lot in pf._open_lots(stk):
                        if lot["shares"] < 1e-12:
                            continue
                        px = prices_today.get(stk, 0.0)
                        if px <= 0:
                            continue
                        unrealized_pct = (px - lot["cost_basis"]) / lot["cost_basis"]
                        if unrealized_pct <= -tlh_threshold:
                            lots_to_harvest.append((stk, lot["lot_id"], lot["shares"]))

                for sell_tk, lot_id, lot_shares in lots_to_harvest:
                    tlh_fired_today = True  # suppress drift check this day
                    # Sell the specific identified lot (not TAX_OPTIMAL generic ordering).
                    # Passing lot_ids=[lot_id] ensures we harvest exactly the lot whose
                    # loss crossed the threshold, not whatever TAX_OPTIMAL happens to pick.
                    px = prices_today[sell_tk]
                    pf.sell(
                        dt, sell_tk, lot_shares, px,
                        lot_ids=[lot_id],
                        reason=f"TLH_SELL:{sell_tk}",
                        on_loss_realized=_on_loss_realized,
                        check_wash_sale_lookback=_check_wash_sale_lookback,
                    )
                    # Rebuy using a DIFFERENT sleeve ticker (not the one just sold)
                    rebuy_tk = None
                    for candidate in sleeve_tickers:
                        if candidate == sell_tk:
                            continue  # can't rebuy what we just sold
                        if wash_tracker.is_buy_blocked(candidate, dt):
                            continue  # wash-sale blocked
                        if prices_today.get(candidate, 0.0) > 0:
                            rebuy_tk = candidate
                            break
                    if rebuy_tk is not None:
                        rebuy_px = prices_today[rebuy_tk]
                        rebuy_dollars = lot_shares * px  # match sell proceeds, not share count
                        rebuy_shares = rebuy_dollars / rebuy_px
                        pf.buy(dt, rebuy_tk, rebuy_shares, rebuy_px, source="TLH_REBUY",
                               reason=f"TLH_REBUY_FOR:{tk}", on_buy_executed=_on_buy_executed)

        # 3. Calendar rebalancing
        if dt in rebal_dates:
            _execute_rebalance(dt, "REBAL")

        # 4. Drift-band rebalancing (skipped on days TLH fired — proxy already holds the position)
        if drift_enabled and drift_cooldown_remaining <= 0 and not tlh_fired_today:
            total_nav = pf.nav(prices_today)
            if total_nav > 0:
                current_weights = {
                    tk: _attributed_value_and_holdings(tk, prices_today)[0] / total_nav
                    for tk in tickers
                }
                drift = _compute_drift(current_weights, weight_map, drift_mode)
                if any(d > drift_tolerance for d in drift.values()):
                    _execute_rebalance(dt, "DRIFT_REBAL")
                    drift_cooldown_remaining = drift_cooldown

        # Decrement cooldown
        if drift_cooldown_remaining > 0:
            drift_cooldown_remaining -= 1

        # 5. Record NAV
        nav_arr[i] = pf.nav(prices_today)

    # FIX 2: Settle any remaining tax liability accrued in the final (partial) year.
    pf.settle_annual_taxes()

    # Re-record final NAV after settlement so it reflects all tax payments/refunds.
    # Without this, Final NAV is pre-settlement and Liquidation NAV is post-settlement,
    # causing Liquidation NAV > Final NAV when a pending refund exists.
    nav_arr[-1] = pf.nav(prices_today)

    # ── Build output ──────────────────────────────────────────────────────────
    nav_series = pd.Series(nav_arr, index=trading_dates, name="NAV")
    nav_series.index.name = "PRICEDATE"

    cfg = {**DEFAULT_COST_CONFIG, **(cost_config or {})}

    # Compute after-tax liquidation value at simulation end
    final_prices = {tk: float(wide.loc[trading_dates[-1], tk]) for tk in all_needed_list}
    liq = pf.liquidation_value(final_prices, trading_dates[-1])

    out = {
        "nav_series": nav_series,
        "trades_df": pf.trades_df(),
        "realized_df": pf.realized_df(),
        "tax_paid_total": pf.total_tax_paid,
        "losses_harvested": round(pf.total_losses_harvested, 2),
        "transaction_costs_total": round(pf.total_commission_and_slippage, 2),
        "cost_config": cfg,
        "ordinary_income_offset_used_ytd_final": pf.tax.ordinary_offset_used_ytd,
        "loss_carryforward_st": float(pf.tax.st_loss_cf),
        "loss_carryforward_lt": float(pf.tax.lt_loss_cf),
        "liquidation_nav": liq["liquidation_nav"],
        "unrealized_gain_st": liq["unrealized_gain_st"],
        "unrealized_gain_lt": liq["unrealized_gain_lt"],
        "liquidation_tax": liq["liquidation_tax"],
        "liquidation_exec_cost": liq["liquidation_exec_cost"],
    }

    # ── Tax Alpha computations ────────────────────────────────────────────────
    if compute_tax_alpha:
        # Strategy 2: no TLH trades baseline
        base = run_optimizer_simulation(
            prices_df=prices_df,
            dividends_df=dividends_df,
            tickers=tickers,
            weights=weights,
            start_date=start_date,
            end_date=end_date,
            rebalance_frequency=rebalance_frequency,
            tax_rates=tax_rates,
            tlh_threshold=0.0,
            reinvest_dividends=reinvest_dividends,
            initial_capital=initial_capital,
            price_field=price_field,
            static=static,
            cost_config=cost_config,
            proxy_df=proxy_df,
            wash_sale_days=wash_sale_days,
            tlh_threshold_mode="explicit",
            compute_tax_alpha=False,
            drift_tolerance=drift_tolerance,
            drift_mode=drift_mode,
            drift_cooldown=drift_cooldown,
            forced_rebalance_dates=forced_rebalance_dates,
        )
        nav_no_tlh = base["nav_series"].reindex(nav_series.index).ffill()
        tax_alpha_2 = nav_series - nav_no_tlh

        out.update({
            "nav_no_tlh": nav_no_tlh,
            "tax_alpha_2_series": tax_alpha_2,
            "tax_alpha_2_final": float(tax_alpha_2.iloc[-1]),
        })

    return out