"""Supplier quotes for a product: landed cost, comparison, and using one."""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import page_header, section, usd  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.costs import get_cost, latest_products  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.fees import load_fee_tables  # noqa: E402
from vendorus.profit import load_planning  # noqa: E402
from vendorus.scoring import load_scoring  # noqa: E402
from vendorus.sourcing import (  # noqa: E402
    apply_quote,
    compare_quotes,
    delete_quote,
    list_suppliers,
    load_sourcing_config,
    price_breaks,
    quote_label,
    save_quote,
)

page_header(
    "Quotes",
    "Turn each supplier quote into a landed cost for the quantity you plan to order, compare them, "
    "and use the best one for this product's costs.",
    eyebrow="Sourcing",
)

engine = get_engine(load_settings())
cfg = load_sourcing_config()
products = latest_products(engine)
suppliers = list_suppliers(engine)
if not products:
    with st.container(border=True):
        st.markdown(":material/inbox: No products yet. Import research data first.")
        st.page_link("views/import_data.py", label="Open import", icon=":material/upload_file:")
    st.stop()

labels = {f"{p.asin} · {(p.title or 'untitled')[:70]}": p.asin for p in products}
keys = list(labels)
requested = st.query_params.get("asin")
index = next((i for i, k in enumerate(keys) if labels[k] == requested), 0)
pick, link = st.columns([4, 1.3], vertical_alignment="bottom")
asin = labels[pick.selectbox("Product", keys, index=index)]
st.query_params["asin"] = asin
link.page_link("views/profit.py", label="Costs and profit", icon=":material/payments:", query_params={"asin": asin})

if not suppliers:
    with st.container(border=True):
        st.markdown(":material/storefront: Add a supplier first, then come back to enter their quote.")
        st.page_link("views/suppliers.py", label="Add a supplier", icon=":material/add_business:")
    st.stop()

cost = get_cost(engine, asin)
if not (cost and cost.fee_category):
    st.info("Choose a referral fee category (and packaged size) on the Profit page to see each quote's "
            "profit and score.", icon=":material/info:")


