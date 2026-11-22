"""Suppliers, quotes and landed cost. All suppliers and prices are made up."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from test_app import import_garlic_market
from vendorus.config import load_settings
from vendorus.costs import get_cost
from vendorus.db import get_engine
from vendorus.fees import load_fee_tables
from vendorus.profit import Assumptions
from vendorus.scoring import load_scoring, score_product
from vendorus.sourcing import (
    apply_quote,
    compare_quotes,
    delete_quote,
    delete_supplier,
    get_quote,
    landed_cost,
    list_suppliers,
    load_sourcing_config,
    quotes_for,
    save_quote,
    save_supplier,
    unit_price_at,
)

CFG = load_sourcing_config()
T = load_fee_tables()
A = Assumptions()
TODAY = date(2026, 11, 22)

NINGBO = dict(
    asin="B0MAIN0001", currency="CNY", exchange_rate=0.14, unit_price=28, moq=500, price_breaks=[[1000, 25]],
    order_qty=1000, incoterm="FOB", packaging_per_unit=2, tooling_cost=1400, sample_cost=300,
    shipping_method="Sea", freight_mode="per_shipment", freight_amount=700, transit_days=35, duty_pct=4,
    broker_fee=150, inspection_fee=250, production_days=30, valid_until=date(2026, 12, 31),
)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'q.db').as_posix()}")
    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    import_garlic_market(engine)  # B0MAIN0001 scores 81 with its default $7.00 landed cost
    return engine


@pytest.fixture
def ningbo(engine):
    supplier = save_supplier(engine, name="Ningbo Kitchenware Co.", country="China", platform="Alibaba")
    return save_quote(engine, supplier_id=supplier, **NINGBO)


# --- suppliers ----------------------------------------------------------------------

def test_supplier_needs_a_name(engine):
    with pytest.raises(ValueError, match="needs a name"):
        save_supplier(engine, name="  ")


def test_suppliers_list_with_quote_counts(engine, ningbo):
    other = save_supplier(engine, name="Ohio Housewares", country="USA", status="Contacted")
    counts = {s.name: n for s, n in list_suppliers(engine)}
    assert counts == {"Ningbo Kitchenware Co.": 1, "Ohio Housewares": 0}
    save_supplier(engine, other, status="Approved")
    assert next(s for s, _ in list_suppliers(engine) if s.id == other).status == "Approved"


def test_deleting_a_supplier_removes_its_quotes_but_keeps_copied_costs(engine, ningbo):
    apply_quote(engine, ningbo, CFG)
    supplier_id = get_quote(engine, ningbo).supplier_id
    delete_supplier(engine, supplier_id)
    assert quotes_for(engine, "B0MAIN0001") == []
    cost = get_cost(engine, "B0MAIN0001")
    assert cost.quote_id is None and cost.unit_cost == 3.5


# --- landed cost, worked by hand ------------------------------------------------------

def test_landed_cost_worked_by_hand(engine, ningbo):
    """1,000 units qualify for the 25 CNY break; CNY at 0.14 USD.
    Product 25 x 0.14 = 3.50; packaging 2 x 0.14 = 0.28
    Freight $700 / 1,000 = 0.70; duty 4% x 3.50 = 0.14
    Fees ($150 broker + $250 inspection) / 1,000 = 0.40
    Tooling 1,400 x 0.14 = $196 / 1,000 = 0.196 -> 0.20
    Landed 5.22; cash = 5.22 x 1,000 + samples 300 x 0.14 = 5,262.00
    Days to Amazon = 30 production + 35 transit + 14 receiving = 79
    """
    landed = landed_cost(get_quote(engine, ningbo), CFG, today=TODAY)
    assert dict(landed.lines) == {
        "Product": D("3.50"), "Packaging and prep": D("0.28"), "Freight": D("0.70"), "Duty": D("0.14"),
        "Broker, inspection, other fees": D("0.40"), "Tooling (first order)": D("0.20"),
    }
    assert landed.per_unit == D("5.22")
    assert landed.cash_needed == D("5262.00")
    assert landed.days_to_amazon == 79
    assert landed.warnings == []


def test_smaller_order_loses_the_price_break_and_spreads_costs_thinner(engine, ningbo):
    quote = get_quote(engine, ningbo)
    assert unit_price_at(quote, 999) == D("28") and unit_price_at(quote, 1000) == D("25")
    # 500 units: 3.92 + 0.28 + 1.40 + 0.16 + 0.80 + 0.39
    assert landed_cost(quote, CFG, quantity=500, today=TODAY).per_unit == D("6.95")


def test_order_below_moq_is_flagged(engine, ningbo):
    warnings = landed_cost(get_quote(engine, ningbo), CFG, quantity=300, today=TODAY).warnings
    assert "Order of 300 is below the minimum order of 500." in warnings


def test_ddp_includes_freight_and_duty(engine):
    supplier = save_supplier(engine, name="Ohio Housewares", country="USA")
    quote = save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=6.10, order_qty=300,
                       incoterm="DDP", freight_amount=100, duty_pct=5)
    landed = landed_cost(get_quote(engine, quote), CFG, today=TODAY)
    assert landed.per_unit == D("6.10")
    assert any("Freight entered but ignored" in w for w in landed.warnings)
    assert any("Duty entered but ignored" in w for w in landed.warnings)


def test_cif_includes_freight_but_not_duty(engine):
    supplier = save_supplier(engine, name="Port supplier")
    quote = save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=4, order_qty=100,
                       incoterm="CIF", freight_amount=500, duty_pct=10)
    landed = landed_cost(get_quote(engine, quote), CFG, today=TODAY)
    assert (landed.freight, landed.duty) == (D("0"), D("0.40"))


def test_per_unit_freight(engine):
    supplier = save_supplier(engine, name="Air shipper")
    quote = save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=4, order_qty=100,
                       freight_mode="per_unit", freight_amount=1.25)
    assert landed_cost(get_quote(engine, quote), CFG, today=TODAY).freight == D("1.25")


def test_missing_duty_freight_and_expired_quotes_are_flagged(engine):
    supplier = save_supplier(engine, name="Vague supplier")
    quote = save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=4, order_qty=100,
                       valid_until=date(2026, 1, 31))
    warnings = landed_cost(get_quote(engine, quote), CFG, today=TODAY).warnings
    assert any("expired on Jan 31, 2026" in w for w in warnings)
    assert any("No duty rate entered" in w for w in warnings)
    assert any("No freight entered" in w for w in warnings)


def test_quotes_need_a_price_and_a_positive_rate(engine):
    supplier = save_supplier(engine, name="X")
    with pytest.raises(ValueError, match="unit price"):
        save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=-1)
    with pytest.raises(ValueError, match="exchange rate"):
        save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=1, currency="CNY", exchange_rate=0)


def test_usd_quotes_ignore_an_exchange_rate(engine):
    supplier = save_supplier(engine, name="X")
    quote = save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=5, exchange_rate=7.2)
    assert get_quote(engine, quote).exchange_rate == 1.0


# --- comparing and using quotes -------------------------------------------------------------

def test_compare_shows_profit_and_score_per_quote_cheapest_first(engine, ningbo):
    supplier = save_supplier(engine, name="Ohio Housewares", country="USA")
    save_quote(engine, supplier_id=supplier, asin="B0MAIN0001", unit_price=6.10, order_qty=300, incoterm="DDP")
    results = compare_quotes(engine, "B0MAIN0001", T, A, load_scoring(), CFG, today=TODAY)
    assert [r.quote.supplier.name for r in results] == ["Ningbo Kitchenware Co.", "Ohio Housewares"]
    ningbo_result = results[0]
    # 25.00 - 3.75 referral - 4.67 FBA - 0.16 surcharge - 0.16 storage - 5.22 landed
    #   - 0.54 returns (5% of 5.22 + 4.67 + 0.16 + 0.75) = 10.50
    assert ningbo_result.profit == D("10.50")
    assert ningbo_result.margin_pct == D("42.0")
    assert ningbo_result.score is not None and ningbo_result.score > 81  # cheaper than the $7.00 assumed before
    assert results[1].profit < ningbo_result.profit
    assert not any(r.in_use for r in results)


def test_comparing_does_not_change_saved_costs(engine, ningbo):
    before = get_cost(engine, "B0MAIN0001").unit_cost
    compare_quotes(engine, "B0MAIN0001", T, A, load_scoring(), CFG, today=TODAY)
    assert get_cost(engine, "B0MAIN0001").unit_cost == before == 5


def test_using_a_quote_updates_costs_profit_and_score(engine, ningbo):
    before = score_product(engine, "B0MAIN0001", T, A, load_scoring(), TODAY).total
    apply_quote(engine, ningbo, CFG)
    cost = get_cost(engine, "B0MAIN0001")
    assert cost.quote_id == ningbo
    # Same landed cost through the Profit page's formula:
    # 3.50 x (1 + 4%) + 0.70 freight + 0.28 prep + 0.60 other = 5.22
    assert (cost.unit_cost, cost.duty_pct, cost.freight_per_unit, cost.prep_per_unit, cost.other_per_unit) == \
        (3.5, 4.0, 0.7, 0.28, 0.6)
    after = score_product(engine, "B0MAIN0001", T, A, load_scoring(), TODAY)
    assert after.total > before
    assert after.profit == D("10.50")
    result = compare_quotes(engine, "B0MAIN0001", T, A, load_scoring(), CFG, today=TODAY)[0]
    assert result.in_use


def test_deleting_the_quote_in_use_keeps_costs(engine, ningbo):
    apply_quote(engine, ningbo, CFG)
    delete_quote(engine, ningbo)
    cost = get_cost(engine, "B0MAIN0001")
    assert cost.quote_id is None and cost.unit_cost == 3.5
