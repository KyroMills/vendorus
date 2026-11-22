"""Shared look and feel for every dashboard page.

Colours and fonts live in .streamlit/config.toml (light and dark themes).
This module adds the few touches the theme can't: page width, spacing,
card styling and a consistent page header.
"""
from __future__ import annotations

import streamlit as st

_CSS = """
<style>
/* Comfortable reading width and tighter top spacing. */
.block-container { max-width: 1180px; padding-top: 2.2rem; padding-bottom: 4rem; }

/* Headings: slightly tighter letter spacing reads as more modern. */
h1, h2, h3 { letter-spacing: -0.02em; }

/* Metric cards: a quiet label above a strong value. */
[data-testid="stMetric"] { padding: 0.9rem 1.1rem; }
[data-testid="stMetricLabel"] p {
  font-size: 0.78rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; opacity: 0.65;
}

/* Bordered containers act as cards: a little more padding, soft shadow. */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

/* Alerts: softer, rounded, no heavy left bar. */
[data-testid="stAlert"] > div { border-radius: 0.75rem; }

/* Page subtitle under the header. */
.vd-subtitle { opacity: 0.7; margin-top: -0.6rem; margin-bottom: 1.4rem; font-size: 1rem; }
.vd-eyebrow {
  font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  opacity: 0.55; margin-bottom: 0.2rem;
}
.vd-muted { opacity: 0.65; font-size: 0.9rem; }
</style>
"""


def apply_style() -> None:
    st.html(_CSS)


def page_header(title: str, subtitle: str | None = None, eyebrow: str | None = None) -> None:
    if eyebrow:
        st.html(f'<div class="vd-eyebrow">{eyebrow}</div>')
    st.title(title, anchor=False)
    if subtitle:
        st.html(f'<div class="vd-subtitle">{subtitle}</div>')


def section(title: str, caption: str | None = None) -> None:
    st.subheader(title, anchor=False)
    if caption:
        st.caption(caption)


def usd(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


# --- shared data ----------------------------------------------------------------

@st.cache_data(show_spinner="Scoring products…", max_entries=4)
def _score_all(database_url: str, version: tuple):
    """Score every product. Cached per database and data version, so it only
    runs again after an import, a cost change or a settings change."""
    from vendorus.config import load_settings
    from vendorus.db import get_engine
    from vendorus.fees import load_fee_tables
    from vendorus.profit import load_planning
    from vendorus.scoring import load_scoring, score_all

    engine = get_engine(load_settings())
    return score_all(engine, load_fee_tables(), load_planning(), load_scoring())


def cached_scores(engine):
    from dataclasses import astuple

    from vendorus.ranking import data_version

    return _score_all(str(engine.url), astuple(data_version(engine)))


def remember(key: str, default=None):
    """Keep a widget's value when you switch pages and come back.
    Call before creating the widget with key=key; call keep(key) after."""
    stored = f"_kept_{key}"
    if key in st.session_state:
        return  # the widget is live on this page: keep what the user just set
    if stored in st.session_state:
        st.session_state[key] = st.session_state[stored]  # back from another page
    elif default is not None:
        st.session_state[key] = default


def keep(*keys: str) -> None:
    for key in keys:
        if key in st.session_state:
            st.session_state[f"_kept_{key}"] = st.session_state[key]