# --- the quote form ---------------------------------------------------------------
def quote_form(key: str, quote=None) -> dict | None:
    def val(name, default=None):
        value = getattr(quote, name, None) if quote is not None else None
        return default if value is None else value

    supplier_names = {f"{s.name} ({s.country or '?'})": s.id for s, _ in suppliers}
    supplier_keys = list(supplier_names)
    current = next((k for k, sid in supplier_names.items() if sid == val("supplier_id")), supplier_keys[0])
    incoterms = list(cfg["incoterms"])
    with st.form(key, clear_on_submit=quote is None):
        st.markdown(":material/storefront: **Supplier and price**")
        a, b, c = st.columns(3)
        supplier = a.selectbox("Supplier", supplier_keys, index=supplier_keys.index(current))
        currency = b.selectbox("Currency", cfg["currencies"],
                               index=cfg["currencies"].index(val("currency", "USD"))
                               if val("currency", "USD") in cfg["currencies"] else 0)
        rate = c.number_input("Exchange rate (USD per 1 unit)", min_value=0.0001, value=float(val("exchange_rate", 1.0)),
                              format="%.4f", help="e.g. 0.14 for CNY. Ignored for USD. Enter today's rate yourself.")
        d, e, f = st.columns(3)
        unit_price = d.number_input("Unit price (quote currency)", min_value=0.0, value=float(val("unit_price", 0.0)),
                                    step=0.1, format="%.2f")
        moq = e.number_input("Minimum order (MOQ)", min_value=0, value=int(val("moq", 0)), step=50)
        order_qty = f.number_input("Your order quantity", min_value=1, value=int(val("order_qty", val("moq", 500)) or 500),
                                   step=50, help="Landed cost is worked out for this quantity.")
        breaks = st.data_editor(
            pd.DataFrame([{"From quantity": q, "Unit price": float(p)} for q, p in price_breaks(quote)]
                         if quote else [], columns=["From quantity", "Unit price"]),
            num_rows="dynamic", hide_index=True, width="stretch", key=f"{key}-breaks",
            column_config={"From quantity": st.column_config.NumberColumn(min_value=1, step=1, format="%d"),
                           "Unit price": st.column_config.NumberColumn(min_value=0.0, format="%.2f")},
        )
        st.caption("Optional price breaks, e.g. 1,000+ units at a lower price.")

        st.markdown(":material/description: **Terms and one-off costs**")
        g, h, i = st.columns(3)
        incoterm = g.selectbox("Incoterm", incoterms, index=incoterms.index(val("incoterm", "FOB")),
                               format_func=lambda t: t, help="\n\n".join(f"**{k}**: {v['name']}" for k, v in cfg["incoterms"].items()))
        payment = h.text_input("Payment terms", value=val("payment_terms", ""), placeholder="e.g. 30% deposit")
        valid_until = i.date_input("Valid until", value=val("valid_until"), format="YYYY-MM-DD")
        j, k, l = st.columns(3)
        packaging = j.number_input("Packaging and labels per unit (quote currency)", min_value=0.0,
                                   value=float(val("packaging_per_unit", 0.0)), format="%.2f")
        tooling = k.number_input("Tooling or molds, one-off (quote currency)", min_value=0.0,
                                 value=float(val("tooling_cost", 0.0)), format="%.2f")
        samples = l.number_input("Samples (quote currency)", min_value=0.0, value=float(val("sample_cost", 0.0)),
                                 format="%.2f")

        st.markdown(":material/local_shipping: **Shipping and import (US dollars)**")
        m, n, o = st.columns(3)
        method = m.selectbox("Shipping method", cfg["shipping_methods"],
                             index=cfg["shipping_methods"].index(val("shipping_method", "Sea"))
                             if val("shipping_method", "Sea") in cfg["shipping_methods"] else 0)
        modes = {"per_shipment": "Whole shipment", "per_unit": "Per unit"}
        mode = n.selectbox("Freight is quoted", list(modes), format_func=modes.get,
                           index=list(modes).index(val("freight_mode", "per_shipment")))
        freight = o.number_input("Freight ($)", min_value=0.0, value=float(val("freight_amount", 0.0)), format="%.2f")
        p, q, r = st.columns(3)
        duty = p.number_input("Duty (% of unit price)", min_value=0.0, max_value=200.0,
                              value=float(val("duty_pct", 0.0)), step=0.5, format="%.2f",
                              help=f"Look up your product's tariff code: {cfg['tariff_lookup_url']}. "
                                   "Rates change often; Vendorus never guesses one.")
        broker = q.number_input("Customs broker ($ per order)", min_value=0.0, value=float(val("broker_fee", 0.0)))
        inspection = r.number_input("Inspection ($ per order)", min_value=0.0, value=float(val("inspection_fee", 0.0)))
        s_, t, u = st.columns(3)
        other = s_.number_input("Other fees ($ per order)", min_value=0.0, value=float(val("other_fees", 0.0)))
        production = t.number_input("Production days", min_value=0, value=int(val("production_days", 0)))
        transit = u.number_input("Transit days", min_value=0, value=int(val("transit_days", 0)))
        notes = st.text_area("Notes", value=val("notes", ""), placeholder="Material, finish, certifications…")
        received = val("received_on", date.today())
        if st.form_submit_button("Save changes" if quote else "Add quote", type="primary", icon=":material/save:"):
            if unit_price <= 0:
                st.error("Enter the unit price.")
                return None
            rows = [(int(r["From quantity"]), float(r["Unit price"])) for _, r in breaks.dropna().iterrows()]
            return dict(
                supplier_id=supplier_names[supplier], asin=asin, received_on=received, valid_until=valid_until,
                currency=currency, exchange_rate=1.0 if currency == "USD" else rate, unit_price=unit_price,
                moq=moq or None, price_breaks=rows, order_qty=order_qty, incoterm=incoterm, payment_terms=payment,
                packaging_per_unit=packaging, tooling_cost=tooling, sample_cost=samples, shipping_method=method,
                freight_mode=mode, freight_amount=freight, transit_days=transit or None, duty_pct=duty,
                broker_fee=broker, inspection_fee=inspection, other_fees=other,
                production_days=production or None, notes=notes,
            )
    return None


results = compare_quotes(engine, asin, load_fee_tables(), load_planning(), load_scoring(), cfg)

