"""Validation: seasonality, reviews, risk flags, decisions. All data made up."""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from test_app import import_garlic_market
from vendorus.config import load_settings
from vendorus.db import get_engine
from vendorus.fees import load_fee_tables
from vendorus.importers import ImportFileError, import_csv
from vendorus.importers.fields import parse_date, trend_metric
from vendorus.models import ResearchSnapshot, TrendPoint
from vendorus.profit import Assumptions
from vendorus.scoring import gather_all_markets, gather_market, load_scoring, score_market
from vendorus.validation import (
    analyze_season,
    complaint_phrases,
    get_decision,
    load_validation_config,
    save_decision,
    validate,
    validate_all,
)

CFG = load_validation_config()
T = load_fee_tables()


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'v.db').as_posix()}")
    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    import_garlic_market(engine)
    return engine


def check(engine, asin="B0MAIN0001"):
    market = gather_market(engine, asin)
    return validate(engine, market, score_market(market, T, Assumptions(), load_scoring()), CFG)


def titles(result):
    return [f.title for f in result.flags]


def trend_csv(values_by_month: dict[str, float], subject="garlic press", header="search volume") -> bytes:
    rows = [f"keyword,month,{header}"] + [f"{subject},{m},{v}" for m, v in values_by_month.items()]
    return "\n".join(rows).encode()


def two_years(peak=30000, normal=8000, second_year_factor=1.0) -> dict[str, float]:
    out = {}
    for year, factor in ((2024, 1.0), (2025, second_year_factor)):
        for month in range(1, 13):
            out[f"{year}-{month:02d}"] = (peak if month in (11, 12) else normal) * factor
    return out


# --- importing trends and reviews ------------------------------------------------

@pytest.mark.parametrize(
    "text, month_only, expected",
    [("2026-03-15", False, date(2026, 3, 15)), ("03/15/2026", False, date(2026, 3, 15)),
     ("Mar 15, 2026", False, date(2026, 3, 15)), ("2026-03-15T10:00:00Z", False, date(2026, 3, 15)),
     ("Mar 2026", True, date(2026, 3, 1)), ("2026-03", True, date(2026, 3, 1)), ("nonsense", True, None)],
)
def test_dates_are_read_in_common_formats(text, month_only, expected):
    assert parse_date(text, month_only=month_only) == expected


@pytest.mark.parametrize("header, metric", [("Search Volume", "search_volume"), ("Monthly Sales", "sales"),
                                            ("Units", "sales"), ("Interest", "interest")])
def test_trend_metric_comes_from_the_value_column(header, metric):
    assert trend_metric(header) == metric


def test_trend_import_stores_first_of_month_and_metric(engine):
    report = import_csv(engine, io.BytesIO(b"ASIN,Date,Units\nB0MAIN0001,2025-07-19,410\nB0MAIN0001,bad,1\n"),
                        "trends")
    assert report.rows_accepted == 1
    assert report.rejected[0].reasons == ["month 'bad' is not a month (try 2026-03-15 or Mar 2026)"]
    with Session(engine) as s:
        point = s.query(TrendPoint).one()
    assert (point.subject, point.month, point.value, point.metric) == ("B0MAIN0001", date(2025, 7, 1), 410, "sales")


def test_review_import_checks_stars(engine):
    data = b"asin,stars,date,title,review\nB0COMP0001,5,2025-01-02,Good,Works\nB0COMP0001,6,2025-01-02,X,Y\n"
    report = import_csv(engine, io.BytesIO(data), "reviews")
    assert report.rows_accepted == 1
    assert report.rejected[0].reasons == ["rating '6' must be at most 5"]


def test_reviews_need_an_asin_column(engine):
    with pytest.raises(ImportFileError, match="Required column missing: asin"):
        import_csv(engine, io.BytesIO(b"rating,body\n5,ok\n"), "reviews")


# --- seasonality -------------------------------------------------------------------------

def test_not_enough_history_is_said_not_guessed(engine):
    import_csv(engine, io.BytesIO(trend_csv({"2025-01": 100, "2025-02": 120})), "trends")
    season = check(engine).season
    assert season.label is None and "needs 12 months" in season.missing
    assert "Seasonality not checked" in titles(check(engine))


def test_highly_seasonal_and_declining(engine):
    import_csv(engine, io.BytesIO(trend_csv(two_years(second_year_factor=0.75))), "trends")
    result = check(engine)
    season = result.season
    assert season.subject == "garlic press" and season.metric == "search_volume"
    assert season.label == "Highly seasonal"
    assert season.ratio == pytest.approx(30000 / 8000)
    assert season.peak_months == ["Nov", "Dec"]
    assert season.change_pct == pytest.approx(-25)
    assert season.trend == "Declining"
    assert {"Highly seasonal", "Demand is declining"} <= set(titles(result))


