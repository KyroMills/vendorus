"""Vendorus dashboard entry point.

Run with:  streamlit run app/Home.py
"""
import sys
from pathlib import Path

import streamlit as st

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP.parent))  # the vendorus package
sys.path.insert(0, str(APP))  # shared ui helpers

from ui import apply_style  # noqa: E402

st.set_page_config(page_title="Vendorus", page_icon=str(APP / "assets" / "icon.svg"), layout="wide")
apply_style()

pages = {
    "": [st.Page(APP / "views" / "overview.py", title="Overview", icon=":material/dashboard:", default=True)],
    "Research": [
        st.Page(APP / "views" / "opportunities.py", title="Opportunities", icon=":material/leaderboard:",
                url_path="opportunities"),
        st.Page(APP / "views" / "import_data.py", title="Import data", icon=":material/upload_file:",
                url_path="import"),
        st.Page(APP / "views" / "profit.py", title="Profit", icon=":material/payments:", url_path="profit"),
        st.Page(APP / "views" / "score.py", title="Score", icon=":material/insights:", url_path="score"),
    ],
}
st.logo(str(APP / "assets" / "logo.svg"), icon_image=str(APP / "assets" / "icon.svg"), size="large")

st.navigation(pages).run()
