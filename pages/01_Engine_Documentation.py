"""Engine Documentation — all five sections in one page with tab navigation"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
st.set_page_config(page_title="Engine Documentation", page_icon="📚", layout="wide")

from ui_style import inject_site_css, render_hero, section_sep, section_header, render_footer
inject_site_css()

render_hero(
    eyebrow="Portfolio Accounting Engine",
    title='Built on trades.<br>Not on <em>assumptions.</em>',
    subtitle="A transaction-driven engine for portfolio valuation, tax handling, and dividend reinvestment. Every dollar is traceable. Every gain is correctly taxed. Every price is real.",
    formula='Portfolio Value &nbsp;=&nbsp; <span>(Shares × Price)</span> &nbsp;+&nbsp; Cash',
)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Core Engine",
    "Tax Engine",
    "Sell & TLH",
    "Dividends",
    "Valuation & Performance",
])

# ═══════════════════════════════════════════════════════════
# TAB 1 — Core Engine Overview
# ═══════════════════════════════════════════════════════════
with tab1:
    section_sep("01", "System Overview")
    section_header(
        "The Problem",
        "Most portfolio models are wrong<br>from the start.",
        "Most simulations compound daily returns — multiply yesterday's value by today's price change. It's fast, but it can't answer the question that actually matters: <em>if I liquidated everything right now, what would I walk away with?</em>",
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="vise-label" style="margin-bottom:0.5rem;">What most models do wrong</div>', unsafe_allow_html=True)
        st.markdown("""
<div class="vise-principle" style="border-color: rgba(255,140,97,0.3);">
  <div class="vise-principle-icon" style="background:rgba(255,140,97,0.1);border-color:rgba(255,140,97,0.3);color:var(--accent3);">✕</div>
  <div>
    <div class="vise-principle-title" style="color:var(--accent3);">Inject dividends into return stream</div>
    <div class="vise-principle-body">Blurs price appreciation and cash received. Prevents lot-level tracking of DRIP shares.</div>
  </div>
</div>
<div class="vise-principle" style="border-color: rgba(255,140,97,0.3);">
  <div class="vise-principle-icon" style="background:rgba(255,140,97,0.1);border-color:rgba(255,140,97,0.3);color:var(--accent3);">✕</div>
  <div>
    <div class="vise-principle-title" style="color:var(--accent3);">Apply taxes as an end-of-year haircut</div>
    <div class="vise-principle-body">Ignores the timing effect of taxes on reinvestable cash throughout the year.</div>
  </div>
</div>
<div class="vise-principle" style="border-color: rgba(255,140,97,0.3);">
  <div class="vise-principle-icon" style="background:rgba(255,140,97,0.1);border-color:rgba(255,140,97,0.3);color:var(--accent3);">✕</div>
  <div>
    <div class="vise-principle-title" style="color:var(--accent3);">Average away transaction costs</div>
    <div class="vise-principle-body">Overstates returns and loses the slippage/commission signal in the cost basis.</div>
  </div>
