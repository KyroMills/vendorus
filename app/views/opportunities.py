"""Ranked opportunities: every researched product, best first."""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import cached_scores, keep, page_header, remember, section  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.profit import ORGANIC_NOTICE  # noqa: E402
from vendorus.ranking import Filters, apply_filters, export_frame, rank, table  # noqa: E402
from vendorus.scoring import load_scoring  # noqa: E402

page_header(
    "Opportunities",
    "Every researched product, ranked by opportunity score. Click a row for its full breakdown.",
    eyebrow="Research",
)

engine = get_engine(load_settings())
config = load_scoring()
scores = cached_scores(engine)

if not scores:
    with st.container(border=True):
        st.markdown(":material/inbox: Nothing to rank yet. Import products, competitors and keywords first.")
        st.page_link("views/import_data.py", label="Open import", icon=":material/upload_file:")
    st.stop()

# --- filters (remembered when you switch pages) ------------------------------
bands = [name for name, _ in config.bands]
categories = sorted({s.category or "Uncategorised" for s in scores})
prices = [float(s.price) for s in scores if s.price is not None]
for key, default in (("opp_search", ""), ("opp_min", 0), ("opp_bands", []), ("opp_cats", []),
                     ("opp_hide_warn", False)):
    remember(key, default)

with st.container(border=True):
    f1, f2, f3 = st.columns([3, 2, 2])
    f1.text_input("Search", key="opp_search", placeholder="ASIN or product name",
                  label_visibility="collapsed")
    f2.multiselect("Band", bands, key="opp_bands", placeholder="Any band", label_visibility="collapsed")
    f3.multiselect("Category", categories, key="opp_cats", placeholder="Any category",
                   label_visibility="collapsed")
    g1, g2, g3 = st.columns([3, 3, 2], vertical_alignment="center")
    g1.slider("Minimum score", 0, 100, key="opp_min", step=5)
    if prices and min(prices) < max(prices):
        lo, hi = float(min(prices)), float(max(prices))
        remember("opp_price", (lo, hi))
        current = st.session_state.get("opp_price", (lo, hi))
        if not (lo <= current[0] <= hi and lo <= current[1] <= hi):
            st.session_state["opp_price"] = (lo, hi)
        price_range = g2.slider("Price ($)", lo, hi, key="opp_price", step=1.0)
    else:
        price_range = (None, None)
    g3.toggle("No warnings", key="opp_hide_warn", help="Hide products that have any warning")
keep("opp_search", "opp_min", "opp_bands", "opp_cats", "opp_hide_warn", "opp_price")

filters = Filters(
    search=st.session_state["opp_search"],
    min_score=st.session_state["opp_min"],
    bands=tuple(st.session_state["opp_bands"]),
    categories=tuple(st.session_state["opp_cats"]),
    price_min=price_range[0],
    price_max=price_range[1],
    hide_warnings=st.session_state["opp_hide_warn"],
)
ranking = rank(apply_filters(scores, filters))
shown = len(ranking.all)
st.caption(
    f"Showing {shown} of {len(scores)} products"
    + ("" if shown == len(scores) else " (filtered)")
    + ". Scores use no advertising and everyday Amazon fees."
)

# --- tables --------------------------------------------------------------------
MONEY = st.column_config.NumberColumn(format="$%.2f")
COLUMNS = {
    "#": st.column_config.NumberColumn(width="small"),
    "ASIN": st.column_config.TextColumn(width=112),
    "Product": st.column_config.TextColumn(width="medium"),
    "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d", width="small"),
    "Price": MONEY,
    "Profit/unit": MONEY,
    "Break-even": MONEY,
    "Margin %": st.column_config.NumberColumn(format="%.1f%%"),
    "Sales/mo (est.)": st.column_config.NumberColumn(format="%d"),
    "Warnings": st.column_config.NumberColumn(width="small"),
    "Researched": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
}


