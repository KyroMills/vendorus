"""Vendorus dashboard entry point.

Run with:  streamlit run app/Home.py
"""
import sys
from pathlib import Path

import streamlit as st

# Let Streamlit find the `vendorus` package when run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vendorus import __version__  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.db import check_connection, get_engine  # noqa: E402

st.set_page_config(page_title="Vendorus", layout="wide")

st.title("Vendorus")
st.caption(f"Product research engine · v{__version__}")

settings = load_settings()

col1, col2 = st.columns(2)
with col1:
    st.subheader("System status")
    try:
        ok = check_connection(get_engine(settings))
        st.success("Database connected" if ok else "Database did not respond")
    except Exception as exc:  # show the problem instead of crashing
        st.error(f"Database error: {exc}")
    st.write(f"Environment: `{settings.environment}`")
    st.write("Database: " + ("SQLite (local file)" if settings.is_sqlite else "PostgreSQL"))

with col2:
    st.subheader("Coming next")
    st.markdown(
        "- ~~Phase 1: import product research data~~ done: see the **Import** page\n"
        "- ~~Phase 2: fee and profit engine~~ done: see the **Profit** page\n"
        "- Phase 3: opportunity scoring\n"
        "- Phase 4: ranked opportunities"
    )
