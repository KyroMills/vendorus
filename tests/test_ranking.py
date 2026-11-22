"""Ranking, filters, export, cache versioning and the Milestone 1 flow."""
from __future__ import annotations

import io
import time
from datetime import datetime
from decimal import Decimal as D

import pytest

from test_scoring import COMPETITORS, KEYWORDS, PRODUCTS, with_costs
from vendorus.config import load_settings
from vendorus.costs import save_cost
from vendorus.db import get_engine
from vendorus.fees import load_fee_tables
from vendorus.importers import import_csv
from vendorus.profit import Assumptions
from vendorus.ranking import Filters, apply_filters, data_version, export_frame, rank, table
from vendorus.scoring import Score, gather_all_markets, gather_market, load_scoring, score_all

T = load_fee_tables()
A = Assumptions()
CONFIG = load_scoring()


def fake(asin, total=None, status="Scored", coverage=10, margin=None, profit=None, **kw) -> Score:
    """A Score with just enough filled in for ranking tests."""
    from vendorus.scoring import FactorResult

    factors = [FactorResult(spec, 5.0 if i < coverage else None, "x", None if i < coverage else "none")
               for i, spec in enumerate(CONFIG.factors)]
    return Score(asin=asin, total=total, status=status, band=kw.pop("band", "Strong" if status == "Scored" else None),
                 factors=factors, margin_pct=None if margin is None else D(str(margin)),
                 profit=None if profit is None else D(str(profit)), **kw)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'r.db').as_posix()}")
    return get_engine(load_settings(env_file=tmp_path / "missing.env"))


# --- ranking order ----------------------------------------------------------------

def test_ranked_by_score_then_coverage_then_margin():
    scores = [
        fake("B0A", 70, coverage=8, margin=30),
        fake("B0B", 82),
        fake("B0C", 70, coverage=10, margin=20),
        fake("B0D", 70, coverage=10, margin=25),
    ]
    assert [s.asin for s in rank(scores).ranked] == ["B0B", "B0D", "B0C", "B0A"]


def test_incomplete_and_money_losing_products_are_kept_out_of_the_ranking():
    scores = [
        fake("B0OK", 60),
        fake("B0THIN", 95, status="Not enough data", coverage=3),
        fake("B0THIN2", 40, status="Not enough data", coverage=5),
        fake("B0LOSS1", 80, status="Not viable", profit=-3),
        fake("B0LOSS2", 80, status="Not viable", profit=-0.5),
    ]
    ranking = rank(scores)
    assert [s.asin for s in ranking.ranked] == ["B0OK"]
    assert [s.asin for s in ranking.needs_data] == ["B0THIN2", "B0THIN"]  # most data first
    assert [s.asin for s in ranking.not_viable] == ["B0LOSS2", "B0LOSS1"]  # smallest loss first


# --- filters --------------------------------------------------------------------

SAMPLE = [
    fake("B0AAA", 85, title="Silicone mat", category="Kitchen", price=D("20"), band="Strong"),
    fake("B0BBB", 60, title="Garlic press", category="Kitchen", price=D("12"), band="Promising",
         warnings=["Fragile price"]),
    fake("B0CCC", 40, title="Yoga strap", category=None, price=D("35"), band="Weak"),
    fake("B0DDD", None, status="Not enough data", coverage=2, title="Desk lamp", price=None),
]


@pytest.mark.parametrize(
    "filters, expected",
    [
        (Filters(), ["B0AAA", "B0BBB", "B0CCC", "B0DDD"]),
        (Filters(search="press"), ["B0BBB"]),
        (Filters(search="b0ccc"), ["B0CCC"]),
        (Filters(min_score=50), ["B0AAA", "B0BBB"]),  # unscored products drop out
        (Filters(bands=("Strong", "Weak")), ["B0AAA", "B0CCC"]),
        (Filters(categories=("Uncategorised",)), ["B0CCC", "B0DDD"]),
        (Filters(price_min=15, price_max=30), ["B0AAA"]),
        (Filters(hide_warnings=True), ["B0AAA", "B0CCC", "B0DDD"]),
    ],
)
def test_filters(filters, expected):
    assert [s.asin for s in apply_filters(SAMPLE, filters)] == expected


# --- tables and export --------------------------------------------------------------

def test_table_numbers_ranked_rows_and_blanks_unscored():
    frame = table(rank(SAMPLE).ranked, numbered=True)
    assert list(frame["#"]) == [1, 2, 3]
    assert list(frame["ASIN"]) == ["B0AAA", "B0BBB", "B0CCC"]
    unscored = table([SAMPLE[3]])
    assert unscored["Score"].isna().all() and unscored["Band"][0] == "Not enough data"


def test_export_has_every_factor_and_the_list_each_product_is_in():
    frame = export_frame(rank(SAMPLE))
    assert list(frame["list"]) == ["Ranked"] * 3 + ["Needs data"]
    assert list(frame["rank"][:3]) == [1, 2, 3] and frame["rank"].isna().iloc[3]
    for spec in CONFIG.factors:
        assert f"{spec.key}_points" in frame and f"{spec.key}_based_on" in frame
    assert frame.loc[1, "warnings"] == "Fragile price"


