"""Phase 1: importing research data. All data here is made up."""
from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vendorus.db import create_tables, get_engine
from vendorus.config import load_settings
from vendorus.importers import (
    SPECS,
    CsvSource,
    ImportFileError,
    ImportKind,
    RawRow,
    import_csv,
    import_history,
    run_import,
)
from vendorus.importers.fields import FieldError, FieldSpec, normalize_header, parse_value
from vendorus.models import CompetitorAsin, Keyword, Product, ResearchSnapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    return get_engine(load_settings(env_file=tmp_path / "missing.env"))


def count(engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


# --- single values ---------------------------------------------------------

PRICE = FieldSpec("price", "number", minimum=0)
REVIEWS = FieldSpec("review_count", "integer", minimum=0)
RATING = FieldSpec("rating", "number", minimum=0, maximum=5)
ASIN = FieldSpec("asin", "asin", required=True)


@pytest.mark.parametrize(
    "raw, expected",
    [("19.99", 19.99), ("$1,299.00", 1299.0), (" 7 ", 7.0), ("€5", 5.0), ("", None), ("N/A", None), ("-", None)],
)
def test_numbers_are_cleaned(raw, expected):
    assert parse_value(PRICE, raw) == expected


@pytest.mark.parametrize(
    "spec, raw, message",
    [
        (PRICE, "abc", "price 'abc' is not a number"),
        (PRICE, "-1", "price '-1' cannot be negative"),
        (PRICE, "inf", "price 'inf' is not a number"),
        (REVIEWS, "1.5", "review count '1.5' must be a whole number"),
        (RATING, "6", "rating '6' must be at most 5"),
        (ASIN, "B0TEST03", "asin 'B0TEST03' is not a valid ASIN"),
        (ASIN, "A0TEST0001", "asin 'A0TEST0001' is not a valid ASIN"),
    ],
)
def test_bad_values_explain_why(spec, raw, message):
    with pytest.raises(FieldError, match=message):
        parse_value(spec, raw)


def test_asins_are_uppercased_and_isbns_accepted():
    assert parse_value(ASIN, " b0test0001 ") == "B0TEST0001"
    assert parse_value(ASIN, "030640615x") == "030640615X"


def test_review_counts_become_integers():
    value = parse_value(REVIEWS, "1,200")
    assert value == 1200 and isinstance(value, int)


@pytest.mark.parametrize("header", ["Price ($)", "price", "PRICE", " Price [USD] "])
def test_column_names_match_loosely(header):
    assert normalize_header(header) == "price"


# --- whole files -----------------------------------------------------------

def test_clean_file_imports_everything(engine):
    report = import_csv(engine, FIXTURES / "products_clean.csv", "products")

    assert (report.rows_total, report.rows_accepted, report.rows_rejected) == (3, 3, 0)
    assert report.unused_columns == [] and report.absent_fields == []
    with Session(engine) as session:
        mat = session.scalars(select(Product).where(Product.asin == "B0TEST0001")).one()
        assert mat.price == 19.99 and mat.bsr == 1500 and mat.monthly_sales == 900
        assert mat.missing == []
        assert mat.snapshot.label == "products_clean.csv"
        assert mat.snapshot.source == "csv"


def test_messy_file_keeps_good_rows_and_explains_bad_ones(engine):
    report = import_csv(engine, FIXTURES / "products_messy.csv", ImportKind.PRODUCTS)

    assert report.rows_total == 9  # the blank line is skipped, not counted
    assert report.rows_accepted == 2
    assert {e.row: e.reasons for e in report.rejected} == {
        4: ["asin 'B0TEST03' is not a valid ASIN"],
        5: ["rating '6' must be at most 5"],
        6: ["price 'abc' is not a number"],
        7: ["review count '-3' cannot be negative"],
        9: ["asin is missing"],
        10: ["price '-1' cannot be negative", "review count '1.5' must be a whole number"],
        11: ["duplicate of row 2"],
    }
    assert str(report.rejected[0]) == "row 4: asin 'B0TEST03' is not a valid ASIN"
    assert report.unused_columns == ["Notes"]
    assert report.absent_fields == ["brand", "category", "bsr", "monthly_revenue"]
    assert report.summary() == "2 of 9 products rows imported, 7 rejected"

    with Session(engine) as session:
        products = {p.asin: p for p in session.scalars(select(Product))}
    assert set(products) == {"B0TEST0001", "B0TEST0002"}
    assert products["B0TEST0001"].price == 1299.0
    # Blank values are recorded so scoring can say "not enough data".
    assert "monthly_sales" in products["B0TEST0002"].missing
    assert products["B0TEST0002"].monthly_sales is None


def test_missing_required_column_stops_import_and_saves_nothing(engine):
    with pytest.raises(ImportFileError, match=r"Required column missing: asin .*Columns found: Title, Price"):
        import_csv(engine, FIXTURES / "products_no_asin.csv", "products")
    create_tables(engine)
    assert count(engine, ResearchSnapshot) == 0


@pytest.mark.parametrize(
    "content, message",
    [(b"", "The file is empty"), (b"asin,price\n\n", "no data rows")],
)
def test_empty_files_are_refused(engine, content, message):
    with pytest.raises(ImportFileError, match=message):
        import_csv(engine, io.BytesIO(content), "products")


def test_reimport_adds_a_new_snapshot_instead_of_overwriting(engine):
    first = import_csv(engine, FIXTURES / "products_clean.csv", "products")
    second = import_csv(engine, FIXTURES / "products_clean.csv", "products")

    assert first.snapshot_id != second.snapshot_id
    assert count(engine, Product) == 6
    history = import_history(engine)
    assert [s.id for s in history] == [second.snapshot_id, first.snapshot_id]
    assert history[0].rows_accepted == 3


def test_all_rows_rejected_is_still_recorded(engine):
    data = b"asin,price\nBAD,1\nALSOBAD,2\n"
    report = import_csv(engine, io.BytesIO(data), "products", label="bad.csv")
    assert report.rows_accepted == 0 and report.rows_rejected == 2
    assert import_history(engine)[0].label == "bad.csv"


def test_keywords_import(engine):
    report = import_csv(engine, FIXTURES / "keywords.csv", "keywords")

    assert report.rows_accepted == 2
    assert {e.row: e.reasons for e in report.rejected} == {
        3: ["duplicate of row 2"],  # same phrase, different case and spacing
        5: ["search volume 'lots' is not a number"],
    }
    with Session(engine) as session:
        keywords = {k.phrase: k for k in session.scalars(select(Keyword))}
    assert keywords["silicone baking mat"].search_volume == 45000
    assert keywords["silicone baking mat"].product_asin == "B0TEST0001"
    assert keywords["garlic press"].missing == ["competing_products"]


def test_competitors_import(engine):
    report = import_csv(engine, FIXTURES / "competitors.csv", "competitors")

    assert report.rows_accepted == 2
    assert {e.row: e.reasons for e in report.rejected} == {
        4: ["duplicate of row 2"],
        5: ["competes with is missing"],
    }
    with Session(engine) as session:
        targets = {c.asin: c.competes_with for c in session.scalars(select(CompetitorAsin))}
    assert targets == {"B0COMP0001": "B0TEST0001", "B0COMP0002": "silicone baking mat"}


def test_windows_excel_files_are_read(engine):
    # Excel on Windows: cp1252 text, CRLF line endings, a "£" in a title.
    data = "asin,title,price\r\nB0TEST0001,Mat £ edition,9.99\r\n".encode("cp1252")
    report = import_csv(engine, io.BytesIO(data), "products")
    assert report.rows_accepted == 1
    with Session(engine) as session:
        assert session.scalars(select(Product.title)).one() == "Mat £ edition"


def test_utf8_bom_header_is_recognised(engine):
    data = "﻿ASIN,Price\nB0TEST0001,5\n".encode("utf-8")
    assert import_csv(engine, io.BytesIO(data), "products").rows_accepted == 1


def test_another_source_can_plug_in(engine):
    """A future Helium 10 connector only needs columns() and rows()."""

    class FakeConnector:
        source_name = "helium10"
        label = "Black Box search"

        def columns(self):
            return ["ASIN", "Price", "ASIN Sales"]

        def rows(self):
            yield RawRow(1, {"ASIN": "B0TEST0009", "Price": "30", "ASIN Sales": "450"})

    report = run_import(engine, FakeConnector(), "products")
    assert report.rows_accepted == 1
    assert import_history(engine)[0].source == "helium10"
    with Session(engine) as session:
        assert session.scalars(select(Product.monthly_sales)).one() == 450


def test_deleting_a_snapshot_removes_its_rows(engine):
    report = import_csv(engine, FIXTURES / "products_clean.csv", "products")
    with Session(engine) as session, session.begin():
        session.delete(session.get(ResearchSnapshot, report.snapshot_id))
    assert count(engine, Product) == 0


@pytest.mark.parametrize("kind", list(ImportKind))
def test_templates_match_the_fields(kind):
    with open(ROOT / "templates" / f"{kind.value}_template.csv", newline="") as f:
        header = next(csv.reader(f))
    assert header == [spec.name for spec in SPECS[kind]]


def test_csv_source_reports_spreadsheet_row_numbers():
    source = CsvSource(io.BytesIO(b"asin\n\nB0TEST0001\n"))
    assert [row.number for row in source.rows()] == [3]
