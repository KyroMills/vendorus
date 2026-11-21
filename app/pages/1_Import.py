"""Import research data from a CSV file."""
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from vendorus.config import load_settings  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.importers import SPECS, ImportFileError, ImportKind, import_csv, import_history  # noqa: E402

st.set_page_config(page_title="Import · Vendorus", layout="wide")
st.title("Import research data")
st.caption(
    "Upload a CSV from any research tool, or fill in a template by hand. "
    "Only the ASIN (or keyword) column is required. Bad rows are skipped and explained."
)

engine = get_engine(load_settings())

kind = st.radio(
    "What are you importing?",
    options=list(ImportKind),
    format_func=lambda k: k.value.capitalize(),
    horizontal=True,
)

left, right = st.columns([3, 2])
with right:
    template = ROOT / "templates" / f"{kind.value}_template.csv"
    st.download_button(
        "Download blank template",
        data=template.read_bytes(),
        file_name=template.name,
        mime="text/csv",
    )
    with st.expander("Columns this import understands"):
        st.dataframe(
            pd.DataFrame(
                {
                    "Field": spec.label,
                    "Required": "yes" if spec.required else "",
                    "Accepted column names": ", ".join(spec.accepted_columns),
                }
                for spec in SPECS[kind]
            ),
            hide_index=True,
            width="stretch",
        )

with left:
    uploaded = st.file_uploader("CSV file", type=["csv"])

if uploaded is not None:
    data = uploaded.getvalue()
    st.subheader("Preview")
    try:
        preview = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, nrows=20)
        st.dataframe(preview, hide_index=True, width="stretch")
    except Exception as exc:  # the importer gives the real verdict below
        st.warning(f"Could not preview this file: {exc}")

    if st.button(f"Import {kind.value}", type="primary"):
        try:
            report = import_csv(engine, io.BytesIO(data), kind, label=uploaded.name)
        except ImportFileError as exc:
            st.error(str(exc))
        else:
            st.subheader("Result")
            a, b, c = st.columns(3)
            a.metric("Rows in file", report.rows_total)
            b.metric("Imported", report.rows_accepted)
            c.metric("Rejected", report.rows_rejected)

            if report.rows_accepted:
                st.success(report.summary())
            else:
                st.error("No rows were imported. See the reasons below.")
            if report.rejected:
                st.dataframe(
                    pd.DataFrame(
                        {"Row": e.row, "Reason": "; ".join(e.reasons)} for e in report.rejected
                    ),
                    hide_index=True,
                    width="stretch",
                )
            if report.absent_fields:
                st.info(
                    "Not in this file (left blank): "
                    + ", ".join(name.replace("_", " ") for name in report.absent_fields)
                )
            if report.unused_columns:
                st.caption("Columns not recognised and ignored: " + ", ".join(report.unused_columns))

def local_time(moment: datetime) -> datetime:
    # Times are stored in UTC; SQLite drops the time zone, so restore it first.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


st.subheader("Import history")
history = import_history(engine)
if history:
    st.dataframe(
        pd.DataFrame(
            {
                "Imported": local_time(s.imported_at).strftime("%Y-%m-%d %H:%M"),
                "Type": s.kind,
                "Source": s.source,
                "File": s.label,
                "Rows": s.rows_total,
                "Imported rows": s.rows_accepted,
                "Rejected": s.rows_rejected,
            }
            for s in history
        ),
        hide_index=True,
        width="stretch",
    )
else:
    st.write("Nothing imported yet.")
