"""Amazon fee calculations, checked against Amazon's own published numbers."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from vendorus.fees import (
    Package,
    fuel_surcharge,
    fulfillment_fee,
    load_fee_tables,
    price_column,
    referral_fee,
    refund_administration_fee,
    size_tier,
    storage_rate,
)

T = load_fee_tables()
PERIODS = ("2026-non-peak", "2026-peak", "2027-non-peak")


def fee(period_id, dims, weight_lb, price, category="Home and Kitchen"):
    period = T.period(period_id)
    size = size_tier(T, period, Package(*(D(str(x)) for x in dims), D(str(weight_lb))))
    return fulfillment_fee(period, size, price_column(D(str(price)), category, period)), size


# Amazon's worked examples on the "2027 US FBA fulfillment fee changes" page
# (sources.fulfillment in us.toml): dimensions, unit weight, price band, and
# the fee Amazon states for each period.
AMAZON_EXAMPLES = [
    ("mobile device case", (13.8, 9, 0.7), D("2.88") / 16, 8, "Electronics Accessories",
     ("2.49", "2.68", "2.72"), "small_standard"),
    ("t-shirt", (13, 9, 0.85), D("5.40") / 16, 20, "Everything Else",
     ("4.20", "4.48", "4.26"), "large_standard"),
    ("iron", (12.6, 6.6, 5.5), D("3.35"), 30, "Home and Kitchen",
     ("7.13", "7.67", "7.32"), "large_standard"),
    ("baby cot", (24, 7.5, 6), D("7.90"), 60, "Baby Products",
     ("10.21", "11.25", "10.24"), "small_bulky"),
    ("monitor", (54, 34, 3.5), D("41"), 40, "Electronics Accessories",
     ("43.81", "46.54", "27.10"), None),
    ("tv", (65, 20, 7), D("62"), 60, "Consumer Electronics",
     ("48.57", "51.38", "36.55"), None),
]


@pytest.mark.parametrize("name, dims, weight, price, category, expected, tier", AMAZON_EXAMPLES)
@pytest.mark.parametrize("index, period_id", list(enumerate(PERIODS)))
def test_matches_amazon_examples(name, dims, weight, price, category, expected, tier, index, period_id):
    got, size = fee(period_id, dims, weight, price, category)
    assert got == D(expected[index]), f"{name} in {period_id}"
    if tier:
        assert size.tier.name == tier


def test_new_2027_oversize_tiers():
    assert fee("2026-non-peak", (54, 34, 3.5), 41, 40)[1].tier.name == "extra_large_0_50"
    assert fee("2027-non-peak", (54, 34, 3.5), 41, 40)[1].tier.name == "small_oversize"
    assert fee("2027-non-peak", (65, 20, 7), 62, 60)[1].tier.name == "medium_oversize"


@pytest.mark.parametrize(
    "dims, weight, tier",
    [
        ((15, 12, 0.75), 1, "small_standard"),  # exactly at every limit
        ((15, 12, 0.76), 0.5, "large_standard"),  # just too thick
        ((10, 8, 0.5), 1.01, "large_standard"),  # just too heavy
        ((18, 14, 8), 20, "large_standard"),
        ((18.1, 14, 8), 10, "small_bulky"),
        ((30, 20, 10), 10, "medium_bulky"),  # 2027 only: longest > 28
    ],
)
def test_size_tier_boundaries_2027(dims, weight, tier):
    assert fee("2027-non-peak", dims, weight, 25)[1].tier.name == tier


def test_dimensional_weight_can_raise_the_fee():
    # Light but bulky box: 14 x 12 x 6 = 1008 in3 / 139 = 7.25 lb dimensional weight.
    light, size = fee("2027-non-peak", (14, 12, 6), 1, 25)
    assert size.shipping_weight_lb > 7 and light > D("7")


def test_small_standard_uses_unit_weight_not_dimensional():
    _, size = fee("2027-non-peak", (14, 11, 0.7), D("0.25"), 25)
    assert size.tier.name == "small_standard"
    assert size.shipping_weight_lb == D("0.25")


def test_large_standard_over_3lb_charges_each_started_4oz():
    # 3.01 lb is one started 4 oz interval above 3 lb. (8 x 6 x 4 in keeps the
    # dimensional weight, 1.38 lb, below the unit weight.)
    got, _ = fee("2027-non-peak", (8, 6, 4), D("3.01"), 25)
    assert got == D("7.16") + D("0.08")
    # A bigger box with the same weight is charged on dimensional weight instead.
    heavier, size = fee("2027-non-peak", (12, 10, 6), D("3.01"), 25)
    assert size.shipping_weight_lb > D("5") and heavier > got


@pytest.mark.parametrize(
    "price, category, period_id, column",
    [
        (D("9.99"), "Home and Kitchen", "2027-non-peak", 0),
        (D("10.00"), "Home and Kitchen", "2027-non-peak", 1),  # exactly $10 is the middle band
        (D("50.00"), "Home and Kitchen", "2027-non-peak", 1),
        (D("50.01"), "Home and Kitchen", "2027-non-peak", 2),
        (D("9.99"), "Computers", "2027-non-peak", 1),  # no low-price rates from 2027
        (D("9.99"), "Computers", "2026-peak", 0),  # still eligible until Jan 14, 2027
    ],
)
def test_price_bands(price, category, period_id, column):
    assert price_column(price, category, T.period(period_id)) == column


@pytest.mark.parametrize(
    "category, price, expected",
    [
        ("Home and Kitchen", "24.99", "3.75"),
        ("Home and Kitchen", "1.00", "0.30"),  # minimum fee
        ("Beauty, Health, and Personal Care", "10.00", "0.80"),  # whole price <= $10: 8%
        ("Beauty, Health, and Personal Care", "10.01", "1.50"),  # whole price > $10: 15%
        ("Clothing and Accessories", "15.00", "0.75"),
        ("Clothing and Accessories", "18.00", "1.80"),
        ("Clothing and Accessories", "25.00", "4.25"),
        ("Jewelry", "300.00", "52.50"),  # 20% of 250 + 5% of 50
        ("Electronics Accessories", "150.00", "19.00"),  # 15% of 100 + 8% of 50
        ("Lawn Mowers and Snow Throwers", "600.00", "48.00"),  # whole price > $500: 8%
        ("Fine Art", "2.00", "1.00"),  # Fine Art minimum is $1.00
        ("Grocery and Gourmet", "2.00", "0.16"),  # no minimum
    ],
)
def test_referral_fees(category, price, expected):
    assert referral_fee(T, category, D(price)) == D(expected)


def test_media_has_closing_fee():
    assert T.referral["Media - Books, DVD, Music, Software, Video"].closing_fee == D("1.80")


def test_fuel_surcharge_is_3_5_percent_of_fulfillment_fee():
    assert fuel_surcharge(T, T.period("2027-non-peak"), D("4.67")) == D("0.16")


def test_storage_rates():
    assert storage_rate(T, True, "off_peak") == D("0.78")
    assert storage_rate(T, True, "peak") == D("2.40")
    assert storage_rate(T, True, "blended") == D("1.185")
    assert storage_rate(T, False, "off_peak") == D("0.56")


def test_refund_administration_fee():
    assert refund_administration_fee(D("3.75")) == D("0.75")
    assert refund_administration_fee(D("40.00")) == D("5.00")


def test_default_period_is_everyday_fees_in_force_or_next():
    assert T.default_period(date(2026, 11, 21)).id == "2027-non-peak"  # peak now, plan on 2027
    assert T.default_period(date(2026, 6, 1)).id == "2026-non-peak"
    assert T.period_for(date(2026, 11, 21)).id == "2026-peak"


def test_fee_table_knows_when_it_is_out_of_date():
    assert not T.is_out_of_date(date(2026, 11, 21))
    assert T.is_out_of_date(date(2027, 10, 15))


def test_fee_table_records_its_sources():
    assert T.retrieved == date(2026, 11, 21)
    assert all(url.startswith("https://") for url in T.sources.values())


def test_invalid_package_is_refused():
    with pytest.raises(ValueError):
        Package(D(0), D(1), D(1), D(1))
