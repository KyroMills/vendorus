"""Validate one product: risks, seasonality, reviews, history, decision."""
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import SEVERITY_BADGES, page_header, section  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.costs import latest_products  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.fees import load_fee_tables  # noqa: E402
from vendorus.profit import load_planning  # noqa: E402
from vendorus.scoring import gather_market, load_scoring, score_market  # noqa: E402
from vendorus.validation import (  # noqa: E402
    DECISIONS,
    get_decision,
    load_validation_config,
    save_decision,
    validate,
)

page_header(
    "Validate",
    "Does a promising product hold up? Risks, seasonality, what customers complain about, "
    "and your go / no-go decision. Validation never changes the score.",
    eyebrow="Research",
)

engine = get_engine(load_settings())
cfg = load_validation_config()
products = latest_products(engine)
if not products:
    with st.container(border=True):
        st.markdown(":material/inbox: No products yet. Import research data first.")
        st.page_link("views/import_data.py", label="Open import", icon=":material/upload_file:")
    st.stop()

labels = {f"{p.asin} · {(p.title or 'untitled')[:70]}": p.asin for p in products}
keys = list(labels)
requested = st.query_params.get("asin")
index = next((i for i, k in enumerate(keys) if labels[k] == requested), 0)
pick, link1, link2 = st.columns([4, 1, 1], vertical_alignment="bottom")
asin = labels[pick.selectbox("Product", keys, index=index)]
st.query_params["asin"] = asin
link1.page_link("views/score.py", label="Score", icon=":material/insights:", query_params={"asin": asin})
link2.page_link("views/profit.py", label="Profit", icon=":material/payments:",
                query_params={"asin": asin})

market = gather_market(engine, asin)
score = score_market(market, load_fee_tables(), load_planning(), load_scoring())
result = validate(engine, market, score, cfg)
decision = get_decision(engine, asin)

# --- summary -----------------------------------------------------------------------
st.write("")
row1, row2 = st.columns(2), st.columns(2)
c1, c2 = row1
c3, c4 = row2
c1.metric("Score", f"{score.total} / 100" if score.status == "Scored" else "—",
          help=score.status if score.status != "Scored" else score.band, border=True)
c2.metric("High risks", result.count("high"), border=True)
c3.metric("Things to check", result.count("medium"), border=True)
done = sum(1 for item in cfg["checklist"]["items"] if decision.checklist.get(item))
c4.metric("Decision", decision.decision, help=f"Checklist {done} of {len(cfg['checklist']['items'])} done",
          border=True)

# --- risk flags --------------------------------------------------------------------------
section("Risk flags", "From your data and the rules in config/validation.toml. Prompts to check, not legal advice.")
with st.container(border=True):
    if not result.flags:
        st.markdown(":material/check_circle: No risk flags from the data you've imported.")
    for flag in result.flags:
        label, color, icon = SEVERITY_BADGES[flag.severity]
        left, right = st.columns([1, 6])
        with left:
            st.badge(label, color=color, icon=icon)
        right.markdown(f"**{flag.title}**  \n{flag.detail}")

# --- seasonality ----------------------------------------------------------------------------
section("Seasonality and demand trend")
season = result.season
with st.container(border=True):
    if season.missing:
        st.caption(f":material/help: {season.missing}")
    else:
        metric_name = {"search_volume": "Monthly searches", "sales": "Monthly sales",
                       "interest": "Search interest"}.get(season.metric, season.metric)
        color = {"Highly seasonal": "orange", "Moderately seasonal": "blue"}.get(season.label, "green")
        st.badge(season.label, color=color, icon=":material/calendar_month:")
        st.caption(f"Busiest calendar month is {season.ratio:.1f}x the quietest.")
        s2, s3 = st.columns(2)
        s2.metric("Peak months", ", ".join(season.peak_months) or "None")
        s3.metric("Last 12 months", f"{season.change_pct:+.0f}%" if season.change_pct is not None else "—",
                  help="Compared with the 12 months before (needs 24 months of history)")
        frame = pd.DataFrame(season.months, columns=["Month", metric_name])
        frame["Month"] = pd.to_datetime(frame["Month"])
        hover = alt.selection_point(fields=["Month"], nearest=True, on="pointerover", empty=False)
        base = alt.Chart(frame).encode(x=alt.X("Month:T", title=None, axis=alt.Axis(format="%b %Y", grid=False)))
        line = base.mark_line(strokeWidth=2).encode(
            y=alt.Y(f"{metric_name}:Q", title=None, axis=alt.Axis(format="~s", tickCount=4)))
        points = base.mark_point(size=80, filled=True, opacity=0).encode(
            y=f"{metric_name}:Q",
            tooltip=[alt.Tooltip("Month:T", format="%B %Y"), alt.Tooltip(f"{metric_name}:Q", format=",.0f")],
        ).add_params(hover)
        rule = base.mark_rule(opacity=0.35).encode().transform_filter(hover)
        st.caption(f"{metric_name} for “{season.subject}”")
        st.altair_chart((line + points + rule).properties(height=240), width="stretch")

