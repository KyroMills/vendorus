"""Opportunity score: 0-100 from ten transparent factors.

Every point comes from imported research data or the Phase 2 profit engine.
Factors without data are left out (not guessed), the total is scaled over
the factors that were scored, and coverage is always reported.
"""
from __future__ import annotations

import math
import statistics
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from vendorus.config import PROJECT_ROOT
from vendorus.costs import assumptions_for, get_cost, inputs_for
from vendorus.db import create_tables
from vendorus.fees import FeeTables
from vendorus.models import CompetitorAsin, Keyword, Product, ProductCost, ResearchSnapshot
from vendorus.profit import Assumptions, analyze

SCORING_FILE = PROJECT_ROOT / "config" / "scoring.toml"


# --- settings ----------------------------------------------------------------

@dataclass(frozen=True)
class FactorSpec:
    key: str
    label: str
    group: str
    weight: float
    why: str
    zero: float | None = None
    full: float | None = None
    log: bool = False
    # Price point only: a band with full points between full_low and full_high.
    zero_low: float | None = None
    full_low: float | None = None
    full_high: float | None = None
    zero_high: float | None = None


@dataclass(frozen=True)
class ScoringConfig:
    factors: tuple[FactorSpec, ...]
    bands: tuple[tuple[str, float], ...]  # highest first
    min_coverage: int = 6
    min_listings: int = 3
    min_listings_for_spread: int = 5
    top_listings: int = 10
    stale_after_days: int = 90
    fragile_break_even_pct: float = 90

    def spec(self, key: str) -> FactorSpec:
        return next(f for f in self.factors if f.key == key)


def load_scoring(path: Path = SCORING_FILE) -> ScoringConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    factors = tuple(FactorSpec(key=key, **values) for key, values in raw["factors"].items())
    bands = tuple(sorted(raw["bands"].items(), key=lambda item: item[1], reverse=True))
    settings = {k: raw[k] for k in (
        "min_coverage", "min_listings", "min_listings_for_spread", "top_listings",
        "stale_after_days", "fragile_break_even_pct",
    ) if k in raw}
    return ScoringConfig(factors=factors, bands=bands, **settings)


# --- points ------------------------------------------------------------------

def scale(value: float, zero: float, full: float, log: bool = False) -> float:
    """0.0 at `zero`, 1.0 at `full`, straight line in between (clamped).
    Works in either direction: if zero > full, lower values score higher."""
    if log:
        value, zero, full = (math.log10(max(v, 1e-9)) for v in (value, zero, full))
    if zero == full:
        return 1.0 if value == full else 0.0
    fraction = (value - zero) / (full - zero)
    return max(0.0, min(1.0, fraction))


def band_scale(value: float, spec: FactorSpec) -> float:
    """Full points inside [full_low, full_high], fading to zero at the ends."""
    if value <= spec.full_low:
        return scale(value, spec.zero_low, spec.full_low)
    if value >= spec.full_high:
        return scale(value, spec.zero_high, spec.full_high)
    return 1.0


# --- the market around one product ---------------------------------------------

@dataclass
class Listing:
    asin: str
    monthly_sales: int | None
    review_count: int | None
    rating: float | None
    price: float | None
    brand: str | None = None


@dataclass
class Market:
    product: Product | None
    cost: ProductCost | None
    keywords: list[Keyword]
    listings: list[Listing]  # the product itself plus its competitors
    researched_on: datetime | None

    @property
    def main_keyword(self) -> Keyword | None:
        with_volume = [k for k in self.keywords if k.search_volume is not None]
        return max(with_volume, key=lambda k: k.search_volume) if with_volume else None


def _latest_per(rows, key):
    """Keep the newest row (highest snapshot id) for each key."""
    newest = {}
    for row in sorted(rows, key=lambda r: (r.snapshot_id, r.id), reverse=True):
        newest.setdefault(key(row), row)
    return list(newest.values())


def _build_market(product, cost, keywords, competitor_rows, researched_on) -> Market:
    """Assemble one product's market. Competitors: newest row per competitor
    ASIN among rows that compete with this ASIN or one of its keywords."""
    competitors = _latest_per(competitor_rows, lambda c: c.asin)
    listings = []
    if product is not None:
        listings.append(Listing(product.asin, product.monthly_sales, product.review_count,
                                product.rating, product.price, product.brand))
    for c in competitors:
        if product is None or c.asin != product.asin:
            listings.append(Listing(c.asin, c.monthly_sales, c.review_count, c.rating, c.price, c.brand))
    return Market(product, cost, keywords, listings, researched_on)


def gather_market(engine: Engine, asin: str) -> Market:
    """The product, its linked keywords and its competitors, newest data first."""
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        product = session.scalars(
            select(Product).where(Product.asin == asin).order_by(Product.snapshot_id.desc())
        ).first()
        keywords = _latest_per(
            session.scalars(select(Keyword).where(Keyword.product_asin == asin)), lambda k: k.phrase
        )
        targets = [asin] + [k.phrase for k in keywords]
        competitor_rows = list(session.scalars(select(CompetitorAsin).where(
            or_(*(CompetitorAsin.competes_with == t for t in targets))
        )))
        researched_on = None
        if product is not None:
            researched_on = session.get(ResearchSnapshot, product.snapshot_id).imported_at
    return _build_market(product, get_cost(engine, asin), keywords, competitor_rows, researched_on)


