"""Suppliers you've found or talked to."""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

APP = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP.parent))
sys.path.insert(0, str(APP))

from ui import page_header, section  # noqa: E402
from vendorus.config import load_settings  # noqa: E402
from vendorus.db import get_engine  # noqa: E402
from vendorus.sourcing import delete_supplier, list_suppliers, load_sourcing_config, save_supplier  # noqa: E402

page_header(
    "Suppliers",
    "Everyone you're sourcing from or talking to. Details stay in your local database.",
    eyebrow="Sourcing",
)

engine = get_engine(load_settings())
cfg = load_sourcing_config()
suppliers = list_suppliers(engine)
STATUS_COLORS = {"Prospect": "gray", "Contacted": "blue", "Sampling": "violet", "Approved": "green",
                 "Rejected": "red"}


def supplier_form(key: str, supplier=None) -> dict | None:
    """Fields for a supplier. Returns the values when submitted."""
    with st.form(key, clear_on_submit=supplier is None):
        a, b, c = st.columns(3)
        name = a.text_input("Name", value=getattr(supplier, "name", "") or "")
        country = b.text_input("Country", value=getattr(supplier, "country", "") or "")
        platforms = cfg["platforms"]
        current = getattr(supplier, "platform", None)
        platform = c.selectbox("Found on", platforms, index=platforms.index(current) if current in platforms else 0)
        d, e, f = st.columns(3)
        contact = d.text_input("Contact person", value=getattr(supplier, "contact_name", "") or "")
        email = e.text_input("Email", value=getattr(supplier, "email", "") or "")
        website = f.text_input("Website or store link", value=getattr(supplier, "website", "") or "")
        statuses = cfg["statuses"]
        status = st.segmented_control("Status", statuses, required=True,
                                      default=getattr(supplier, "status", None) or statuses[0])
        notes = st.text_area("Notes", value=getattr(supplier, "notes", "") or "",
                             placeholder="Certifications, communication, sample quality…")
        label = "Save changes" if supplier else "Add supplier"
        if st.form_submit_button(label, type="primary", icon=":material/save:"):
            if not name.strip():
                st.error("A supplier needs a name.")
                return None
            return dict(name=name, country=country, platform=platform, contact_name=contact,
                        email=email, website=website, status=status, notes=notes)
    return None


with st.expander("Add a supplier", icon=":material/add_business:", expanded=not suppliers):
    values = supplier_form("new-supplier")
    if values:
        save_supplier(engine, **values)
        st.toast(f"Added {values['name']}", icon=":material/check_circle:")
        st.rerun()

if not suppliers:
    st.stop()

section("Your suppliers")
frame = pd.DataFrame(
    {
        "Name": s.name, "Country": s.country or "", "Found on": s.platform or "", "Status": s.status,
        "Quotes": count, "Contact": s.contact_name or "", "Updated": s.updated_at,
    }
    for s, count in suppliers
)
st.dataframe(frame, hide_index=True, width="stretch", column_config={
    "Updated": st.column_config.DatetimeColumn(format="MMM D, YYYY"),
    "Quotes": st.column_config.NumberColumn(width="small"),
})
counts = {status: sum(1 for s, _ in suppliers if s.status == status) for status in cfg["statuses"]}
with st.container(horizontal=True):
    for status, n in counts.items():
        if n:
            st.badge(f"{status}: {n}", color=STATUS_COLORS.get(status, "gray"))

section("Edit a supplier")
by_label = {f"{s.name} ({s.country or 'country unknown'})": s for s, _ in suppliers}
chosen = by_label[st.selectbox("Supplier", list(by_label), label_visibility="collapsed")]
values = supplier_form(f"edit-{chosen.id}", chosen)
if values:
    save_supplier(engine, chosen.id, **values)
    st.toast(f"Saved {values['name']}", icon=":material/check_circle:")
    st.rerun()

with st.expander("Delete this supplier", icon=":material/delete:"):
    n_quotes = dict((s.id, c) for s, c in suppliers)[chosen.id]
    st.caption(f"Deletes {chosen.name} and its {n_quotes} quote{'' if n_quotes == 1 else 's'}. "
               "Products using one of its quotes keep their costs. This can't be undone.")
    if st.checkbox("I understand", key=f"confirm-del-{chosen.id}"):
        if st.button(f"Delete {chosen.name}", icon=":material/delete:", key=f"del-{chosen.id}"):
            delete_supplier(engine, chosen.id)
            st.toast(f"Deleted {chosen.name}")
            st.rerun()

st.page_link("views/quotes.py", label="Add and compare quotes", icon=":material/request_quote:")
