"""Opportunity scoring. All products and numbers here are made up."""
from __future__ import annotations

import io
from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from vendorus.config import load_settings
from vendorus.costs import save_cost
from vendorus.db import get_engine
from vendorus.fees import load_fee_tables
from vendorus.importers import import_csv
from vendorus.profit import Assumptions
from vendorus.scoring import band_scale, load_scoring, scale, score_product

T = load_fee_tables()
A = Assumptions()
CONFIG = load_scoring()
TODAY = date(2026, 11, 21)  # fees: 2027 non-peak

PRODUCTS = b"""asin,title,price,monthly_sales,review_count,rating,length_in,width_in,height_in,weight_lb
B0MAIN0001,Garlic press,25.00,400,120,4.2,10,6,2,0.9
"""
COMPETITORS = b"""asin,competes_with,monthly_sales,review_count,rating,price
B0COMP0001,B0MAIN0001,1000,2000,4.6,22
B0COMP0002,B0MAIN0001,600,300,4.3,24
B0COMP0003,garlic press,300,80,4.0,19
B0COMP0004,B0MAIN0001,200,50,3.9,27
"""
KEYWORDS = b"""phrase,search_volume,competing_products,product_asin
garlic press,20000,1000,B0MAIN0001
garlic crusher,3000,400,B0MAIN0001
"""


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    import_csv(engine, io.BytesIO(PRODUCTS), "products")
    import_csv(engine, io.BytesIO(COMPETITORS), "competitors")
    import_csv(engine, io.BytesIO(KEYWORDS), "keywords")
    return engine


def with_costs(engine, **overrides):
    values = dict(fee_category="Home and Kitchen", unit_cost=5, duty_pct=10,
                  freight_per_unit=1.20, prep_per_unit=0.30)
    values.update(overrides)
    save_cost(engine, "B0MAIN0001", **values)


def score(engine, today=TODAY, config=CONFIG):
    return score_product(engine, "B0MAIN0001", T, A, config, today)


def points(result):
    return {f.spec.key: f.points for f in result.factors}


# --- the points scale ---------------------------------------------------------

def test_scale_both_directions_and_clamped():
    assert scale(350, 100, 600) == 0.5
    assert scale(50, 100, 600) == 0 and scale(900, 100, 600) == 1
    assert scale(4.3, 4.6, 4.0) == pytest.approx(0.5)  # lower is better


def test_log_scale_values_small_numbers_more():
    # 150 -> 1500 reviews on a log scale: halfway is about 474, not 825.
    assert scale(474, 1500, 150, log=True) == pytest.approx(0.5, abs=0.01)


@pytest.mark.parametrize("price, fraction", [(5, 0), (15, 0.5), (20, 1), (45, 1), (60, 1), (90, 0.5), (150, 0)])
def test_price_band(price, fraction):
    assert band_scale(price, CONFIG.spec("price_point")) == pytest.approx(fraction)


def test_settings_have_ten_factors_worth_100_points():
    assert len(CONFIG.factors) == 10
    assert sum(f.weight for f in CONFIG.factors) == 100
    assert all(f.why for f in CONFIG.factors)


# --- a full market, worked by hand ---------------------------------------------