def gather_all_markets(engine: Engine) -> list[Market]:
    """Every product's market, loaded with a handful of bulk queries.
    Same rules as gather_market, newest imports first."""
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        products = _latest_per(session.scalars(select(Product)), lambda p: p.asin)
        products.sort(key=lambda p: (-p.snapshot_id, p.id))
        imported = dict(session.execute(select(ResearchSnapshot.id, ResearchSnapshot.imported_at)).all())
        costs = {c.asin: c for c in session.scalars(select(ProductCost))}
        keywords_by_asin: dict[str, list[Keyword]] = {}
        for k in _latest_per(
            session.scalars(select(Keyword).where(Keyword.product_asin.is_not(None))),
            lambda k: (k.product_asin, k.phrase),
        ):
            keywords_by_asin.setdefault(k.product_asin, []).append(k)
        competitors_by_target: dict[str, list[CompetitorAsin]] = {}
        for c in session.scalars(select(CompetitorAsin)):
            competitors_by_target.setdefault(c.competes_with, []).append(c)

    markets = []
    for product in products:
        keywords = keywords_by_asin.get(product.asin, [])
        rows = []
        for target in [product.asin] + [k.phrase for k in keywords]:
            rows.extend(competitors_by_target.get(target, []))
        markets.append(_build_market(product, costs.get(product.asin), keywords, rows,
                                     imported.get(product.snapshot_id)))
    return markets


# --- result -------------------------------------------------------------------

@dataclass
class FactorResult:
    spec: FactorSpec
    points: float | None  # None = no data
    value: str  # the number behind the points, formatted
    missing: str | None = None  # why there's no score

    @property
    def scored(self) -> bool:
        return self.points is not None


@dataclass
class Score:
    asin: str
    total: float | None
    status: str  # Scored / Not enough data / Not viable
    band: str | None
    factors: list[FactorResult]
    warnings: list[str] = field(default_factory=list)
    listings: int = 0
    main_keyword: str | None = None
    # Summary numbers for ranking tables.
    title: str | None = None
    category: str | None = None
    price: Decimal | None = None
    profit: Decimal | None = None  # organic, per unit
    margin_pct: Decimal | None = None
    break_even: Decimal | None = None
    monthly_sales: int | None = None  # research estimate for this ASIN
    researched_on: datetime | None = None

    @property
    def coverage(self) -> int:
        return sum(1 for f in self.factors if f.scored)

    @property
    def coverage_text(self) -> str:
        return f"{self.coverage} of {len(self.factors)} factors"


def _top(listings: list[Listing], n: int) -> list[Listing]:
    with_sales = [l for l in listings if l.monthly_sales is not None]
    if len(with_sales) >= len(listings) / 2:
        return sorted(with_sales, key=lambda l: l.monthly_sales, reverse=True)[:n]
    return sorted(listings, key=lambda l: l.review_count or 0, reverse=True)[:n]