# --- reviews --------------------------------------------------------------------------------
section("Customer reviews", "Reviews of this product and its competitors.")
reviews = result.reviews
with st.container(border=True):
    if reviews.missing:
        st.caption(f":material/help: {reviews.missing} Import a reviews file to see common complaints.")
    else:
        r1, r2 = st.columns([1, 2], gap="large")
        with r1:
            st.metric("Reviews", f"{reviews.count:,}", help="Reviews imported for this market")
            st.metric(f"1–{cfg['reviews']['low_star_max']} star share", f"{reviews.complaint_share:.0f}%")
        with r2:
            most = max(reviews.by_rating.values()) or 1
            for stars, n in reviews.by_rating.items():
                st.progress(n / most, text=f"{stars}★ · {n} review{'' if n == 1 else 's'}")
        if reviews.phrases:
            st.markdown("**What complaints mention most**")
            st.dataframe(
                pd.DataFrame({
                    "Phrase": p.text, "Complaints": p.reviews, "Share of complaints": p.share,
                    "Example": p.example,
                } for p in reviews.phrases),
                hide_index=True, width="stretch",
                column_config={
                    "Share of complaints": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
                    "Example": st.column_config.TextColumn(width="large"),
                },
            )
        else:
            st.caption(f"Common phrases appear once at least {cfg['reviews']['min_low_reviews']} "
                       "complaint reviews are imported.")
    st.markdown("**Review velocity**")
    if reviews.velocity:
        st.dataframe(
            pd.DataFrame({"ASIN": a, "New reviews per month": round(v, 1)}
                         for a, v in sorted(reviews.velocity.items(), key=lambda kv: -kv[1])),
            hide_index=True, width="stretch",
        )
    else:
        st.caption(reviews.velocity_note or "")

# --- history -----------------------------------------------------------------------------------
section("Research history", "This ASIN's numbers in each import. Re-import over time to see trends.")
with st.container(border=True):
    if len(result.history) <= 1:
        st.caption("Only one import so far. Import this product again later to track price, sales and reviews.")
    if result.history:
        st.dataframe(
            pd.DataFrame(result.history).rename(columns={
                "imported": "Imported", "price": "Price", "monthly_sales": "Sales/mo (est.)",
                "review_count": "Reviews", "rating": "Rating", "bsr": "BSR",
            }),
            hide_index=True, width="stretch",
            column_config={
                "Imported": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
                "Price": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

# --- checklist and decision ------------------------------------------------------------------
section("Checklist and decision", "Saved for this ASIN. Shows on the Opportunities page.")
with st.form(f"decision-{asin}"):
    checks = {
        item: st.checkbox(item, value=bool(decision.checklist.get(item)), key=f"chk-{asin}-{i}")
        for i, item in enumerate(cfg["checklist"]["items"])
    }
    choice = st.segmented_control("Decision", DECISIONS, default=decision.decision, required=True,
                                  key=f"dec-{asin}")
    notes = st.text_area("Notes", value=decision.notes, placeholder="Why go, watch or pass?",
                         key=f"notes-{asin}")
    if st.form_submit_button("Save", type="primary", icon=":material/save:"):
        save_decision(engine, asin, choice, checks, notes)
        st.toast(f"Saved: {choice}", icon=":material/check_circle:")
        st.rerun()
