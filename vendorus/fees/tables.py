"""Load the dated Amazon fee tables from `us.toml`.

All money and measurements are Decimal so fees match Amazon to the cent.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

FEE_FILE = Path(__file__).with_name("us.toml")


def dec(value) -> Decimal:
    """Exact Decimal from a TOML number (str() avoids binary float noise)."""
    return Decimal(str(value))


@dataclass(frozen=True)
class TierLimit:
    name: str
    max_lb: Decimal | None = None
    longest: Decimal | None = None
    median: Decimal | None = None
    shortest: Decimal | None = None
    length_girth: Decimal | None = None

    @property
    def is_standard(self) -> bool:
        return self.name.endswith("_standard")

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").replace("0 50", "0-50 lb").replace(
            "50 70", "50-70 lb").replace("70 150", "70-150 lb").replace("150 plus", "150+ lb")


@dataclass(frozen=True)
class WeightBand:
    max_oz: Decimal
    fees: tuple[Decimal, Decimal, Decimal]  # <$10, $10-$50, >$50


@dataclass(frozen=True)
class PerLbRate:
    base: tuple[Decimal, Decimal, Decimal]
    per_lb: Decimal
    after_lb: Decimal


@dataclass(frozen=True)
class Period:
    id: str
    label: str
    starts: date
    ends: date
    tier_set: str
    low_price_excluded: frozenset[str]
    small_standard: tuple[WeightBand, ...]
    large_standard: tuple[WeightBand, ...]
    over_3lb_base: tuple[Decimal, Decimal, Decimal]
    over_3lb_per_4oz: Decimal
    per_lb: dict[str, PerLbRate]

    @property
    def is_peak(self) -> bool:
        return "peak" in self.id and "non-peak" not in self.id

    def covers(self, day: date) -> bool:
        return self.starts <= day <= self.ends


@dataclass(frozen=True)
class ReferralTier:
    rate: Decimal  # percent
    up_to: Decimal | None = None


@dataclass(frozen=True)
class ReferralCategory:
    name: str
    mode: str  # flat / whole / portion
    tiers: tuple[ReferralTier, ...]
    minimum: Decimal
    closing_fee: Decimal


@dataclass(frozen=True)
class FeeTables:
    marketplace: str
    retrieved: date
    reviewed: bool
    sources: dict[str, str]
    surcharge_percent: Decimal
    surcharge_starts: date
    dim_divisor: Decimal
    bulky_min_side: Decimal
    storage: dict[str, Decimal]
    tier_sets: dict[str, tuple[TierLimit, ...]]
    periods: tuple[Period, ...]
    referral: dict[str, ReferralCategory]

    def period(self, period_id: str) -> Period:
        for period in self.periods:
            if period.id == period_id:
                return period
        raise KeyError(f"No fee period '{period_id}'")

    def period_for(self, day: date) -> Period | None:
        return next((p for p in self.periods if p.covers(day)), None)

    def default_period(self, today: date) -> Period:
        """The non-peak period in force today, or the next one to start.

        Planning uses everyday (non-peak) fees; holiday peak fees can be
        chosen on the page. Falls back to the latest period if all have ended.
        """
        non_peak = [p for p in self.periods if not p.is_peak]
        for period in sorted(non_peak, key=lambda p: p.starts):
            if period.ends >= today:
                return period
        return max(self.periods, key=lambda p: p.ends)

    def is_out_of_date(self, today: date) -> bool:
        return today > max(p.ends for p in self.periods)

    @property
    def categories(self) -> list[str]:
        return sorted(self.referral)


def _price_columns(values) -> tuple[Decimal, Decimal, Decimal]:
    low, mid, high = (dec(v) for v in values)
    return low, mid, high


def _load_period(period_id: str, raw: dict) -> Period:
    over = raw["large_standard_over_3lb"]
    return Period(
        id=period_id,
        label=raw["label"],
        starts=raw["starts"],
        ends=raw["ends"],
        tier_set=raw["tier_set"],
        low_price_excluded=frozenset(raw.get("low_price_excluded", [])),
        small_standard=tuple(WeightBand(dec(r[0]), _price_columns(r[1:])) for r in raw["small_standard"]),
        large_standard=tuple(WeightBand(dec(r[0]), _price_columns(r[1:])) for r in raw["large_standard"]),
        over_3lb_base=_price_columns(over["base"]),
        over_3lb_per_4oz=dec(over["per_4oz"]),
        per_lb={
            name: PerLbRate(_price_columns(rate["base"]), dec(rate["per_lb"]), dec(rate["after_lb"]))
            for name, rate in raw["per_lb"].items()
        },
    )


def _limit(raw: dict, key: str) -> Decimal | None:
    return dec(raw[key]) if key in raw else None


def parse_fee_tables(raw: dict) -> FeeTables:
    tier_sets = {
        name: tuple(
            TierLimit(
                name=t["name"],
                max_lb=_limit(t, "max_lb"),
                longest=_limit(t, "longest"),
                median=_limit(t, "median"),
                shortest=_limit(t, "shortest"),
                length_girth=_limit(t, "length_girth"),
            )
            for t in tier_set["tiers"]
        )
        for name, tier_set in raw["tier_sets"].items()
    }
    periods = tuple(_load_period(pid, p) for pid, p in raw["periods"].items())
    for period in periods:
        tier_names = {t.name for t in tier_sets[period.tier_set]}
        missing = tier_names - set(period.per_lb) - {"small_standard", "large_standard"}
        if missing:
            raise ValueError(f"Period {period.id} has no fees for tiers: {sorted(missing)}")

    referral = {}
    for r in raw["referral"]:
        referral[r["category"]] = ReferralCategory(
            name=r["category"],
            mode=r.get("mode", "flat"),
            tiers=tuple(ReferralTier(dec(t["rate"]), _limit(t, "up_to")) for t in r["tiers"]),
            minimum=dec(r.get("minimum", 0)),
            closing_fee=dec(r.get("closing_fee", 0)),
        )

    return FeeTables(
        marketplace=raw["marketplace"],
        retrieved=raw["retrieved"],
        reviewed=bool(raw.get("reviewed", False)),
        sources=dict(raw["sources"]),
        surcharge_percent=dec(raw["surcharge"]["percent"]),
        surcharge_starts=raw["surcharge"]["starts"],
        dim_divisor=dec(raw["dimensional_weight"]["divisor"]),
        bulky_min_side=dec(raw["dimensional_weight"]["bulky_min_side_in"]),
        storage={k: dec(v) for k, v in raw["storage"].items()},
        tier_sets=tier_sets,
        periods=periods,
        referral=referral,
    )


@lru_cache(maxsize=4)
def load_fee_tables(path: Path = FEE_FILE) -> FeeTables:
    with open(path, "rb") as f:
        return parse_fee_tables(tomllib.load(f))
