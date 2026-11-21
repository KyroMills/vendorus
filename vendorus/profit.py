"""Per-unit profit for an FBA product: Amazon fees, landed cost, margins.

Organic-first: advertising is an optional scenario that is off by default.
Results are "profit per unit IF a unit sells at this price". They never
assume that a new listing will rank or sell.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

from vendorus.config import PROJECT_ROOT
from vendorus.fees import (
    FeeTables,
    Package,
    Period,
    fuel_surcharge,
    fulfillment_fee,
    money,
    price_column,
    referral_fee,
    refund_administration_fee,
    size_tier,
    storage_rate,
)
from vendorus.fees.tables import dec

PLANNING_FILE = PROJECT_ROOT / "config" / "planning.toml"
ZERO = Decimal("0")

ORGANIC_NOTICE = (
    "Organic ranking and sales are not guaranteed. A new listing starts with no sales history, "
    "reviews or ranking, and it can take months to rank organically; some products never do. "
    "These figures show profit per unit if a unit sells at this price, not that it will sell."
)

# Costs this engine does not include, shown next to every result.
NOT_INCLUDED = (
    "Holiday peak fulfillment fees (Oct 15 - Jan 14), unless the peak fee period is selected",
    "Storage for stock older than 180 days (much higher rates)",
    "Inbound placement service fee (depends on how you ship to Amazon)",
    "Low-inventory-level fee",
    "Returns processing fee (only for categories with high return rates)",
    "Oversize handling fees (flat product, overhang, overmax)",
    "Coupons, deals, promotions and Amazon Vine",
    "Professional selling plan ($39.99/month, account level)",
    "Income tax, and sales tax collection",
    "New-seller incentives and New Selection credits (left out so results are not flattered)",
)


@dataclass(frozen=True)
class Assumptions:
    returns_pct: Decimal = Decimal("5")
    storage_months: Decimal = Decimal("2")
    storage_season: str = "blended"
    ad_spend_pct: Decimal = ZERO
    professional_plan_monthly: Decimal = Decimal("39.99")


def load_planning(path: Path = PLANNING_FILE) -> Assumptions:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return Assumptions(
        returns_pct=dec(raw.get("returns_pct", 5)),
        storage_months=dec(raw.get("storage_months", 2)),
        storage_season=raw.get("storage_season", "blended"),
        ad_spend_pct=dec(raw.get("ad_spend_pct", 0)),
        professional_plan_monthly=dec(raw.get("professional_plan_monthly", "39.99")),
    )


@dataclass(frozen=True)
class ProductInputs:
    price: Decimal
    fee_category: str | None
    length_in: Decimal | None = None
    width_in: Decimal | None = None
    height_in: Decimal | None = None
    weight_lb: Decimal | None = None
    unit_cost: Decimal | None = None
    freight_per_unit: Decimal = ZERO
    duty_pct: Decimal = ZERO  # percent of unit cost
    prep_per_unit: Decimal = ZERO
    other_per_unit: Decimal = ZERO
    apparel: bool = False
    dangerous_goods: bool = False

    @property
    def package(self) -> Package | None:
        values = (self.length_in, self.width_in, self.height_in, self.weight_lb)
        if any(v is None or v <= 0 for v in values):
            return None
        return Package(*values)

    @property
    def landed_cost(self) -> Decimal | None:
        if self.unit_cost is None:
            return None
        duty = self.unit_cost * self.duty_pct / 100
        return money(self.unit_cost + duty + self.freight_per_unit + self.prep_per_unit + self.other_per_unit)


@dataclass
class AmazonFees:
    period: Period
    referral: Decimal | None = None
    closing: Decimal = ZERO
    fulfillment: Decimal | None = None
    surcharge: Decimal = ZERO
    storage: Decimal | None = None
    refund_admin: Decimal | None = None
    size_tier: str | None = None
    shipping_weight_lb: Decimal | None = None
    problems: list[str] = field(default_factory=list)  # why a fee is missing
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return None not in (self.referral, self.fulfillment, self.storage)

    @property
    def total(self) -> Decimal | None:
        if not self.complete:
            return None
        return self.referral + self.closing + self.fulfillment + self.surcharge + self.storage


def amazon_fees(
    tables: FeeTables, period: Period, inputs: ProductInputs, assumptions: Assumptions
) -> AmazonFees:
    fees = AmazonFees(period=period)
    category = inputs.fee_category
    if category not in tables.referral:
        fees.problems.append("Choose a referral fee category.")
    else:
        fees.referral = referral_fee(tables, category, inputs.price)
        fees.closing = tables.referral[category].closing_fee
        fees.refund_admin = refund_administration_fee(fees.referral)

    if inputs.apparel or inputs.dangerous_goods:
        kind = "Apparel" if inputs.apparel else "Dangerous goods"
        fees.problems.append(
            f"{kind} use separate Amazon rate cards that aren't loaded yet, so the "
            "fulfillment fee is not calculated."
        )
        return fees

    package = inputs.package
    if package is None:
        fees.problems.append(
            "Needs packaged dimensions and weight: FBA fulfillment and storage fees depend on them."
        )
        return fees

    size = size_tier(tables, period, package)
    fees.size_tier = size.tier.label
    fees.shipping_weight_lb = size.shipping_weight_lb
    column = price_column(inputs.price, category or "", period)
    fees.fulfillment = fulfillment_fee(period, size, column)
    fees.surcharge = fuel_surcharge(tables, period, fees.fulfillment)
    rate = storage_rate(tables, size.tier.is_standard, assumptions.storage_season)
    fees.storage = money(package.cubic_feet * rate * assumptions.storage_months)

    longest, median, shortest = package.sides
    if not size.tier.is_standard:
        fees.warnings.append(
            "Bulky or oversize item: extra handling fees may apply and are not included."
        )
    if 9 <= inputs.price < 10 or 49 <= inputs.price <= 50:
        fees.warnings.append(
            "Price is just below a fee price band ($10 / $50); a small price change moves the fulfillment fee."
        )
    return fees


@dataclass
class Scenario:
    name: str
    price: Decimal
    lines: list[tuple[str, Decimal]]  # cost lines, positive amounts
    profit: Decimal | None
    landed_cost: Decimal | None
    ad_spend_pct: Decimal

    @property
    def margin_pct(self) -> Decimal | None:
        if self.profit is None or self.price <= 0:
            return None
        return (self.profit / self.price * 100).quantize(Decimal("0.1"))

    @property
    def roi_pct(self) -> Decimal | None:
        if self.profit is None or not self.landed_cost:
            return None
        return (self.profit / self.landed_cost * 100).quantize(Decimal("0.1"))


def scenario(
    tables: FeeTables,
    period: Period,
    inputs: ProductInputs,
    assumptions: Assumptions,
    ad_spend_pct: Decimal = ZERO,
    name: str | None = None,
) -> Scenario:
    fees = amazon_fees(tables, period, inputs, assumptions)
    landed = inputs.landed_cost
    lines: list[tuple[str, Decimal]] = []
    if fees.referral is not None:
        lines.append(("Referral fee", fees.referral))
    if fees.closing:
        lines.append(("Closing fee (media)", fees.closing))
    if fees.fulfillment is not None:
        lines.append(("FBA fulfillment fee", fees.fulfillment))
        lines.append(("Fuel and logistics surcharge", fees.surcharge))
    if fees.storage is not None:
        lines.append(("Storage", fees.storage))
    if landed is not None:
        lines.append(("Landed cost", landed))

    returns = None
    if landed is not None and fees.fulfillment is not None and fees.refund_admin is not None:
        lost_per_return = landed + fees.fulfillment + fees.surcharge + fees.refund_admin
        returns = money(lost_per_return * assumptions.returns_pct / 100)
        lines.append((f"Returns allowance ({assumptions.returns_pct.normalize():f}%)", returns))

    ads = money(inputs.price * ad_spend_pct / 100)
    if ad_spend_pct:
        lines.append((f"Advertising ({ad_spend_pct.normalize():f}% of price)", ads))

    profit = None
    if fees.complete and landed is not None:
        profit = inputs.price - sum((amount for _, amount in lines), ZERO)

    label = name or ("Organic (no ads)" if not ad_spend_pct else f"With ads ({ad_spend_pct.normalize():f}%)")
    return Scenario(label, inputs.price, lines, profit, landed, ad_spend_pct)


def break_even_price(
    tables: FeeTables,
    period: Period,
    inputs: ProductInputs,
    assumptions: Assumptions,
    ad_spend_pct: Decimal = ZERO,
    ceiling: Decimal | None = None,
) -> Decimal | None:
    """Lowest price (to the cent) at which profit per unit is zero or more.

    Fees jump at price bands, so this scans upwards in 25 cent steps and then
    finds the exact cent. Returns None if there is no break-even below
    `ceiling` (default: 3x the current price, at least $200).
    """
    def profit_at(price: Decimal) -> Decimal | None:
        return scenario(tables, period, replace(inputs, price=price), assumptions, ad_spend_pct).profit

    if profit_at(inputs.price) is None:
        return None
    ceiling = ceiling or max(inputs.price * 3, Decimal("200"))
    step, cent = Decimal("0.25"), Decimal("0.01")
    previous = Decimal("0.00")
    price = step
    while price <= ceiling:
        if profit_at(price) >= 0:
            candidate = previous + cent
            while candidate <= price:
                if profit_at(candidate) >= 0:
                    return candidate
                candidate += cent
        previous = price
        price += step
    return None


@dataclass
class Analysis:
    fees: AmazonFees
    organic: Scenario
    with_ads: Scenario | None
    break_even_organic: Decimal | None
    break_even_with_ads: Decimal | None
    missing: list[str]


def analyze(
    tables: FeeTables,
    period: Period,
    inputs: ProductInputs,
    assumptions: Assumptions,
    ads_enabled: bool = False,
) -> Analysis:
    fees = amazon_fees(tables, period, inputs, assumptions)
    organic = scenario(tables, period, inputs, assumptions)
    with_ads = None
    if ads_enabled:
        with_ads = scenario(tables, period, inputs, assumptions, assumptions.ad_spend_pct)

    missing = list(fees.problems)
    if inputs.unit_cost is None:
        missing.append("Enter a unit cost to see profit (landed cost is unknown).")

    return Analysis(
        fees=fees,
        organic=organic,
        with_ads=with_ads,
        break_even_organic=break_even_price(tables, period, inputs, assumptions),
        break_even_with_ads=(
            break_even_price(tables, period, inputs, assumptions, assumptions.ad_spend_pct)
            if ads_enabled else None
        ),
        missing=missing,
    )
