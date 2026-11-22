"""Profit per unit for one product: Amazon fees, landed cost, margins."""
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import page_header, section, usd  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.costs import get_cost, latest_products, save_cost, suggest_fee_category  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.fees import load_fee_tables  # noqa: E402
from vendorus.profit import (  # noqa: E402
    NOT_INCLUDED,
    ORGANIC_NOTICE,
    ProductInputs,
    analyze,
    load_planning,
)

page_header(
    "Profit calculator",
    "Amazon fees, landed cost and margin per unit. Organic-first: no advertising unless you add it.",
    eyebrow="Research",
)

tables = load_fee_tables()
planning = load_planning()
engine = get_engine(load_settings())
today = date.today()

st.warning(ORGANIC_NOTICE, icon=":material/trending_flat:")
if tables.is_out_of_date(today):
    st.error(
        "The Amazon fee table has expired. Update vendorus/fees/us.toml from Amazon's fee pages.",
        icon=":material/error:",
    )
elif not tables.reviewed:
    st.badge(
        f"Fees copied from Amazon {tables.retrieved:%b %d, %Y} · not yet reviewed",
        icon=":material/rule:", color="orange",
        help="Sources are listed at the bottom of this page. Set reviewed = true in us.toml once checked.",
    )


def dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(round(float(value), 4)))


def first(*values):
    return next((v for v in values if v is not None), None)


# --- choose a product ------------------------------------------------------
products = latest_products(engine)
by_label = {"Enter a product by hand": None}
for p in products:
    by_label[f"{p.asin} · {(p.title or 'untitled')[:70]}"] = p

st.write("")
left, right = st.columns([3, 2])
choice = left.selectbox("Product", list(by_label))
product = by_label[choice]
asin = product.asin if product else None
saved = get_cost(engine, asin) if asin else None

periods = list(tables.periods)
default_period = tables.default_period(today)
period = right.selectbox(
    "Amazon fee period",
    periods,
    index=periods.index(default_period),
    format_func=lambda p: p.label + (" (in force today)" if p.covers(today) else ""),
    help="Defaults to everyday (non-peak) fees in force now or starting next. "
    "Holiday peak fees are higher.",
)

key = asin or "manual"
categories = tables.categories
suggested = suggest_fee_category(tables, product.category if product else None)
saved_category = saved.fee_category if saved else None
category_default = first(saved_category, suggested)

# --- inputs ----------------------------------------------------------------
with st.form(f"inputs-{key}"):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(":material/sell: **Price and category**")
        price = st.number_input(
            "Selling price ($)", min_value=0.01, step=0.50, key=f"{key}-price",
            value=float(first(saved and saved.planned_price, product and product.price, 19.99)),
        )
        category = st.selectbox(
            "Referral fee category", categories, key=f"{key}-cat",
            index=categories.index(category_default) if category_default in categories else None,
            placeholder="Choose a category",
        )
        if suggested and not saved_category:
            st.caption(f"Suggested from the store category “{product.category}”. Please confirm.")
        apparel = st.checkbox("Apparel", value=bool(saved and saved.apparel), key=f"{key}-apparel")
        dangerous = st.checkbox(
            "Dangerous goods (hazmat)", value=bool(saved and saved.dangerous_goods), key=f"{key}-dg"
        )
    with c2:
        st.markdown(":material/package_2: **Packaged size and weight**")
        dims = {}
        for name, label in (("length_in", "Length (in)"), ("width_in", "Width (in)"),
                            ("height_in", "Height (in)"), ("weight_lb", "Weight (lb)")):
            dims[name] = st.number_input(
                label, min_value=0.0, step=0.1, format="%.2f", key=f"{key}-{name}",
                value=first(saved and getattr(saved, name), product and getattr(product, name)),
                placeholder="needed for FBA fees",
            )
    with c3:
        st.markdown(":material/local_shipping: **Landed cost per unit**")
        unit_cost = st.number_input(
            "Unit cost from supplier ($)", min_value=0.0, step=0.10, key=f"{key}-unit",
            value=saved.unit_cost if saved else None, placeholder="needed for profit",
        )
        freight = st.number_input("Freight to Amazon ($)", min_value=0.0, step=0.10, key=f"{key}-freight",
                                  value=float(first(saved and saved.freight_per_unit, 0.0)))
        duty = st.number_input("Duty (% of unit cost)", min_value=0.0, step=0.5, key=f"{key}-duty",
                               value=float(first(saved and saved.duty_pct, 0.0)))
        prep = st.number_input("Prep and packaging ($)", min_value=0.0, step=0.05, key=f"{key}-prep",
                               value=float(first(saved and saved.prep_per_unit, 0.0)))
        other = st.number_input("Other per unit ($)", min_value=0.0, step=0.05, key=f"{key}-other",
                                value=float(first(saved and saved.other_per_unit, 0.0)))

    st.divider()
    st.markdown(":material/tune: **Planning assumptions**")
    a1, a2, a3, a4 = st.columns(4)
    returns_pct = a1.number_input(
        "Returns (% of units)", min_value=0.0, max_value=100.0, step=0.5, key=f"{key}-returns",
        value=float(first(saved and saved.returns_pct, planning.returns_pct)),
        help="Each return is costed as lost: product, fulfillment fee and refund admin fee.",
    )
    storage_months = a2.number_input(
        "Months in storage", min_value=0.0, step=0.5, key=f"{key}-storage",
        value=float(first(saved and saved.storage_months, planning.storage_months)),
        help="Average months a unit waits in FBA before it sells.",
    )
    planned_units = a3.number_input(
        "Planned units per month", min_value=0, step=10, key=f"{key}-units",
        value=saved.planned_units_per_month if saved else None, placeholder="optional",
        help="Your own plan. Not a forecast; leave blank to skip monthly figures.",
    )
    with a4:
        ads_enabled = st.checkbox(
            "Ad scenario", key=f"{key}-ads",
            value=bool(saved and saved.ads_enabled),
            help="Off by default (organic-first). Shows profit with and without ads.",
        )
        ad_pct = st.number_input(
            "Ad spend (% of price)", min_value=0.0, max_value=100.0, step=1.0, key=f"{key}-adpct",
            value=float(first(saved and saved.ad_spend_pct, planning.ad_spend_pct)),
        )

    submitted = st.form_submit_button(
        "Calculate and save" if asin else "Calculate", type="primary", icon=":material/calculate:"
    )