def score_market(
    market: Market,
    tables: FeeTables,
    planning: Assumptions,
    config: ScoringConfig,
    today: date | None = None,
) -> Score:
    today = today or date.today()
    results: dict[str, FactorResult] = {}

    def add(key: str, fraction: float | None, value: str, missing: str | None = None):
        spec = config.spec(key)
        points = None if fraction is None else round(fraction * spec.weight, 1)
        results[key] = FactorResult(spec, points, value, missing)

    def simple(key: str, value: float | None, text: str, missing: str):
        spec = config.spec(key)
        if value is None:
            add(key, None, "—", missing)
        else:
            add(key, scale(value, spec.zero, spec.full, spec.log), text)

    listings = market.listings
    top = _top(listings, config.top_listings)
    enough = len(listings) >= config.min_listings
    need = (f"needs at least {config.min_listings} listings (the product plus competitors); "
            f"have {len(listings)}. Import competitors for this ASIN.")

    # Demand
    sales = [l.monthly_sales for l in top if l.monthly_sales is not None]
    if not enough:
        simple("market_demand", None, "", need)
    else:
        simple("market_demand", statistics.mean(sales) if sales else None,
               f"{statistics.mean(sales):,.0f} sales/month" if sales else "",
               "no monthly sales estimates for these listings")

    keyword = market.main_keyword
    simple("search_demand", keyword.search_volume if keyword else None,
           f"{keyword.search_volume:,} searches/month for “{keyword.phrase}”" if keyword else "",
           "no keyword with search volume linked to this ASIN")

    # Organic competition
    reviews = [l.review_count for l in top if l.review_count is not None]
    if not enough:
        simple("review_barrier", None, "", need)
    else:
        median = statistics.median(reviews) if reviews else None
        simple("review_barrier", median, f"median {median:,.0f} reviews" if reviews else "",
               "no review counts for these listings")

    if keyword and keyword.competing_products is not None and keyword.search_volume:
        density = keyword.competing_products / keyword.search_volume * 1000
        simple("competition_density", density,
               f"{density:,.0f} competing products per 1,000 searches", "")
    else:
        simple("competition_density", None, "",
               "main keyword needs both search volume and competing products")

    ratings = [l.rating for l in top if l.rating is not None]
    if not enough:
        simple("rating_gap", None, "", need)
    else:
        avg = statistics.mean(ratings) if ratings else None
        simple("rating_gap", avg, f"average {avg:.2f} stars" if ratings else "",
               "no ratings for these listings")

    all_sales = sorted((l.monthly_sales for l in listings if l.monthly_sales is not None), reverse=True)
    if len(all_sales) < config.min_listings_for_spread or sum(all_sales) == 0:
        simple("sales_spread", None, "",
               f"needs sales estimates for at least {config.min_listings_for_spread} listings; "
               f"have {len(all_sales)}")
    else:
        share = sum(all_sales[:3]) / sum(all_sales) * 100
        simple("sales_spread", share, f"top 3 listings hold {share:.0f}% of sales", "")

    # Profit (organic) and operations, from the Phase 2 engine
    inputs = inputs_for(market.product, market.cost)
    analysis = None
    if inputs is not None:
        analysis = analyze(tables, tables.default_period(today), inputs, assumptions_for(planning, market.cost))
    organic = analysis.organic if analysis else None
    profit_missing = (
        " ".join(analysis.missing) if analysis and analysis.missing else "no selling price"
    )

    if organic and organic.profit is not None:
        simple("margin", float(organic.margin_pct), f"{organic.margin_pct}% margin", "")
        simple("roi", float(organic.roi_pct), f"{organic.roi_pct}% return on landed cost", "")
    else:
        simple("margin", None, "", profit_missing)
        simple("roi", None, "", profit_missing)

    if inputs is not None:
        price = float(inputs.price)
        add("price_point", band_scale(price, config.spec("price_point")), f"${price:,.2f}")
    else:
        add("price_point", None, "—", "no selling price")

    fees = analysis.fees if analysis else None
    if fees is not None and fees.complete:
        share = float(fees.total / inputs.price * 100)
        simple("fee_share", share, f"Amazon fees are {share:.0f}% of price", "")
    else:
        simple("fee_share", None, "",
               " ".join(fees.problems) if fees and fees.problems else profit_missing)

    factors = [results[spec.key] for spec in config.factors]
    scored = [f for f in factors if f.scored]
    total = None
    if scored:
        total = round(sum(f.points for f in scored) / sum(f.spec.weight for f in scored) * 100)

    # Hard stops and warnings
    warnings: list[str] = []
    status = "Scored"
    if organic and organic.profit is not None and organic.profit <= 0:
        status = "Not viable"
        warnings.append(f"Loses money with no advertising: {organic.profit:+.2f} per unit at this price.")
    elif len(scored) < config.min_coverage:
        status = "Not enough data"
    if analysis and analysis.break_even_organic and inputs is not None and status != "Not viable":
        if analysis.break_even_organic >= inputs.price * Decimal(config.fragile_break_even_pct) / 100:
            warnings.append(
                f"Fragile price: break-even is ${analysis.break_even_organic}, close to the "
                f"${inputs.price} selling price."
            )
    if fees is not None:
        warnings.extend(fees.warnings)
    if inputs is not None and (inputs.apparel or inputs.dangerous_goods):
        warnings.append("Apparel or hazmat: fulfillment fees aren't supported yet.")
    if market.researched_on is not None:
        moment = market.researched_on
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age = (today - moment.date()).days
        if age > config.stale_after_days:
            warnings.append(f"Research data is {age} days old; re-import before deciding.")

    band = None
    if total is not None and status == "Scored":
        band = next(name for name, minimum in config.bands if total >= minimum)

    product = market.product
    return Score(
        asin=product.asin if product else (market.cost.asin if market.cost else "?"),
        total=total,
        status=status,
        band=band,
        factors=factors,
        warnings=warnings,
        listings=len(listings),
        main_keyword=keyword.phrase if keyword else None,
        title=product.title if product else None,
        category=product.category if product else None,
        price=inputs.price if inputs is not None else None,
        profit=organic.profit if organic else None,
        margin_pct=organic.margin_pct if organic else None,
        break_even=analysis.break_even_organic if analysis else None,
        monthly_sales=product.monthly_sales if product else None,
        researched_on=market.researched_on,
    )


def score_product(
    engine: Engine, asin: str, tables: FeeTables, planning: Assumptions,
    config: ScoringConfig | None = None, today: date | None = None,
) -> Score:
    return score_market(gather_market(engine, asin), tables, planning, config or load_scoring(), today)


def score_all(
    engine: Engine, tables: FeeTables, planning: Assumptions,
    config: ScoringConfig | None = None, today: date | None = None,
) -> list[Score]:
    """Score every imported product."""
    config = config or load_scoring()
    return [score_market(m, tables, planning, config, today) for m in gather_all_markets(engine)]