# --- bulk loading matches one-by-one loading ----------------------------------------

def test_bulk_markets_match_single_markets(engine):
    from pathlib import Path

    import_csv(engine, io.BytesIO(PRODUCTS), "products")
    import_csv(engine, io.BytesIO(COMPETITORS), "competitors")
    import_csv(engine, io.BytesIO(KEYWORDS), "keywords")
    fixtures = Path(__file__).parent / "fixtures"
    for name, kind in (("products_clean", "products"), ("keywords", "keywords"), ("competitors", "competitors")):
        import_csv(engine, fixtures / f"{name}.csv", kind)

    bulk = {m.product.asin: m for m in gather_all_markets(engine)}
    assert set(bulk) == {"B0MAIN0001", "B0TEST0001", "B0TEST0002", "0306406152"}
    for asin, market in bulk.items():
        single = gather_market(engine, asin)
        assert sorted(l.asin for l in market.listings) == sorted(l.asin for l in single.listings)
        assert sorted(k.phrase for k in market.keywords) == sorted(k.phrase for k in single.keywords)


# --- cache version -----------------------------------------------------------------

def test_data_version_changes_with_imports_costs_and_settings(engine, tmp_path):
    settings = tmp_path / "scoring.toml"
    settings.write_text("x = 1")
    first = data_version(engine, (settings,))
    import_csv(engine, io.BytesIO(PRODUCTS), "products")
    after_import = data_version(engine, (settings,))
    assert after_import != first
    save_cost(engine, "B0MAIN0001", unit_cost=5)
    after_cost = data_version(engine, (settings,))
    assert after_cost != after_import
    time.sleep(0.01)
    settings.write_text("x = 2")
    import os
    os.utime(settings, (time.time() + 5, time.time() + 5))
    assert data_version(engine, (settings,)) != after_cost
    assert data_version(engine, (settings,)) == data_version(engine, (settings,))


# --- Milestone 1: import -> costs -> score -> rank -> export ---------------------------

def test_milestone_1_end_to_end(engine):
    import_csv(engine, io.BytesIO(PRODUCTS), "products")
    import_csv(engine, io.BytesIO(COMPETITORS), "competitors")
    import_csv(engine, io.BytesIO(KEYWORDS), "keywords")
    with_costs(engine)
    extra = b"""asin,title,price,monthly_sales,review_count,rating,length_in,width_in,height_in,weight_lb
B0LOSS0001,Too cheap to sell,9.00,300,40,4.1,8,5,2,0.6
B0THIN0001,Barely researched,30.00,,,,,,,
"""
    import_csv(engine, io.BytesIO(extra), "products")
    save_cost(engine, "B0LOSS0001", fee_category="Home and Kitchen", unit_cost=6)

    ranking = rank(score_all(engine, T, A, CONFIG, today=datetime(2026, 11, 22).date()))
    assert [(s.asin, s.total, s.band) for s in ranking.ranked] == [("B0MAIN0001", 81, "Strong")]
    assert [s.asin for s in ranking.needs_data] == ["B0THIN0001"]
    assert [s.asin for s in ranking.not_viable] == ["B0LOSS0001"]
    assert ranking.not_viable[0].profit < 0

    csv = export_frame(ranking).to_csv(index=False)
    assert "B0MAIN0001" in csv and "Strong" in csv and "organic_margin_pct" in csv


# --- speed ---------------------------------------------------------------------------

def test_500_products_score_quickly(engine):
    header = "asin,title,price,monthly_sales,review_count,rating,length_in,width_in,height_in,weight_lb\n"
    rows = [f"B0PERF{i:04d},Product {i},{15 + i % 40}.99,{100 + i},{50 + i},4.{i % 9},10,6,2,0.9"
            for i in range(500)]
    import_csv(engine, io.BytesIO((header + "\n".join(rows)).encode()), "products")
    comp_rows = [f"B0CMP{i:05d},B0PERF{i % 500:04d},{200 + i},{300 + i},4.3,22" for i in range(2000)]
    import_csv(engine, io.BytesIO(("asin,competes_with,monthly_sales,review_count,rating,price\n"
                                   + "\n".join(comp_rows)).encode()), "competitors")
    for i in range(0, 500, 2):
        save_cost(engine, f"B0PERF{i:04d}", fee_category="Home and Kitchen", unit_cost=4)

    started = time.perf_counter()
    scores = score_all(engine, T, A, CONFIG)
    elapsed = time.perf_counter() - started
    assert len(scores) == 500
    assert sum(1 for s in scores if s.status == "Scored") >= 250
    assert elapsed < 20, f"scoring 500 products took {elapsed:.1f}s"
    print(f"\n500 products scored in {elapsed:.2f}s")