with st.expander("Add a quote", icon=":material/request_quote:", expanded=not results):
    values = quote_form("new-quote")
    if values:
        save_quote(engine, **values)
        st.toast("Quote added", icon=":material/check_circle:")
        st.rerun()

if not results:
    st.stop()

# --- comparison ------------------------------------------------------------------------
section("Compare quotes", "Cheapest landed cost first. Profit and score assume no advertising. Click a row for details.")
frame = pd.DataFrame(
    {
        "In use": "✓" if r.in_use else "",
        "Quote": quote_label(r.quote),
        "Landed/unit": float(r.landed.per_unit),
        "Profit/unit": None if r.profit is None else float(r.profit),
        "Margin %": None if r.margin_pct is None else float(r.margin_pct),
        "Break-even": None if r.break_even is None else float(r.break_even),
        "Score": r.score,
        "Cash for order": float(r.landed.cash_needed),
        "MOQ": r.quote.moq,
        "Days to Amazon": r.landed.days_to_amazon,
        "Warnings": len(r.landed.warnings),
    }
    for r in results
)
MONEY = st.column_config.NumberColumn(format="$%.2f")
event = st.dataframe(
    frame, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="quotes_table",
    column_config={
        "In use": st.column_config.TextColumn(width="small"),
        "Quote": st.column_config.TextColumn(width="medium"),
        "Landed/unit": MONEY, "Profit/unit": MONEY, "Break-even": MONEY,
        "Cash for order": st.column_config.NumberColumn(format="$%,.0f"),
        "Margin %": st.column_config.NumberColumn(format="%.1f%%"),
        "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
    },
)
rows = event.selection.rows if event and event.selection else []
chosen = results[rows[0]] if rows else next((r for r in results if r.in_use), results[0])

# --- one quote ---------------------------------------------------------------------------
section(quote_label(chosen.quote))
left, right = st.columns([3, 2], gap="medium")
with left:
    landed = chosen.landed
    breakdown = "\n".join(f"| {label} | ${amount:,.2f} |" for label, amount in landed.lines)
    st.markdown(f"| Per unit | |\n|---|---:|\n{breakdown}\n| **Landed cost** | **${landed.per_unit:,.2f}** |")
    quote = chosen.quote
    rate_note = "" if quote.currency == "USD" else f" ({quote.currency} at {quote.exchange_rate:g} USD)"
    st.caption(
        f"For {landed.quantity:,} units at {landed.unit_price:,.2f} {quote.currency}/unit{rate_note} · "
        f"{quote.incoterm} · {quote.shipping_method or 'shipping not set'}"
        + (f" · payment: {quote.payment_terms}" if quote.payment_terms else "")
    )
with right:
    st.metric("Order cash", f"${landed.cash_needed:,.0f}", border=True,
              help="Stock, tooling and fees for the whole order, plus samples")
    st.metric("Days to Amazon", landed.days_to_amazon if landed.days_to_amazon is not None else "—",
              border=True, help=f"Production + transit + {cfg['receiving_buffer_days']} days for Amazon to receive it")
    st.metric("Profit/unit (organic)", usd(chosen.profit), border=True)
for warning in landed.warnings:
    st.warning(warning, icon=":material/warning:")

if chosen.in_use:
    st.success("This quote's costs are in use for this product.", icon=":material/check_circle:")
elif st.button("Use this quote for this product", type="primary", icon=":material/check:"):
    apply_quote(engine, quote.id, cfg)
    st.toast("Costs updated from this quote", icon=":material/check_circle:")
    st.rerun()

with st.expander("Edit this quote", icon=":material/edit:"):
    values = quote_form(f"edit-quote-{quote.id}", quote)
    if values:
        save_quote(engine, quote.id, **values)
        if chosen.in_use:
            apply_quote(engine, quote.id, cfg)  # keep the product's costs in step
        st.toast("Quote saved", icon=":material/check_circle:")
        st.rerun()

with st.expander("Delete this quote", icon=":material/delete:"):
    st.caption("If this quote is in use, the product keeps its copied costs. This can't be undone.")
    if st.checkbox("I understand", key=f"confirm-del-quote-{quote.id}"):
        if st.button("Delete quote", icon=":material/delete:", key=f"del-quote-{quote.id}"):
            delete_quote(engine, quote.id)
            st.toast("Quote deleted")
            st.rerun()