def clickable(frame: pd.DataFrame, key: str, hide: tuple[str, ...] = ()) -> None:
    """A table whose rows open the product's Score page."""
    event = st.dataframe(
        frame.drop(columns=list(hide)),
        hide_index=True,
        width="stretch",
        column_config=COLUMNS,
        on_select="rerun",
        selection_mode="single-row",
        key=key,
    )
    rows = event.selection.rows if event and event.selection else []
    if rows:
        st.switch_page("views/score.py", query_params={"asin": frame.iloc[rows[0]]["ASIN"]})


ranked_tab, needs_tab, viable_tab = st.tabs([
    f"Ranked · {len(ranking.ranked)}",
    f"Needs data · {len(ranking.needs_data)}",
    f"Not viable · {len(ranking.not_viable)}",
])

with ranked_tab:
    if ranking.ranked:
        clickable(table(ranking.ranked, numbered=True), "ranked_table")
    else:
        st.info("No products with a full score match these filters.", icon=":material/filter_alt:")

with needs_tab:
    st.caption(f"Fewer than {config.min_coverage} of 10 factors have data, so these get no score yet.")
    if ranking.needs_data:
        frame = table(ranking.needs_data)
        frame["Missing"] = [
            ", ".join(f.spec.label for f in s.factors if not f.scored) for s in ranking.needs_data
        ]
        clickable(frame, "needs_table", hide=("Score", "Band", "Profit/unit", "Margin %", "Break-even"))
    else:
        st.info("Nothing here: every product has enough data.", icon=":material/check_circle:")

with viable_tab:
    st.caption("These lose money per unit with no advertising, whatever their score.")
    if ranking.not_viable:
        clickable(table(ranking.not_viable), "viable_table", hide=("Score", "Band", "Margin %"))
    else:
        st.info("Nothing here: no product loses money at its price.", icon=":material/check_circle:")

# --- compare -----------------------------------------------------------------------
st.write("")
section("Compare", "Pick 2 to 4 products to see their factors side by side.")
options = {f"{s.asin} · {(s.title or '')[:40]}": s for s in ranking.all}
picked = st.multiselect("Products to compare", list(options), max_selections=4,
                        default=list(options)[:min(2, len(ranking.ranked))] if len(ranking.ranked) >= 2 else [],
                        label_visibility="collapsed", placeholder="Choose products")
if len(picked) >= 2:
    chosen = [options[p] for p in picked]
    rows = {}
    for s in chosen:
        name = s.asin
        rows.setdefault("Score", {})[name] = s.total if s.status == "Scored" else None
        for f in s.factors:
            rows.setdefault(f.spec.label, {})[name] = f.points
        rows.setdefault("Organic profit/unit", {})[name] = float(s.profit) if s.profit is not None else None
    numbers = pd.DataFrame.from_dict(rows, orient="index").astype(float)  # missing -> NaN
    best = numbers.eq(numbers.max(axis=1), axis=0) & numbers.notna()
    shown_values = numbers.map(lambda v: "—" if pd.isna(v) else f"{v:g}")
    styled = shown_values.style.apply(
        lambda _: best.replace({True: "background-color: rgba(16, 185, 129, 0.22)", False: ""}),
        axis=None,
    )
    st.dataframe(styled, width="stretch", height=(len(numbers) + 1) * 35 + 3)
    st.caption("Points per factor (out of 10). The best value in each row is highlighted; “—” means no data.")

# --- export and notes ------------------------------------------------------------------
st.write("")
left, right = st.columns([1.5, 2], vertical_alignment="center")
left.download_button(
    f"Download CSV ({shown} products)",
    data=export_frame(ranking).to_csv(index=False).encode(),
    file_name="vendorus_opportunities.csv",
    mime="text/csv",
    icon=":material/download:",
)
right.caption("Includes every factor's points and the number behind it.")
st.info(ORGANIC_NOTICE, icon=":material/trending_flat:")
