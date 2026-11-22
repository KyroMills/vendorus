"""Overview: where things stand and what to do next."""
import sys
from datetime import date, timezone
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import page_header, section  # noqa: E402
from vendorus import __version__  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.db import check_connection, get_engine  # noqa: E402
from vendorus.fees import load_fee_tables  # noqa: E402
from vendorus.summary import summary  # noqa: E402

settings = load_settings()
engine = get_engine(settings)
tables = load_fee_tables()
today = date.today()

page_header(
    "Product research",
    "Find private-label products worth selling, with numbers you can check.",
    eyebrow="Overview",
)

try:
    connected = check_connection(engine)
    stats = summary(engine)
except Exception as exc:  # show the problem instead of crashing
    connected, stats = False, None
    st.error(f"Database error: {exc}", icon=":material/error:")

if stats:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Products", f"{stats.products:,}", border=True, help="Distinct ASINs imported")
    c2.metric("Keywords", f"{stats.keywords:,}", border=True)
    c3.metric("With costs", f"{stats.costed:,}", border=True, help="Products with a unit cost entered")
    c4.metric("Imports", f"{stats.imports:,}", border=True)

st.write("")
left, right = st.columns([3, 2], gap="large")

with left:
    section("Get started")
    with st.container(border=True):
        st.markdown("**1. Import research data**")
        st.caption("Upload a CSV from any research tool, or fill in a template by hand.")
        st.page_link("views/import_data.py", label="Open import", icon=":material/upload_file:")
    with st.container(border=True):
        st.markdown("**2. Check profitability**")
        st.caption("Amazon fees, landed cost and margin per unit. Organic-first: no ads unless you add them.")
        st.page_link("views/profit.py", label="Open profit calculator", icon=":material/payments:")
    with st.container(border=True):
        st.markdown("**3. Score opportunities**")
        st.caption("Coming in Phase 3: a 0–100 score with a full breakdown.")
        st.badge("Coming next", icon=":material/schedule:", color="gray")

with right:
    section("Status")
    with st.container(border=True):
        if connected:
            st.badge("Database connected", icon=":material/check_circle:", color="green")
        else:
            st.badge("Database unavailable", icon=":material/error:", color="red")
        st.caption(
            ("SQLite (local file)" if settings.is_sqlite else "PostgreSQL")
            + f" · {settings.environment} · v{__version__}"
        )
        if stats and stats.last_import:
            last = stats.last_import
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            st.caption(f"Last import: {last.astimezone():%b %d, %Y at %H:%M}")

    with st.container(border=True):
        period = tables.period_for(today)
        if tables.is_out_of_date(today):
            st.badge("Fee table expired", icon=":material/error:", color="red")
        elif tables.reviewed:
            st.badge("Fee table reviewed", icon=":material/verified:", color="green")
        else:
            st.badge("Fee table not yet reviewed", icon=":material/rule:", color="orange")
        st.caption(
            f"Amazon US fees copied {tables.retrieved:%b %d, %Y}. "
            + (f"In force today: {period.label}." if period else "")
        )

    with st.container(border=True):
        st.markdown("**Roadmap**")
        for done, label in (
            (True, "Foundation"),
            (True, "Data import"),
            (True, "Fee and profit engine"),
            (False, "Opportunity scoring"),
            (False, "Ranked opportunities"),
        ):
            icon = ":material/check_circle:" if done else ":material/radio_button_unchecked:"
            st.markdown(f"{icon} {label}")
