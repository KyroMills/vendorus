"""Profit per unit, organic-first. All products here are made up."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

import pytest

from vendorus.config import load_settings
from vendorus.costs import get_cost, latest_products, save_cost, suggest_fee_category
from vendorus.db import get_engine
from vendorus.fees import load_fee_tables
from vendorus.importers import import_csv
from vendorus.profit import (
    NOT_INCLUDED,
    ORGANIC_NOTICE,
    Assumptions,
    ProductInputs,
    analyze,
    break_even_price,
    load_planning,
    scenario,
)

T = load_fee_tables()
P = T.period("2027-non-peak")
A = Assumptions()  # returns 5%, storage 2 months blended, ads 0%

MAT = ProductInputs(
    price=D("24.99"),
    fee_category="Home and Kitchen",
    length_in=D(10), width_in=D(6), height_in=D(2), weight_lb=D("0.9"),
    unit_cost=D("5.00"), duty_pct=D(10), freight_per_unit=D("1.20"), prep_per_unit=D("0.30"),
)


def test_planning_defaults_are_organic_first():
    planning = load_planning()
    assert planning.ad_spend_pct == 0
    assert planning.returns_pct == 5


def test_worked_example_by_hand():
    """Worked by hand:
    referral 15% x 24.99 = 3.75; large standard 14.4 oz, $10-50 -> 4.67;
    surcharge 3.5% -> 0.16; storage 0.0694 ft3 x 1.185 x 2 months -> 0.16;
    landed 5.00 + 10% duty + 1.20 + 0.30 = 7.00;
    returns 5% x (7.00 + 4.67 + 0.16 + 0.75 refund admin) = 0.63.
    24.99 - 3.75 - 4.67 - 0.16 - 0.16 - 7.00 - 0.63 = 8.62
    """
    s = scenario(T, P, MAT, A)
    assert dict(s.lines) == {
        "Referral fee": D("3.75"),
        "FBA fulfillment fee": D("4.67"),
        "Fuel and logistics surcharge": D("0.16"),
        "Storage": D("0.16"),
        "Landed cost": D("7.00"),
        "Returns allowance (5%)": D("0.63"),
    }
    assert s.profit == D("8.62")
    assert s.margin_pct == D("34.5")
    assert s.roi_pct == D("123.1")
    assert s.name == "Organic (no ads)"


def test_ads_are_off_by_default_and_shown_side_by_side_when_on():
    off = analyze(T, P, MAT, A)
    assert off.with_ads is None
    assert not any("Advertising" in label for label, _ in off.organic.lines)

    on = analyze(T, P, MAT, replace(A, ad_spend_pct=D(10)), ads_enabled=True)
    assert on.organic.profit == D("8.62")  # organic result is unchanged
    assert on.with_ads.profit == D("6.12")  # 10% of 24.99 = 2.50 less
    assert on.with_ads.name == "With ads (10%)"
    assert on.break_even_with_ads > on.break_even_organic


def test_returns_rate_is_configurable():
    none = scenario(T, P, MAT, replace(A, returns_pct=D(0)))
    more = scenario(T, P, MAT, replace(A, returns_pct=D(10)))
    assert none.profit > scenario(T, P, MAT, A).profit > more.profit


def test_break_even_is_the_exact_cent():
    be = break_even_price(T, P, MAT, A)
    assert be == D("14.82")
    assert scenario(T, P, replace(MAT, price=be), A).profit >= 0
    assert scenario(T, P, replace(MAT, price=be - D("0.01")), A).profit < 0


def test_no_break_even_when_costs_are_too_high():
    expensive = replace(MAT, unit_cost=D("500"))
    assert break_even_price(T, P, expensive, A, ceiling=D("100")) is None


def test_missing_dimensions_are_not_guessed():
    result = analyze(T, P, replace(MAT, weight_lb=None), A)
    assert result.organic.profit is None
    assert any("dimensions and weight" in m for m in result.missing)
    assert result.fees.referral == D("3.75")  # what can be calculated still is


def test_missing_unit_cost_shows_fees_but_no_profit():
    result = analyze(T, P, replace(MAT, unit_cost=None), A)
    assert result.organic.profit is None
    assert result.fees.complete
    assert any("unit cost" in m for m in result.missing)


@pytest.mark.parametrize("flag", ["apparel", "dangerous_goods"])
def test_unsupported_rate_cards_are_flagged(flag):
    result = analyze(T, P, replace(MAT, **{flag: True}), A)
    assert result.organic.profit is None
    assert any("rate cards" in m for m in result.missing)


def test_no_category_means_no_referral_fee():
    result = analyze(T, P, replace(MAT, fee_category=None), A)
    assert result.organic.profit is None
    assert "Choose a referral fee category." in result.missing


def test_peak_fees_lower_profit():
    peak = scenario(T, T.period("2026-peak"), MAT, A)
    assert peak.profit < scenario(T, P, MAT, A).profit


def test_results_carry_the_organic_warning_and_exclusions():
    assert "not guaranteed" in ORGANIC_NOTICE
    assert any("incentives" in item for item in NOT_INCLUDED)


@pytest.mark.parametrize(
    "store_category, expected",
    [("Home & Kitchen", "Home and Kitchen"), ("Toys & Games", "Toys and Games"),
     ("Kitchen", None), (None, None), ("Beauty", "Beauty, Health, and Personal Care")],
)
def test_fee_category_suggestions_never_guess(store_category, expected):
    assert suggest_fee_category(T, store_category) == expected


def test_costs_are_saved_per_asin_and_survive_reimport(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    fixture = Path(__file__).parent / "fixtures" / "products_clean.csv"
    import_csv(engine, fixture, "products")

    save_cost(engine, "B0TEST0001", unit_cost=D("4.10"), ads_enabled=True, ad_spend_pct=12)
    save_cost(engine, "B0TEST0001", freight_per_unit=1)  # second save updates, doesn't replace
    import_csv(engine, fixture, "products")  # re-import

    cost = get_cost(engine, "B0TEST0001")
    assert (cost.unit_cost, cost.freight_per_unit, cost.ads_enabled) == (4.10, 1.0, True)
    assert [p.asin for p in latest_products(engine)] == ["B0TEST0001", "B0TEST0002", "0306406152"]
    with pytest.raises(ValueError):
        save_cost(engine, "B0TEST0001", price=5)