</div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="vise-label" style="margin-bottom:0.5rem;">What this engine does instead</div>', unsafe_allow_html=True)
        st.markdown("""
<div class="vise-principle">
  <div class="vise-principle-icon">✓</div>
  <div>
    <div class="vise-principle-title">Every dollar traces to a trade</div>
    <div class="vise-principle-body">Nothing happens unless a trade executes. No phantom gains, no drift.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon">✓</div>
  <div>
    <div class="vise-principle-title">Taxes deducted from cash immediately</div>
    <div class="vise-principle-body">The portfolio value shown is always the true after-tax number. No adjustment needed.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon">✓</div>
  <div>
    <div class="vise-principle-title">Costs baked into cost basis</div>
    <div class="vise-principle-body">Slippage and commission increase the cost basis of each lot, reducing overstated gains.</div>
  </div>
</div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:2rem;">The Five Components</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-card-grid">
  <div class="vise-card">
    <div class="vise-card-num">01</div>
    <div class="vise-card-title">Portfolio</div>
    <div><span class="vise-card-tag tag-stateful">Stateful</span></div>
    <p>The central ledger. Holds all state: lots, cash, trade history, realized gains, and taxes paid. Every other component reads from or writes to this object.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">02</div>
    <div class="vise-card-title">Tax Engine</div>
    <div><span class="vise-card-tag tag-stateless">Stateless</span></div>
    <p>A pure calculator. Given a gain amount and a holding period, returns the correct tax rate and amount owed.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">03</div>
    <div class="vise-card-title">Transaction Cost Engine</div>
    <div><span class="vise-card-tag tag-stateless">Stateless</span></div>
    <p>Another pure calculator. Given an action and a base price, returns execution price after slippage and all-in cash impact including commission.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">04</div>
    <div class="vise-card-title">Daily Valuation Engine</div>
    <div><span class="vise-card-tag tag-orchestrator">Orchestrator</span></div>
    <p>The outer loop. Iterates through every trading day, processes dividends, executes scheduled trades, and records the daily NAV snapshot.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">05</div>
    <div class="vise-card-title">Portfolio Reporter</div>
    <div><span class="vise-card-tag tag-stateless">Read-Only</span></div>
    <p>Takes the final Portfolio state and produces returns, tax summaries, gain breakdowns, turnover statistics, and a visualization dashboard.</p>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1.5rem;">How Cash Moves</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-outputs">
  <div class="vise-output-item"><div class="vise-output-name" style="color:var(--accent)">BUY</div><div class="vise-output-desc">Cash out → −(shares × exec_price) − commission</div></div>
  <div class="vise-output-item"><div class="vise-output-name" style="color:var(--accent)">SELL</div><div class="vise-output-desc">Cash in → +(shares × exec_price) − commission</div></div>
  <div class="vise-output-item"><div class="vise-output-name" style="color:var(--accent)">DIVIDEND</div><div class="vise-output-desc">Cash in → tax out → +gross_div, then −(gross_div × dividend_rate)</div></div>
  <div class="vise-output-item"><div class="vise-output-name" style="color:var(--accent)">DRIP</div><div class="vise-output-desc">Cash out → −(drip_shares × drip_price) − commission</div></div>
  <div class="vise-output-item"><div class="vise-output-name" style="color:var(--accent)">CAPITAL GAINS</div><div class="vise-output-desc">Tax out → −(realized_gain × applicable_rate)</div></div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1.5rem;">Key Classes at a Glance</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-classref">
  <div class="vise-classref-title">class Portfolio</div>
  <div class="vise-classref-method"><div class="vise-crm-sig">buy(date, tid, ticker, shares, price)</div><div class="vise-crm-desc">Opens a new lot. Applies slippage + commission. Guards against insufficient cash.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">sell(date, tid, ticker, shares, price, lot_selection)</div><div class="vise-crm-desc">Disposes lots via FIFO/LIFO/TAX_OPTIMAL. Realizes gains, computes tax, deducts from cash.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">process_dividend(pay_date, tid, ticker, div_amount, price)</div><div class="vise-crm-desc">Full 6-step DRIP sequence: gross div → tax → log → deduct → DRIP buy → new lot.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">portfolio_value(price_lookup)</div><div class="vise-crm-desc">Returns market_value + cash. Recomputed from first principles on every call.</div></div>
</div>
<div class="vise-classref" style="border-left-color: var(--accent3);">
  <div class="vise-classref-title" style="color:var(--accent3);">class DailyValuationEngine</div>
  <div class="vise-classref-method"><div class="vise-crm-sig">__init__(portfolio, prices, dividends)</div><div class="vise-crm-desc">Builds the forward-filled price pivot table and pre-indexes dividends by pay date. All expensive setup happens here — once.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">run(start_date, end_date, scheduled_trades)</div><div class="vise-crm-desc">The outer loop. Iterates pd.bdate_range, processes dividends and trades, records NAV each day.</div></div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 2 — Tax Engine
# ═══════════════════════════════════════════════════════════
with tab2:
    section_sep("02", "Tax Engine")
    section_header(
        "Tax Treatment",
        "Three categories.<br>One moment of truth.",
        "Taxes are deducted from cash the exact moment a gain is realized — not at year-end, not as a return adjustment. This keeps portfolio value accurate every day, because timing of tax payments affects reinvestable cash.",
    )

    st.markdown("""
<div class="vise-tax-grid">
  <div class="vise-tax-card st">
    <div class="vise-tax-rate">35%</div>
    <div class="vise-tax-name">Short-Term Capital Gains</div>
    <div class="vise-tax-desc">Triggered when selling shares held for <strong>fewer than 365 days</strong>. Taxed at ordinary income rates. Frequent trading or early exits land here.</div>
  </div>
  <div class="vise-tax-card lt">
    <div class="vise-tax-rate">20%</div>
    <div class="vise-tax-name">Long-Term Capital Gains</div>
    <div class="vise-tax-desc">Triggered when selling shares held for <strong>365 days or more</strong>. Preferential rate. Holding one extra day past the threshold can change your tax bill materially.</div>
  </div>
  <div class="vise-tax-card div">
    <div class="vise-tax-rate">15%</div>
    <div class="vise-tax-name">Dividend Income</div>
    <div class="vise-tax-desc">Triggered on <strong>PAYDATE</strong> only. Applied to gross dividend before reinvestment. The after-tax amount is what gets reinvested via a DRIP trade.</div>
  </div>
</div>
""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="vise-label">Holding Period Classification</div>', unsafe_allow_html=True)
        st.markdown("""
<p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:1rem;line-height:1.7;">
Every lot carries an <code style="font-family:'DM Mono',monospace;font-size:0.82em;background:var(--surface2);border:1px solid var(--border);padding:0.1em 0.4em;border-radius:3px;color:var(--accent2);">open_date</code>. When the lot is sold, the engine computes holding days and routes the gain to the correct tax tier.
</p>
<div class="vise-code"><span class="cm"># Classification logic in TaxEngine</span>
holding_days = (sale_date - open_date).days

<span class="kw">if</span> holding_days >= <span class="s">365</span>:
    gain_type = <span class="s">'LT'</span>  <span class="cm"># 20% rate</span>
<span class="kw">else</span>:
    gain_type = <span class="s">'ST'</span>  <span class="cm"># 35% rate</span>

<span class="cm"># Happens at LOT level — same sell order
# can trigger both ST and LT events</span></div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="vise-label">Losses — Conservative Default</div>', unsafe_allow_html=True)
        st.markdown("""
<p style="color:var(--text-muted);font-size:0.9rem;margin-bottom:1rem;line-height:1.7;">
The engine does not generate tax refunds for losing positions. A loss is recorded in the Realized Gains Ledger with a negative gain_loss value and tax_owed = 0.
</p>
<div class="vise-principle">
  <div class="vise-principle-icon">→</div>
  <div>
    <div class="vise-principle-title">Loss reduces reported realized gain</div>
    <div class="vise-principle-body">Visible in the Realized Gains Ledger and gains summary report.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon">→</div>
  <div>
    <div class="vise-principle-title">No positive cash event on a loss</div>
    <div class="vise-principle-body">Extend TaxEngine.compute_sale_tax for loss carry-forward logic if needed.</div>
  </div>
</div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1.5rem;">How TaxEngine Works in Code</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-classref">
  <div class="vise-classref-title">class TaxEngine · stateless — no memory between calls</div>
  <div class="vise-classref-method"><div class="vise-crm-sig">classify_holding(open_date, close_date)</div><div class="vise-crm-desc">Computes (close_date − open_date).days. Returns ('LT', 0.20) if ≥ 365, else ('ST', 0.35). Called once per lot during sell().</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">compute_sale_tax(gain, gain_type)</div><div class="vise-crm-desc">If gain < 0, adds abs(gain) to the ST or LT carry-forward bucket and returns 0. If gain > 0, nets it against same-type carry-forward first, then cross-type, then taxes the remainder.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">compute_dividend_tax(gross_dividend)</div><div class="vise-crm-desc">Returns (tax_owed, after_tax_div). Simple flat-rate multiply. Called inside Portfolio.process_dividend() before the DRIP reinvestment.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">st_loss_carryforward · lt_loss_carryforward</div><div class="vise-crm-desc">Two float attributes that persist across the full simulation. Reset to 0 on Portfolio construction.</div></div>
</div>
<p style="font-size:0.82rem;color:var(--text-muted);margin-top:0.75rem;line-height:1.6;">
TaxEngine is instantiated inside Portfolio.__init__ and stored as self.tax_eng. You never call it directly — Portfolio.sell() and Portfolio.process_dividend() call it internally on every realization event.
</p>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 3 — Sell Handling & TLH
# ═══════════════════════════════════════════════════════════
with tab3:
    section_sep("03", "Sell Handling")
    section_header(
        "Lot Selection & Tax-Aware Selling",
        "Which shares you sell matters<br>as much as when you sell.",
        "When you hold multiple lots of the same stock at different cost bases, you get to choose which ones to sell. That choice directly changes your tax bill. The engine supports three strategies — set per sell order via <code style='font-family:DM Mono,monospace;font-size:0.82em;background:#1a1f2e;border:1px solid #232a3a;padding:0.1em 0.4em;border-radius:3px;color:#7b8cff;'>lot_selection</code>.",
    )

    st.markdown("""
<div class="vise-tax-grid" style="margin-bottom:2rem;">
  <div class="vise-tax-card" style="border-top:2px solid var(--accent2);">
    <div class="vise-tax-rate" style="font-size:1.3rem;color:var(--accent2);margin-bottom:0.4rem;">FIFO</div>
    <div class="vise-tax-name">First In, First Out</div>
    <div class="vise-tax-desc">Always sells the <strong>oldest lot first</strong>. Simple and predictable. Oldest lots are most likely to be long-term (20% rate), so FIFO tends to favour lower tax rates — but it ignores loss opportunities.</div>
  </div>
  <div class="vise-tax-card" style="border-top:2px solid var(--text-muted);">
    <div class="vise-tax-rate" style="font-size:1.3rem;color:var(--text-muted);margin-bottom:0.4rem;">LIFO</div>
    <div class="vise-tax-name">Last In, First Out</div>
    <div class="vise-tax-desc">Always sells the <strong>newest lot first</strong>. Keeps old low-basis lots alive longer, deferring their eventual gain further into the future.</div>
  </div>
  <div class="vise-tax-card lt">
    <div class="vise-tax-rate" style="font-size:1rem;margin-bottom:0.4rem;">TAX_OPTIMAL</div>
    <div class="vise-tax-name">Tax-Loss Harvesting</div>
    <div class="vise-tax-desc">Sells <strong>loss lots first</strong> (biggest ST loss first, then LT losses), then smallest-gain lots last. Crystallises losses into a carry-forward that offsets future gains.</div>
  </div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="vise-label">The Scenario That Motivated This</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-example-box" style="margin-bottom:2rem;">
  <div style="padding:1.5rem;font-family:'DM Mono',monospace;font-size:0.78rem;color:var(--text-dim);letter-spacing:0.1em;text-transform:uppercase;border-bottom:1px solid var(--border);">You hold two lots. Current price is $150. Which do you sell?</div>
  <div style="padding:1.25rem 1.5rem;">
    <div style="display:flex;gap:1rem;align-items:center;padding:0.6rem 0.75rem;border-radius:4px;background:rgba(79,255,176,0.06);border:1px solid rgba(79,255,176,0.2);margin-bottom:0.5rem;font-family:'DM Mono',monospace;font-size:0.82rem;">
      <span style="color:var(--text-dim);font-size:0.72rem;">LOT001</span>
      <span style="color:var(--text-muted);flex:1;">2 years ago · cost $100</span>
      <span style="color:var(--heading);">+$50 gain / share</span>
      <span style="font-size:0.62rem;padding:0.15rem 0.5rem;border-radius:3px;background:rgba(79,255,176,0.15);color:var(--accent);">LT</span>
      <span style="font-size:0.72rem;color:var(--accent);">tax = $10/sh</span>
    </div>
    <div style="display:flex;gap:1rem;align-items:center;padding:0.6rem 0.75rem;border-radius:4px;background:rgba(255,140,97,0.06);border:1px solid rgba(255,140,97,0.2);font-family:'DM Mono',monospace;font-size:0.82rem;">
      <span style="color:var(--text-dim);font-size:0.72rem;">LOT002</span>
      <span style="color:var(--text-muted);flex:1;">6 months ago · cost $200</span>
      <span style="color:var(--heading);">−$50 loss / share</span>
      <span style="font-size:0.62rem;padding:0.15rem 0.5rem;border-radius:3px;background:rgba(255,140,97,0.15);color:var(--accent3);">ST</span>
      <span style="font-size:0.72rem;color:var(--accent3);">tax = $0/sh</span>
    </div>
  </div>
  <div style="padding:1rem 1.5rem;border-top:1px solid var(--border);font-size:0.82rem;color:var(--text-muted);line-height:1.8;">
    <strong style="color:var(--heading);">FIFO sells LOT001</strong> — realises a $50 LT gain, pays $10/share in tax. You keep the loss lot, but you've paid tax now.<br>
    <strong style="color:var(--accent);">TAX_OPTIMAL sells LOT002</strong> — realises a $50 ST loss, pays $0 tax, and banks that $50 loss as a carry-forward that will reduce tax on the next gain you realise.
  </div>
</div>
""", unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown('<div class="vise-label">Loss Carry-Forward</div>', unsafe_allow_html=True)
        st.markdown("""
<p style="color:var(--text-muted);font-size:0.88rem;margin-bottom:1rem;line-height:1.7;">Harvested losses don't disappear — they go into a carry-forward bucket (separate ST and LT balances) that automatically offsets the next gain before tax is computed.</p>
<div class="vise-code"><span class="cm"># When a loss is realised:</span>
st_loss_carryforward += abs(loss)  <span class="cm"># or lt_</span>

<span class="cm"># When the next gain arrives:</span>
taxable = gain - st_loss_carryforward
<span class="cm"># carryforward is consumed, remainder taxed
# ST losses offset ST gains first (35% rate)
# then spill into LT gains (20% rate)</span></div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="vise-label">Netting Priority</div>', unsafe_allow_html=True)
        st.markdown("""
<div class="vise-principle">
  <div class="vise-principle-icon" style="color:var(--accent3);background:rgba(255,140,97,0.1);border-color:rgba(255,140,97,0.3);">ST</div>
  <div>
    <div class="vise-principle-title">ST losses → ST gains first</div>
    <div class="vise-principle-body">Offsets gains taxed at 35% — highest value use. Excess spills into LT gains.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon" style="color:var(--accent);background:rgba(79,255,176,0.1);border-color:rgba(79,255,176,0.3);">LT</div>
  <div>
    <div class="vise-principle-title">LT losses → LT gains first</div>
    <div class="vise-principle-body">Offsets gains taxed at 20%. Excess spills into ST gains.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon">∞</div>
  <div>
    <div class="vise-principle-title">Carry-forward persists</div>
    <div class="vise-principle-body">Unused losses accumulate across the full simulation. Inspect anytime via pf.tax_eng.st_loss_carryforward.</div>
  </div>
</div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1.5rem;">Cost Basis & Realized Gain Formulas</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
<p style="color:var(--text-muted);font-size:0.88rem;margin-bottom:0.75rem;line-height:1.7;">Cost basis is the all-in acquisition cost — not just the closing price:</p>
<div class="vise-code">exec_price = close_price * (<span class="s">1</span> + slippage_bps / <span class="s">10000</span>)
commission  = shares * commission_per_share

cost_basis = (shares * exec_price + commission) / shares
<span class="cm"># cost_basis > close_price — reduces overstated gains</span></div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown("""
<p style="color:var(--text-muted);font-size:0.88rem;margin-bottom:0.75rem;line-height:1.7;">On sale, proceeds are also net of costs:</p>
<div class="vise-code">sell_exec    = close_price * (<span class="s">1</span> - slippage_bps / <span class="s">10000</span>)
net_proceeds = shares * sell_exec - commission

gain = net_proceeds - (shares * cost_basis)
tax  = <span class="fn">compute_sale_tax</span>(gain, gain_type)
<span class="cm"># loss? → added to carry-forward, tax = 0</span></div>
        """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 4 — Dividends & Cash Flows
# ═══════════════════════════════════════════════════════════
with tab4:
    section_sep("05", "Dividend Handling")
    section_header(
        "DRIP Sequence",
        "Dividends trigger on PAYDATE.<br>Not before. Not differently.",
        "Three mistakes plague most dividend models: reinvesting on ex-date, injecting dividends into the return stream, and adjusting prices. This engine avoids all three. Dividends are cash events that create new trades — nothing else.",
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
<div class="vise-steps">
  <div class="vise-step"><div class="vise-step-num">1</div><div><div class="vise-step-title">Count shares held</div><div class="vise-step-detail">Sum open lot shares for this security as of PAYDATE. This is the entitled count.</div></div></div>
  <div class="vise-step"><div class="vise-step-num">2</div><div><div class="vise-step-title">Compute gross dividend</div><div class="vise-step-detail">shares_held × DIVAMOUNT</div></div></div>
  <div class="vise-step"><div class="vise-step-num">3</div><div><div class="vise-step-title">Apply dividend tax</div><div class="vise-step-detail">tax = gross_div × 0.15<br>after_tax = gross_div − tax</div></div></div>
  <div class="vise-step"><div class="vise-step-num">4</div><div><div class="vise-step-title">Log realization event</div><div class="vise-step-detail">Written to Realized Gains Ledger as a DIVIDEND event with full detail.</div></div></div>
  <div class="vise-step"><div class="vise-step-num">5</div><div><div class="vise-step-title">Deduct tax from cash</div><div class="vise-step-detail">+gross_div added to cash, then −tax deducted. Net: +after_tax_div in cash.</div></div></div>
  <div class="vise-step"><div class="vise-step-num">6</div><div><div class="vise-step-title">Execute DRIP buy</div><div class="vise-step-detail">drip_shares = after_tax_div ÷ reinvest_price<br>Goes through full cost engine. <strong>Creates a new lot</strong> with today as open_date.</div></div></div>
</div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="vise-label">Why DRIP Creates a New Lot</div>', unsafe_allow_html=True)
        st.markdown("""
<p style="color:var(--text-muted);font-size:0.88rem;margin-bottom:1.25rem;line-height:1.7;">The reinvestment does not add shares to an existing lot. It opens a brand new one — with today's price as cost basis and today as the open date.</p>
<div class="vise-principle">
  <div class="vise-principle-icon">→</div>
  <div>
    <div class="vise-principle-title">Different cost basis</div>
    <div class="vise-principle-body">DRIP shares cost today's price, not the original purchase price. Gains are computed correctly when sold.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon">→</div>
  <div>
    <div class="vise-principle-title">Different holding period clock</div>
    <div class="vise-principle-body">The 365-day LT threshold starts from the DRIP date, independent of the original position.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon">→</div>
  <div>
    <div class="vise-principle-title">Quarterly dividends spawn many lots</div>
    <div class="vise-principle-body">Over a multi-year hold, a single original buy can produce 8–12 DRIP lots. Each tracked separately.</div>
  </div>
</div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1.5rem;">How Dividends Work in Code</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-classref">
  <div class="vise-classref-title">Portfolio.process_dividend(pay_date, tradingitemid, tickersymbol, div_amount_per_share, reinvest_price)</div>
  <div class="vise-classref-method"><div class="vise-crm-sig">Step 1–2: _shares_held + gross div</div><div class="vise-crm-desc">_shares_held(tradingitemid) sums open lot shares from _lots_idx. If 0, silently returns. Gross = shares × div_amount_per_share.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">Step 3–4: TaxEngine + realized ledger</div><div class="vise-crm-desc">Calls tax_eng.compute_dividend_tax(gross_div) → (tax_owed, after_tax_div). Logs a DIVIDEND event to realized_ledger.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">Step 5–6: Cash + DRIP buy</div><div class="vise-crm-desc">Adds gross to cash, deducts tax. Then calls buy(source='DRIP') with after_tax_div ÷ reinvest_price shares. This opens a new lot.</div></div>
</div>
<div class="vise-classref" style="border-left-color:var(--accent3);margin-top:0.75rem;">
  <div class="vise-classref-title" style="color:var(--accent3);">DailyValuationEngine — dividend triggering</div>
  <div class="vise-classref-method"><div class="vise-crm-sig">self._div_by_date</div><div class="vise-crm-desc">A dict built once in __init__: {PAYDATE → [list of dividend row dicts]}. The daily loop calls _get_dividends_on(day) which is a pure .get() — O(1) per day.</div></div>
  <div class="vise-classref-method"><div class="vise-crm-sig">Dividend data source</div><div class="vise-crm-desc">Must contain TRADINGITEMID, PAYDATE, DIVAMOUNT. Only securities in TRADING_IDS are kept after loading. The engine checks PAYDATE — not EXDATE.</div></div>
</div>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 5 — Valuation & Performance
# ═══════════════════════════════════════════════════════════
with tab5:
    section_sep("06", "Daily Valuation")
    section_header(
        "The Daily Loop",
        "Four steps. Every business day.<br>No look-ahead, ever.",
        "The Daily Valuation Engine iterates through every business day. Each day executes in a fixed sequence — the order matters because DRIP shares must be visible in the same day's valuation.",
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
<div class="vise-principle">
  <div class="vise-principle-icon" style="font-size:0.65rem;font-weight:500;">01</div>
  <div>
    <div class="vise-principle-title">Build price lookup</div>
    <div class="vise-principle-body">Filter prices to PRICEDATE ≤ today, take last per security. Future prices are invisible.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon" style="font-size:0.65rem;font-weight:500;">02</div>
  <div>
    <div class="vise-principle-title">Process dividends</div>
    <div class="vise-principle-body">Check for PAYDATE == today. Execute the full 6-step DRIP sequence for each match.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon" style="font-size:0.65rem;font-weight:500;">03</div>
  <div>
    <div class="vise-principle-title">Execute scheduled trades</div>
    <div class="vise-principle-body">Check the user-provided trade schedule for orders dated today. Buys and sells routed through the full accounting pipeline.</div>
  </div>
</div>
<div class="vise-principle">
  <div class="vise-principle-icon" style="font-size:0.65rem;font-weight:500;">04</div>
  <div>
    <div class="vise-principle-title">Record NAV snapshot</div>
    <div class="vise-principle-body">Compute market value + cash, unrealized gain, realized YTD, taxes YTD, and daily return.</div>
  </div>
</div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown("""
<div class="vise-formula-visual">
  <div class="vise-formula-big">
    <em>Portfolio Value</em><br>= <span>(Shares × Price)</span> + Cash
  </div>
  <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:0.5rem;">Recomputed from first principles every day. Never carries forward yesterday's value.</div>
</div>
<div style="padding:1rem 1.25rem;background:var(--surface);border:1px solid rgba(79,255,176,0.2);border-radius:6px;">
  <div class="vise-label" style="margin-bottom:0.4rem;">Daily Return Is Already After-Tax</div>
  <p style="font-size:0.84rem;color:var(--text-muted);line-height:1.65;">Because taxes hit cash at the moment of realization, portfolio value on Day N already reflects that reduction. No separate after-tax adjustment needed. The NAV time series <em>is</em> the after-tax time series.</p>
</div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1.5rem;">Edge Cases & Guardrails</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-guardrails">
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Sell > shares held</div><div class="vise-guardrail-response">Order <strong>clamped to available shares</strong>. Warning emitted. No crash.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Insufficient cash for buy</div><div class="vise-guardrail-response">Trade <strong>skipped with warning</strong>. Cash cannot go negative.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Transaction costs > proceeds</div><div class="vise-guardrail-response">Sell <strong>aborted with warning</strong>. No negative cash event created.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">No price on dividend pay date</div><div class="vise-guardrail-response">Uses <strong>last available close</strong>. Weekend/holiday paydates handled automatically.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Zero shares at dividend time</div><div class="vise-guardrail-response">Dividend <strong>silently skipped</strong>. Position was already sold.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Sell entire position</div><div class="vise-guardrail-response">Pass shares=1e9. <strong>Disposal loop exhausts all lots</strong> and stops naturally.</div></div>
</div>
""", unsafe_allow_html=True)

    section_sep("07", "Reporting")
    section_header(
        "Outputs",
        "Five views of the truth.<br>All traceable to the ledger.",
        "The Portfolio Reporter is read-only — it never modifies state. Every number it reports can be reproduced by querying the underlying ledgers directly.",
    )

    st.markdown("""
<div class="vise-outputs">
  <div class="vise-output-item"><div class="vise-output-name">NAV Summary</div><div class="vise-output-desc">Full daily time series with portfolio value, cash, market value, daily return, and cumulative return. One row per trading day.</div></div>
  <div class="vise-output-item"><div class="vise-output-name">After-Tax Return</div><div class="vise-output-desc">A single percentage: (final_value ÷ initial_value) − 1. Already net of all taxes and costs.</div></div>
  <div class="vise-output-item"><div class="vise-output-name">Tax Summary</div><div class="vise-output-desc">Total taxes paid broken down by category: short-term gains, long-term gains, and dividend tax.</div></div>
  <div class="vise-output-item"><div class="vise-output-name">Gains Summary</div><div class="vise-output-desc">Total realized gain/loss, current unrealized gain, total taxes paid, and net after-tax realized gain.</div></div>
  <div class="vise-output-item"><div class="vise-output-name">Turnover Stats</div><div class="vise-output-desc">Buy count, sell count, DRIP count, total buy and sell volumes, and total commissions paid.</div></div>
</div>
""", unsafe_allow_html=True)

    section_sep("08", "Configuration")
    section_header(
        "Two Dictionaries",
        "All levers in one place.<br>Change once, flows everywhere.",
        "Every configurable parameter lives at the top of the notebook. There is no hunting through class constructors or method signatures.",
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
<div class="vise-config-block">
  <div class="vise-config-header">TAX_CONFIG</div>
  <div class="vise-config-row"><div class="vise-config-key">short_term_rate</div><div class="vise-config-val">0.35</div><div class="vise-config-desc">Gains from positions held < 365 days</div></div>
  <div class="vise-config-row"><div class="vise-config-key">long_term_rate</div><div class="vise-config-val">0.20</div><div class="vise-config-desc">Gains from positions held ≥ 365 days</div></div>
  <div class="vise-config-row"><div class="vise-config-key">dividend_rate</div><div class="vise-config-val">0.15</div><div class="vise-config-desc">Tax on gross dividend before reinvestment</div></div>
  <div class="vise-config-row"><div class="vise-config-key">lt_holding_days</div><div class="vise-config-val">365</div><div class="vise-config-desc">Day threshold between ST and LT</div></div>
</div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown("""
<div class="vise-config-block">
  <div class="vise-config-header">COST_CONFIG</div>
  <div class="vise-config-row"><div class="vise-config-key">commission_per_share</div><div class="vise-config-val">$0.005</div><div class="vise-config-desc">Flat dollar fee per share, every trade</div></div>
  <div class="vise-config-row"><div class="vise-config-key">slippage_bps</div><div class="vise-config-val">5 bps</div><div class="vise-config-desc">Adverse price movement at execution</div></div>
</div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="vise-label" style="margin-top:1rem;">Common Configurations</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="vise-guardrails">
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Tax-deferred account (IRA / 401k)</div><div class="vise-guardrail-response">Set short_term_rate, long_term_rate, and dividend_rate all to <strong>0</strong>.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Commission-free brokerage</div><div class="vise-guardrail-response">Set commission_per_share to <strong>0</strong>. Keep slippage.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Everything long-term</div><div class="vise-guardrail-response">Set lt_holding_days to <strong>0</strong>. All gains taxed at 20%.</div></div>
  <div class="vise-guardrail"><div class="vise-guardrail-trigger">Institutional slippage</div><div class="vise-guardrail-response">Increase slippage_bps to <strong>10–20</strong> for large-order realism.</div></div>
</div>
""", unsafe_allow_html=True)

    section_sep("10", "Performance")
    section_header(
        "Optimizations",
        "Built for large price files<br>and long simulations.",
        "Four targeted fixes eliminate the bottlenecks that make the engine slow at scale — particularly with a large prices CSV, 20–50 securities, and a 10+ year simulation window.",
    )

    st.markdown("""
<div class="vise-card-grid">
  <div class="vise-card">
    <div class="vise-card-num">01</div>
    <div class="vise-card-title">Security Filter</div>
    <div><span class="vise-card-tag tag-stateless">Data Loading</span></div>
    <p>Set TRADING_IDS to only the securities you trade. A 3M-row file covering 5,000 stocks cut to 50 securities drops to ~30,000 rows — <strong>99% smaller</strong>.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">02</div>
    <div class="vise-card-title">Pre-Built Price Table</div>
    <div><span class="vise-card-tag tag-orchestrator">Daily Loop</span></div>
    <p>Forward-filled pivot table built once in __init__. Each day's lookup is a single <strong>O(1) .loc[] row access</strong>. Over 2,500 days that's 2,500 expensive operations → 2,500 dict lookups.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">03</div>
    <div class="vise-card-title">List-Based Ledger Buffers</div>
    <div><span class="vise-card-tag tag-stateful">Portfolio</span></div>
    <p>Six ledgers previously used pd.concat row-by-row. Now accumulates rows as plain dicts in a list, materializes to DataFrame once. Eliminates the <strong>O(n²) memory pattern</strong>.</p>
  </div>
  <div class="vise-card">
    <div class="vise-card-num">04</div>
    <div class="vise-card-title">Lot Index + Reverse Map</div>
    <div><span class="vise-card-tag tag-stateful">Portfolio</span></div>
    <p>_lots_idx (security → lot positions) for O(k) lookup, and _lot_id_to_buf for O(1) sell updates. Critical for 50 securities with quarterly dividends over 10 years.</p>
  </div>
</div>
""", unsafe_allow_html=True)

render_footer()
