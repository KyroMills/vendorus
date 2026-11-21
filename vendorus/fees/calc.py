"""Amazon fee calculations: size tier, fulfillment, referral and storage.

Plain arithmetic on Amazon's published tables. Rules are quoted from the
pages listed in `us.toml`.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from vendorus.fees.tables import FeeTables, Period, TierLimit

CENT = Decimal("0.01")
ZERO = Decimal("0")
OZ_PER_LB = Decimal(16)
CUBIC_INCHES_PER_FOOT = Decimal(1728)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def ceil(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_CEILING)


@dataclass(frozen=True)
class Package:
    """A packaged unit: dimensions in inches, weight in pounds."""

    length: Decimal
    width: Decimal
    height: Decimal
    weight_lb: Decimal

    def __post_init__(self):
        for name in ("length", "width", "height", "weight_lb"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")

    @property
    def sides(self) -> tuple[Decimal, Decimal, Decimal]:
        longest, median, shortest = sorted((self.length, self.width, self.height), reverse=True)
        return longest, median, shortest

    @property
    def length_plus_girth(self) -> Decimal:
        longest, median, shortest = self.sides
        return longest + 2 * (median + shortest)

    @property
    def cubic_feet(self) -> Decimal:
        return self.length * self.width * self.height / CUBIC_INCHES_PER_FOOT

    def dimensional_weight(self, divisor: Decimal, min_side: Decimal = ZERO) -> Decimal:
        longest, median, shortest = self.sides
        return longest * max(median, min_side) * max(shortest, min_side) / divisor


@dataclass(frozen=True)
class SizeResult:
    tier: TierLimit
    unit_weight_lb: Decimal
    dimensional_weight_lb: Decimal
    shipping_weight_lb: Decimal  # the weight the fee is charged on


def _fits(tier: TierLimit, package: Package, weight: Decimal) -> bool:
    longest, median, shortest = package.sides
    checks = (
        (tier.max_lb, weight),
        (tier.longest, longest),
        (tier.median, median),
        (tier.shortest, shortest),
        (tier.length_girth, package.length_plus_girth),
    )
    return all(limit is None or value <= limit for limit, value in checks)


def size_tier(tables: FeeTables, period: Period, package: Package) -> SizeResult:
    """Pick the size tier and the weight the fulfillment fee is charged on.

    Tiering uses the greater of unit and dimensional weight. Fees use unit
    weight for small standard and the heaviest tier; all other tiers use the
    greater of unit and dimensional weight. Bulky and oversize dimensional
    weight assumes a minimum width and height of 2 inches.
    """
    actual_dim = package.dimensional_weight(tables.dim_divisor)
    bulky_dim = package.dimensional_weight(tables.dim_divisor, tables.bulky_min_side)
    tiers = tables.tier_sets[period.tier_set]

    chosen = None
    for tier in tiers:
        dim = actual_dim if tier.is_standard else bulky_dim
        if _fits(tier, package, max(package.weight_lb, dim)):
            chosen = tier
            break
    assert chosen is not None, "the last tier has no limits"

    dim = actual_dim if chosen.is_standard else bulky_dim
    if chosen.name == "small_standard" or chosen is tiers[-1]:
        shipping = package.weight_lb
    else:
        shipping = max(package.weight_lb, dim)
    return SizeResult(chosen, package.weight_lb, dim, shipping)


def price_column(price: Decimal, category: str, period: Period) -> int:
    """0 = under $10, 1 = $10 to $50 (inclusive), 2 = over $50."""
    if price < 10:
        return 1 if category in period.low_price_excluded else 0
    return 1 if price <= 50 else 2


def fulfillment_fee(period: Period, size: SizeResult, column: int) -> Decimal:
    """FBA fulfillment fee per unit, before the fuel and logistics surcharge."""
    weight = size.shipping_weight_lb
    name = size.tier.name
    if name in ("small_standard", "large_standard"):
        ounces = weight * OZ_PER_LB
        bands = period.small_standard if name == "small_standard" else period.large_standard
        for band in bands:
            if ounces <= band.max_oz:
                return money(band.fees[column])
        # Large standard over 3 lb: base + each started 4 oz interval above 3 lb.
        intervals = ceil((weight - 3) / Decimal("0.25"))
        return money(period.over_3lb_base[column] + intervals * period.over_3lb_per_4oz)
    rate = period.per_lb[name]
    extra_lb = max(ZERO, ceil(weight - rate.after_lb))
    return money(rate.base[column] + extra_lb * rate.per_lb)


def fuel_surcharge(tables: FeeTables, period: Period, fee: Decimal) -> Decimal:
    if period.ends < tables.surcharge_starts:
        return ZERO
    return money(fee * tables.surcharge_percent / 100)


def referral_fee(tables: FeeTables, category: str, price: Decimal) -> Decimal:
    """Referral fee on the total sales price (FBA items: the item price)."""
    cat = tables.referral[category]
    if cat.mode == "portion":
        fee, floor = ZERO, ZERO
        for tier in cat.tiers:
            top = price if tier.up_to is None else min(price, tier.up_to)
            if top > floor:
                fee += (top - floor) * tier.rate / 100
            if tier.up_to is None or price <= tier.up_to:
                break
            floor = tier.up_to
    else:
        tier = next(t for t in cat.tiers if t.up_to is None or price <= t.up_to)
        fee = price * tier.rate / 100
    return money(max(fee, cat.minimum))


def storage_rate(tables: FeeTables, standard: bool, season: str) -> Decimal:
    """Monthly storage per cubic foot for stock under 180 days old.

    season: "off_peak" (Jan-Sep), "peak" (Oct-Dec) or "blended" (a year's
    average: 9 off-peak months and 3 peak months).
    """
    prefix = "standard" if standard else "oversize"
    off, peak = tables.storage[f"{prefix}_off_peak"], tables.storage[f"{prefix}_peak"]
    if season == "off_peak":
        return off
    if season == "peak":
        return peak
    if season == "blended":
        return (off * 9 + peak * 3) / 12
    raise ValueError(f"Unknown storage season '{season}'")


def refund_administration_fee(referral: Decimal) -> Decimal:
    """Kept by Amazon when a sale is refunded: $5.00 or 20% of the referral
    fee, whichever is less."""
    return money(min(Decimal("5.00"), referral * Decimal("0.20")))