def test_steady_and_growing():
    months = [(date(2024 + (i // 12), i % 12 + 1, 1), 1000 + i * 15) for i in range(24)]
    season = analyze_season(months, "x", "search_volume", CFG["seasonality"])
    assert season.label == "Steady"
    assert season.trend == "Growing"


def test_newest_import_wins_for_a_month(engine):
    import_csv(engine, io.BytesIO(trend_csv(two_years())), "trends")
    import_csv(engine, io.BytesIO(trend_csv({"2025-11": 8000, "2025-12": 8000})), "trends")
    season = check(engine).season
    assert dict(season.months)[date(2025, 11, 1)] == 8000


# --- reviews ---------------------------------------------------------------------------------

REVIEWS = """asin,rating,date,title,body
B0COMP0001,1,2025-02-01,Handle broke,The handle broke after two weeks.
B0COMP0001,2,2025-02-03,Cheap,Plastic feels cheap and the handle broke.
B0COMP0002,1,2025-02-05,Rust,Started to rust in the dishwasher.
B0COMP0002,2,2025-02-07,Rusted,Rust spots after one wash. Handle broke too.
B0COMP0003,3,2025-02-09,Hard to clean,Garlic gets stuck and it is hard to clean.
B0COMP0003,5,2025-02-11,Great,Love it.
B0COMP0004,5,2025-02-12,Great,Easy to use.
B0COMP0004,4,2025-02-13,Good,Solid.
B0OTHER001,1,2025-02-14,Unrelated,Not in this market.
"""


def test_review_analysis(engine):
    import_csv(engine, io.BytesIO(REVIEWS.encode()), "reviews")
    reviews = check(engine).reviews
    assert reviews.count == 8  # the unrelated ASIN is not in this market
    assert reviews.by_rating == {5: 2, 4: 1, 3: 1, 2: 2, 1: 2}
    assert reviews.complaint_share == pytest.approx(62.5)
    top = reviews.phrases[0]
    assert (top.text, top.reviews) == ("handle broke", 3)
    assert "handle broke" in top.example.lower()


def test_complaint_phrases_never_cross_sentences_or_repeat():
    texts = ["Handle broke. The handle broke fast", "Handle broke. Broke handle again", "nothing here"]
    phrases = [p.text for p in complaint_phrases(texts, 10)]
    assert "handle broke" in phrases
    assert "broke handle" not in phrases  # same words, other order


def test_review_velocity_needs_two_imports_apart(engine):
    result = check(engine)
    assert result.reviews.velocity == {} and "two imports" in result.reviews.velocity_note
    # Re-import a competitor 30 days later with 60 more reviews.
    import_csv(engine, io.BytesIO(b"asin,competes_with,review_count\nB0COMP0001,B0MAIN0001,2060\n"), "competitors")
    with Session(engine) as s, s.begin():
        first = s.query(ResearchSnapshot).filter_by(kind="competitors").order_by(ResearchSnapshot.id).first()
        s.execute(update(ResearchSnapshot).where(ResearchSnapshot.id == first.id)
                  .values(imported_at=datetime.now(timezone.utc) - timedelta(days=30)))
    result = check(engine)
    assert result.reviews.velocity["B0COMP0001"] == pytest.approx(60 / 30 * 30.4, rel=0.001)
    assert "Competitors gain reviews fast" in titles(result)


# --- risk flags --------------------------------------------------------------------------

def test_brand_dominance_ignores_case_and_shows_the_brand(engine):
    data = b"asin,competes_with,brand,monthly_sales\nB0COMP0001,B0MAIN0001,OXO,1000\nB0COMP0002,B0MAIN0001,oxo,600\n"
    import_csv(engine, io.BytesIO(data), "competitors")
    flag = next(f for f in check(engine).flags if f.title == "One brand dominates")
    assert flag.severity == "high"
    assert "“OXO” holds 64%" in flag.detail  # (1000 + 600) / 2500


def test_product_type_rules_match_whole_words(engine):
    data = b"asin,title,price\nB0KIDS0001,Kids toothbrush with timer,12\nB0TOYOTA01,Toyota floor mats,40\n"
    import_csv(engine, io.BytesIO(data), "products")
    assert "Children's product" in titles(check(engine, "B0KIDS0001"))
    assert "Children's product" not in titles(check(engine, "B0TOYOTA01"))  # "toy" inside "Toyota"


def test_flags_are_sorted_by_severity(engine):
    import_csv(engine, io.BytesIO(trend_csv(two_years(second_year_factor=0.75))), "trends")
    severities = [f.severity for f in check(engine).flags]
    order = {"high": 0, "medium": 1, "info": 2}
    assert severities == sorted(severities, key=order.get)


def test_validation_never_changes_the_score(engine):
    market = gather_market(engine, "B0MAIN0001")
    before = score_market(market, T, Assumptions(), load_scoring()).total
    import_csv(engine, io.BytesIO(trend_csv(two_years(second_year_factor=0.5))), "trends")
    import_csv(engine, io.BytesIO(REVIEWS.encode()), "reviews")
    market = gather_market(engine, "B0MAIN0001")
    assert score_market(market, T, Assumptions(), load_scoring()).total == before == 81


# --- decisions and bulk -------------------------------------------------------------------

def test_decision_and_checklist_are_saved_per_asin(engine):
    assert get_decision(engine, "B0MAIN0001").decision == "Undecided"
    item = CFG["checklist"]["items"][0]
    save_decision(engine, "B0MAIN0001", "Watch", {item: True}, "Wait for samples")
    save_decision(engine, "B0MAIN0001", "Go", {item: True}, "Samples good")
    saved = get_decision(engine, "B0MAIN0001")
    assert (saved.decision, saved.checklist, saved.notes) == ("Go", {item: True}, "Samples good")
    with pytest.raises(ValueError):
        save_decision(engine, "B0MAIN0001", "Maybe", {})


def test_bulk_validation_matches_single(engine):
    import_csv(engine, io.BytesIO(trend_csv(two_years(second_year_factor=0.75))), "trends")
    import_csv(engine, io.BytesIO(REVIEWS.encode()), "reviews")
    markets = gather_all_markets(engine)
    scores = [score_market(m, T, Assumptions(), load_scoring()) for m in markets]
    bulk = validate_all(engine, markets, scores, CFG)["B0MAIN0001"]
    single = check(engine)
    assert titles(bulk) == titles(single)
    assert bulk.season.ratio == single.season.ratio
    assert bulk.reviews.count == single.reviews.count