if submitted and asin:
    save_cost(
        engine, asin,
        fee_category=category, planned_price=price, unit_cost=unit_cost, freight_per_unit=freight,
        duty_pct=duty, prep_per_unit=prep, other_per_unit=other, apparel=apparel,
        dangerous_goods=dangerous, returns_pct=returns_pct, storage_months=storage_months,
        ads_enabled=ads_enabled, ad_spend_pct=ad_pct, planned_units_per_month=planned_units,
        **dims,
    )
    st.toast(f"Saved costs for {asin}", icon=":material/check_circle:")

# --- results ---------------------------------------------------------------
inputs = ProductInputs(
    price=dec(price), fee_category=category,
    length_in=dec(dims["length_in"]), width_in=dec(dims["width_in"]),
    height_in=dec(dims["height_in"]), weight_lb=dec(dims["weight_lb"]),
    unit_cost=dec(unit_cost), freight_per_unit=dec(freight), duty_pct=dec(duty),
    prep_per_unit=dec(prep), other_per_unit=dec(other), apparel=apparel, dangerous_goods=dangerous,
)
assumptions = replace(
    planning, returns_pct=dec(returns_pct), storage_months=dec(storage_months), ad_spend_pct=dec(ad_pct)
)
result = analyze(tables, period, inputs, assumptions, ads_enabled=ads_enabled)

st.write("")
section("Result")
for problem in result.missing:
    st.error(problem, icon=":material/error:")
for warning in result.fees.warnings:
    st.warning(warning, icon=":material/warning:")

scenarios = [result.organic] + ([result.with_ads] if result.with_ads else [])
break_evens = [result.break_even_organic, result.break_even_with_ads]
cols = st.columns(len(scenarios))
for col, s in zip(cols, scenarios):
    with col:
        st.metric(f"{s.name}: profit per unit", usd(s.profit), border=True)
        if s.profit is not None and s.profit < 0:
            st.error("Loses money on every unit at this price.", icon=":material/trending_down:")


def pct(value) -> str:
    return "—" if value is None else f"{value}%"


summary = pd.DataFrame(
    {
        s.name: {
            "Margin": pct(s.margin_pct),
            "Return on landed cost": pct(s.roi_pct),
            "Break-even price": usd(be) if s.profit is not None else "—",
        }
        for s, be in zip(scenarios, break_evens)
    }
)
st.dataframe(summary, width="stretch")

if not ads_enabled:
    st.caption("No advertising assumed. Turn on the ad scenario above to compare.")

rows = {}
for s in scenarios:
    for label, amount in s.lines:
        rows.setdefault(label, {})[s.name] = usd(amount)
    rows.setdefault("Profit per unit", {})[s.name] = usd(s.profit)
breakdown = pd.DataFrame.from_dict(rows, orient="index")
breakdown.loc["Selling price"] = usd(inputs.price)
breakdown = breakdown.reindex(["Selling price"] + [i for i in breakdown.index if i != "Selling price"])
section("Where each dollar goes")
st.dataframe(breakdown.fillna("—"), width="stretch")

fees = result.fees
if fees.size_tier:
    st.caption(
        f"Size tier: {fees.size_tier} · charged on {fees.shipping_weight_lb.quantize(Decimal('0.01'))} lb · "
        f"fees from: {period.label}"
    )

if planned_units and result.organic.profit is not None:
    section("At your planned volume")
    st.caption(f"{planned_units} units per month is your plan, not a forecast. Sales are not guaranteed.")
    mcols = st.columns(len(scenarios))
    for col, s in zip(mcols, scenarios):
        col.metric(f"{s.name}: profit per month", usd(s.profit * planned_units), border=True)
    st.caption(f"Before the ${planning.professional_plan_monthly}/month Professional plan (account level).")

if product and product.monthly_sales:
    st.info(
        icon=":material/insights:",
        body=f"Research estimate for this ASIN: about {product.monthly_sales:,} sales a month. That is an "
        "established listing's estimate. A new listing starts with no reviews or ranking and will not "
        "start at that level."
    )

with st.expander("Not included in these figures", icon=":material/block:"):
    st.markdown("\n".join(f"- {item}" for item in NOT_INCLUDED))

with st.expander("Fee sources", icon=":material/link:"):
    st.markdown(
        f"Copied on {tables.retrieved:%B %d, %Y} from Amazon. "
        + ("Reviewed." if tables.reviewed else "Not yet marked as reviewed.")
    )
    st.markdown("\n".join(f"- {name.replace('_', ' ')}: {url}" for name, url in tables.sources.items()))
