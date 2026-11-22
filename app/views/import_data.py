"""Import research data from a CSV file."""
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent.parent
ROOT = APP.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP))

from ui import page_header, section  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.importers import SPECS, ImportFileError, ImportKind, import_csv, import_history  # noqa: E402

page_header(
    "Import research data",
    "Upload a CSV from any research tool, or fill in a template by hand. "
    "Only the ASIN (or keyword) is required; bad rows are skipped and explained.",
    eyebrow="Research",
)

engine = get_engine(load_settings())

ICONS = {
    ImportKind.PRODUCTS: ":material/inventory_2:",
    ImportKind.KEYWORDS: ":material/search:",
    ImportKind.COMPETITORS: ":material/groups:",
    ImportKind.TRENDS: ":material/show_chart:",
    ImportKind.REVIEWS: ":material/reviews:",
}
kind = st.segmented_control(
    "What are you importing?",
    options=list(ImportKind),
    format_func=lambda k: k.value.capitalize(),
    default=ImportKind.PRODUCTS,
    required=True,
)

left, right = st.columns([3, 2], gap="large")
with left:
    with st.container(border=True):
        uploaded = st.file_uploader(f"{kind.value.capitalize()} CSV", type=["csv"])
with right:
    with st.container(border=True):
        st.markdown(f"{ICONS[kind]} **{kind.value.capitalize()} template**")
        st.caption("A blank CSV with every column this import understands.")
        template = ROOT / "templates" / f"{kind.value}_template.csv"
        st.download_button(
            "Download template",
            data=template.read_bytes(),
            file_name=template.name,
            mime="text/csv",
            icon=":material/download:",
        )
        with st.expander("Accepted column names"):
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

if uploaded is not None:
    data = uploaded.getvalue()
    section("Preview", "First 20 rows, exactly as they appear in the file.")
    try:
        preview = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, nrows=20)
        st.dataframe(preview, hide_index=True, width="stretch")
    except Exception as exc:  # the importer gives the real verdict below
        st.warning(f"Could not preview this file: {exc}", icon=":material/warning:")

    if st.button(f"Import {kind.value}", type="primary", icon=":material/upload:"):
        try:
            report = import_csv(engine, io.BytesIO(data), kind, label=uploaded.name)
        except ImportFileError as exc:
            st.error(str(exc), icon=":material/error:")
        else:
            section("Result")
            a, b, c = st.columns(3)
            a.metric("Rows in file", report.rows_total, border=True)
            b.metric("Imported", report.rows_accepted, border=True)
            c.metric("Rejected", report.rows_rejected, border=True)

            if report.rows_accepted:
                st.success(report.summary(), icon=":material/check_circle:")
            else:
                st.error("No rows were imported. See the reasons below.", icon=":material/error:")
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
                    + ", ".join(name.replace("_", " ") for name in report.absent_fields),
                    icon=":material/info:",
                )
            if report.unused_columns:
                st.caption("Columns not recognised and ignored: " + ", ".join(report.unused_columns))


def local_time(moment: datetime) -> datetime:
    # Times are stored in UTC; SQLite drops the time zone, so restore it first.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone()


st.write("")
section("Import history")
history = import_history(engine)
if history:
    st.dataframe(
        pd.DataFrame(
            {
                "Imported": local_time(s.imported_at),
                "Type": s.kind.capitalize(),
                "Source": s.source.upper(),
                "File": s.label,
                "Rows": s.rows_total,
                "Imported rows": s.rows_accepted,
                "Rejected": s.rows_rejected,
            }
            for s in history
        ),
        hide_index=True,
        width="stretch",
        column_config={"Imported": st.column_config.DatetimeColumn(format="MMM D, YYYY · HH:mm")},
    )
else:
    with st.container(border=True):
        st.markdown(":material/inbox: Nothing imported yet.")