def test_fully_researched_product_worked_by_hand(engine):
    """Listings: the product + 4 competitors (one linked via the keyword).
    Sales 1000, 600, 400, 300, 200 -> mean 500 -> (500-100)/500 = 0.8 -> 8.0
    Main keyword 20,000 searches -> 10.0; 1,000 / 20,000 x 1,000 = 50 -> 10.0
    Reviews 2000, 300, 120, 80, 50 -> median 120 (<= 150) -> 10.0
    Ratings average 4.2 -> (4.6-4.2)/0.6 -> 6.7
    Top 3 hold 2000/2500 = 80% -> (85-80)/35 -> 1.4
    Profit (Phase 2): 25.00 - 3.75 - 4.67 - 0.16 - 0.16 - 7.00 - 0.63 = 8.63
      margin 34.5% -> 9.8; ROI 123.3% -> 7.3; price $25 -> 10.0
    Fees 8.74 / 25 = 35.0% -> (50-35)/20 -> 7.5
    Total 80.7 of 100 -> 81, Strong.
    """
    with_costs(engine)
    result = score(engine)
    assert points(result) == {
        "market_demand": 8.0, "search_demand": 10.0, "review_barrier": 10.0,
        "competition_density": 10.0, "rating_gap": 6.7, "sales_spread": 1.4,
        "margin": 9.8, "roi": 7.3, "price_point": 10.0, "fee_share": 7.5,
    }
    assert result.total == 81
    assert (result.status, result.band) == ("Scored", "Strong")
    assert result.coverage_text == "10 of 10 factors"
    assert result.listings == 5
    assert result.main_keyword == "garlic press"
    assert result.warnings == []


def test_each_factor_shows_the_number_behind_it(engine):
    with_costs(engine)
    values = {f.spec.key: f.value for f in score(engine).factors}
    assert values["market_demand"] == "500 sales/month"
    assert values["review_barrier"] == "median 120 reviews"
    assert values["sales_spread"] == "top 3 listings hold 80% of sales"
    assert values["margin"] == "34.5% margin"


# --- missing data ---------------------------------------------------------------

def test_without_costs_profit_factors_are_left_out_not_guessed(engine):
    result = score(engine)
    missing = {f.spec.key: f.missing for f in result.factors if not f.scored}
    assert set(missing) == {"margin", "roi", "fee_share"}
    assert "Choose a referral fee category." in missing["margin"]
    assert result.coverage == 7
    # Scaled over the 7 scored factors: (8 + 10 + 10 + 10 + 6.7 + 1.4 + 10) / 70 -> 80
    assert result.total == 80
    assert result.status == "Scored"


def test_too_little_data_is_not_ranked(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'thin.db').as_posix()}")
    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    import_csv(engine, io.BytesIO(PRODUCTS), "products")  # no competitors or keywords
    result = score_product(engine, "B0MAIN0001", T, A, CONFIG, TODAY)
    assert result.status == "Not enough data"
    assert result.band is None
    assert result.coverage == 1  # only price point
    market = next(f for f in result.factors if f.spec.key == "market_demand")
    assert "Import competitors" in market.missing


# --- hard stops and warnings ------------------------------------------------------

def test_money_losing_product_is_not_viable_whatever_the_score(engine):
    with_costs(engine, unit_cost=30)
    result = score(engine)
    assert result.status == "Not viable"
    assert result.band is None
    assert result.warnings[0].startswith("Loses money with no advertising")


def test_fragile_price_warning(engine):
    with_costs(engine, unit_cost=12)  # break-even close to the $25 price
    result = score(engine)
    assert result.status == "Scored"
    assert any(w.startswith("Fragile price") for w in result.warnings)


def test_old_research_gets_a_warning(engine):
    with_costs(engine)
    result = score(engine, today=date(2027, 6, 1))
    assert any("days old" in w for w in result.warnings)


def test_weights_and_limits_come_from_settings(engine):
    with_costs(engine)
    heavier = replace(CONFIG, factors=tuple(
        replace(f, weight=20) if f.key == "sales_spread" else f for f in CONFIG.factors
    ))
    # Sales spread scores poorly here, so doubling its weight lowers the total.
    assert score(engine, config=heavier).total < score(engine).total


def test_newest_competitor_data_wins(engine):
    newer = b"asin,competes_with,monthly_sales,review_count,rating\nB0COMP0001,B0MAIN0001,1000,9000,4.6\n"
    import_csv(engine, io.BytesIO(newer), "competitors")
    reviews = next(f for f in score(engine).factors if f.spec.key == "review_barrier")
    assert reviews.value == "median 120 reviews"  # still 5 listings, one updated
    assert score(engine).listings == 5
