"""Opportunity score for one product, with the full breakdown."""
import sys
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import page_header, section  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.costs import latest_products  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.fees import load_fee_tables  # noqa: E402
from vendorus.profit import ORGANIC_NOTICE, load_planning  # noqa: E402
from vendorus.scoring import load_scoring, score_product  # noqa: E402

page_header(
    "Opportunity score",
    "A 0–100 score from ten factors, each traced to your research data or the profit engine. "
    "Missing data is left out, never guessed.",
    eyebrow="Research",
)

engine = get_engine(load_settings())
tables = load_fee_tables()
planning = load_planning()
config = load_scoring()

products = latest_products(engine)
if not products:
    with st.container(border=True):
        st.markdown(":material/inbox: No products yet. Import research data to score it.")
        st.page_link("views/import_data.py", label="Open import", icon=":material/upload_file:")
    st.stop()

labels = {f"{p.asin} · {(p.title or 'untitled')[:70]}": p.asin for p in products}
keys = list(labels)
requested = st.query_params.get("asin")
index = next((i for i, k in enumerate(keys) if labels[k] == requested), 0)
top, link = st.columns([4, 1.4], vertical_alignment="bottom")
asin = labels[top.selectbox("Product", keys, index=index)]
st.query_params["asin"] = asin  # the address opens this product again
link.page_link("views/profit.py", label="Costs and profit", icon=":material/payments:",
               query_params={"asin": asin})
result = score_product(engine, asin, tables, planning, config)

BAND_COLORS = {"Strong": "green", "Promising": "blue", "Weak": "orange", "Poor": "red"}
STATUS_COLORS = {"Not viable": "red", "Not enough data": "gray"}

st.write("")
left, right = st.columns([1.25, 2], gap="large")
with left:
    with st.container(border=True):
        # Only a fully usable score is shown as a headline number. A partial
        # score (too few factors) or a money-losing product would mislead.
        headline = f"{result.total} / 100" if result.status == "Scored" else "—"
        st.metric("Score", headline)
        if result.status == "Scored":
            st.badge(result.band, color=BAND_COLORS.get(result.band, "gray"), icon=":material/insights:")
        else:
            st.badge(result.status, color=STATUS_COLORS.get(result.status, "gray"),
                     icon=":material/block:" if result.status == "Not viable" else ":material/help:")
        st.caption(
            f"Based on {result.coverage_text} · {result.listings} listing"
            f"{'' if result.listings == 1 else 's'} in this market"
            + (f" · main keyword “{result.main_keyword}”" if result.main_keyword else "")
        )
        if result.status == "Not enough data":
            st.caption(f"Products need at least {config.min_coverage} scored factors to get a score.")
        elif result.status == "Not viable" and result.total is not None:
            st.caption(f"The factors alone would give {result.total}, but the product loses money "
                       "with no advertising.")
with right:
    for warning in result.warnings:
        icon = ":material/block:" if warning.startswith("Loses money") else ":material/warning:"
        (st.error if warning.startswith("Loses money") else st.warning)(warning, icon=icon)
    st.info(ORGANIC_NOTICE, icon=":material/trending_flat:")

section("Breakdown", "Points for each factor, the number behind them, and why the factor matters.")
groups = []
for f in result.factors:
    if f.spec.group not in groups:
        groups.append(f.spec.group)

for group in groups:
    rows = [f for f in result.factors if f.spec.group == group]
    with st.container(border=True):
        st.markdown(f"**{group}**")
        for f in rows:
            name, bar, pts = st.columns([2.2, 5, 1.1], vertical_alignment="center")
            name.markdown(f.spec.label, help=f.spec.why)
            if f.scored:
                bar.progress(min(1.0, f.points / f.spec.weight), text=f.value)
                pts.markdown(f"**{f.points:g}** / {f.spec.weight:g}")
            else:
                bar.caption(f":material/help: No data: {f.missing}")
                pts.markdown(f"— / {f.spec.weight:g}")

missing = [f for f in result.factors if not f.scored]
if missing:
    section("Raise the coverage")
    with st.container(border=True):
        for f in missing:
            st.markdown(f"- **{f.spec.label}**: {f.missing}")
        c1, c2 = st.columns(2)
        c1.page_link("views/import_data.py", label="Import competitors or keywords", icon=":material/upload_file:")
        c2.page_link("views/profit.py", label="Enter costs on the Profit page", icon=":material/payments:",
                     query_params={"asin": asin})

with st.expander("How the score works", icon=":material/info:"):
    st.markdown(
        f"""
- Each factor earns 0 to its points. Points scale smoothly between a "no points" and a
  "full points" value; review counts, search volume and competition density use a log scale.
- Factors without data are left out and the total is scaled over the factors that were scored.
  Coverage is always shown; products with fewer than {config.min_coverage} scored factors are
  marked "Not enough data".
- Profit factors use the Phase 2 engine with **no advertising** and everyday (non-peak) fees.
- A product that loses money with no advertising is "Not viable" whatever its score.
- Limits, weights and bands are in `config/scoring.toml` and can be changed.
- Bands: {", ".join(f"{name} ≥ {minimum:g}" for name, minimum in config.bands)}.
"""
    )
