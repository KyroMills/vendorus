"""Validation: does a promising product hold up?

Seasonality from monthly history, review analysis (velocity, ratings, common
complaint phrases), risk flags, research history across imports, and your
checklist and go / no-go decision. Everything is calculated from imported
data with plain rules; nothing here changes the opportunity score.
"""
from __future__ import annotations

import calendar
import json
import re
import statistics
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from vendorus.config import PROJECT_ROOT
from vendorus.db import create_tables
from vendorus.models import (
    CompetitorAsin,
    Product,
    ProductValidation,
    ResearchSnapshot,
    Review,
    TrendPoint,
)
from vendorus.scoring import Market, Score

VALIDATION_FILE = PROJECT_ROOT / "config" / "validation.toml"
DECISIONS = ("Undecided", "Go", "Watch", "No-go")
SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


def load_validation_config(path: Path = VALIDATION_FILE) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# --- seasonality ---------------------------------------------------------------

@dataclass
class Season:
    subject: str | None
    metric: str | None
    months: list[tuple[date, float]]
    label: str | None = None  # Highly seasonal / Moderately seasonal / Steady
    trend: str | None = None  # Growing / Declining / Stable (needs 24 months)
    ratio: float | None = None  # busiest calendar month / quietest
    peak_months: list[str] = field(default_factory=list)
    quiet_months: list[str] = field(default_factory=list)
    change_pct: float | None = None  # last 12 months vs the 12 before
    missing: str | None = None


def monthly_series(points: list[TrendPoint]) -> list[tuple[date, float]]:
    """One value per month; the newest import wins when months overlap."""
    newest: dict[date, TrendPoint] = {}
    for p in sorted(points, key=lambda p: (p.snapshot_id, p.id)):
        newest[p.month] = p
    return sorted((m, float(p.value)) for m, p in newest.items())


def analyze_season(series: list[tuple[date, float]], subject: str | None, metric: str | None,
                   cfg: dict) -> Season:
    season = Season(subject, metric, series)
    need = cfg["min_months"]
    if len(series) < need:
        season.missing = (f"needs {need} months of history; have {len(series)}. "
                          "Import a trends file for the main keyword or this ASIN.")
        return season

    by_month: dict[int, list[float]] = defaultdict(list)
    for month, value in series:
        by_month[month.month].append(value)
    averages = {m: statistics.mean(v) for m, v in by_month.items()}
    overall = statistics.mean(averages.values())
    busiest, quietest = max(averages.values()), min(averages.values())
    season.ratio = float("inf") if quietest == 0 else busiest / quietest
    factor = cfg["peak_factor"]
    season.peak_months = [calendar.month_abbr[m] for m in sorted(averages) if averages[m] >= overall * factor]
    season.quiet_months = [calendar.month_abbr[m] for m in sorted(averages) if averages[m] <= overall / factor]
    if season.ratio >= cfg["high_ratio"]:
        season.label = "Highly seasonal"
    elif season.ratio >= cfg["moderate_ratio"]:
        season.label = "Moderately seasonal"
    else:
        season.label = "Steady"

    if len(series) >= 24:
        values = [v for _, v in series]
        recent, before = sum(values[-12:]), sum(values[-24:-12])
        if before > 0:
            season.change_pct = (recent - before) / before * 100
            if season.change_pct <= cfg["declining_pct"]:
                season.trend = "Declining"
            elif season.change_pct >= cfg["growing_pct"]:
                season.trend = "Growing"
            else:
                season.trend = "Stable"
    return season


def pick_trend(market: Market, points_by_subject: dict[str, list[TrendPoint]]) -> tuple[str | None, str | None, list]:
    """Prefer the main keyword's search volume, then any linked keyword, then
    the ASIN's own sales history."""
    candidates = []
    if market.main_keyword:
        candidates.append(market.main_keyword.phrase)
    candidates += [k.phrase for k in market.keywords]
    if market.product:
        candidates.append(market.product.asin)
    for subject in candidates:
        points = points_by_subject.get(subject)
        if points:
            metrics = Counter(p.metric for p in points)
            metric = metrics.most_common(1)[0][0]
            return subject, metric, [p for p in points if p.metric == metric]
    return None, None, []


# --- reviews ----------------------------------------------------------------------

STOPWORDS = set("""
a about after again all also am an and any are as at be because been before being but by can
could did do does doing don't down for from get got had has have having he her here hers him his
how i i'm if in into is it it's its just like me more most my no not now of off on once one only
or other our out over own same she should so some such than that the their them then there these
they this those through to too under until up very was we were what when where which while who
why will with would you your yours i've i'd it'll didn't doesn't wasn't isn't aren't won't can't
really much even still well item product amazon bought buy purchase ordered order thing things
use used using get getting go going way would've they're there's that's
""".split())


@dataclass
class Phrase:
    text: str
    reviews: int  # complaint reviews that mention it
    share: float  # % of complaint reviews
    example: str


@dataclass
class ReviewInsight:
    count: int = 0
    by_rating: dict[int, int] = field(default_factory=dict)
    complaint_share: float | None = None  # % of reviews at or below low_star_max
    phrases: list[Phrase] = field(default_factory=list)
    velocity: dict[str, float] = field(default_factory=dict)  # ASIN -> reviews per month
    velocity_note: str | None = None
    missing: str | None = None

    @property
    def fastest(self) -> tuple[str, float] | None:
        return max(self.velocity.items(), key=lambda kv: kv[1]) if self.velocity else None


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z']+", text.lower()) if len(w) > 2 and w not in STOPWORDS]


def _grams(text: str) -> set[str]:
    """Words and two-word phrases, never joining across sentences (or a
    review's title and body, which are separated with a full stop)."""
    grams: set[str] = set()
    for sentence in re.split(r"[.!?;:\n]+", text):
        words = _words(sentence)
        grams.update(words)
        grams.update(f"{a} {b}" for a, b in zip(words, words[1:]) if a != b)
    return grams


def complaint_phrases(texts: list[str], top: int) -> list[Phrase]:
    """Words and two-word phrases that come up in several complaint reviews.
    Counted once per review; two-word phrases are listed before the single
    words they contain, and "handle broke" / "broke handle" count once."""
    docs = [_grams(t) for t in texts]
    counts = Counter(g for grams in docs for g in grams)
    common = [(g, n) for g, n in counts.items() if n >= 2]
    common.sort(key=lambda gn: (-gn[1], -len(gn[0].split()), gn[0]))
    chosen: list[tuple[str, int]] = []
    for gram, n in common:
        words = gram.split()
        if len(words) == 1 and any(gram in c.split() and m >= n for c, m in chosen):
            continue  # already covered by a phrase at least as common
        if len(words) == 2 and any(set(words) == set(c.split()) for c, _ in chosen):
            continue  # same two words in the other order
        chosen.append((gram, n))
        if len(chosen) == top:
            break
    phrases = []
    for gram, n in chosen:
        example = next((t for t, grams in zip(texts, docs) if gram in grams), "")
        phrases.append(Phrase(gram, n, n / len(texts) * 100, _snippet(example, gram.split()[0])))
    return phrases


def _snippet(text: str, word: str, width: int = 140) -> str:
    text = " ".join(text.split())
    at = text.lower().find(word)
    start = max(0, at - width // 3) if at >= 0 else 0
    piece = text[start:start + width]
    return ("…" if start > 0 else "") + piece + ("…" if start + width < len(text) else "")


def analyze_reviews(reviews: list[Review], history: dict[str, list[tuple[datetime, int]]], cfg: dict) -> ReviewInsight:
    insight = ReviewInsight()
    if reviews:
        insight.count = len(reviews)
        insight.by_rating = {stars: sum(1 for r in reviews if r.rating == stars) for stars in range(5, 0, -1)}
        low = [r for r in reviews if r.rating <= cfg["low_star_max"]]
        insight.complaint_share = len(low) / len(reviews) * 100
        if len(low) >= cfg["min_low_reviews"]:
            texts = [". ".join(filter(None, (r.title, r.body))) for r in low]
            insight.phrases = complaint_phrases(texts, cfg["top_phrases"])
    else:
        insight.missing = "No reviews imported for this product or its competitors."

    # Review velocity from review counts in imports at least N days apart.
    for asin, points in history.items():
        known = sorted((moment, count) for moment, count in points if count is not None)
        if len(known) < 2:
            continue
        (first_at, first), (last_at, last) = known[0], known[-1]
        days = (_aware(last_at) - _aware(first_at)).total_seconds() / 86400
        if days >= cfg["min_days_between_imports"] - 0.01 and last >= first:
            insight.velocity[asin] = (last - first) / days * 30.4
    if not insight.velocity:
        insight.velocity_note = (
            f"Review velocity needs two imports of the same listings at least "
            f"{cfg['min_days_between_imports']} days apart. Re-import competitors later to measure it."
        )
    return insight


# --- risk flags ----------------------------------------------------------------------

@dataclass
class Flag:
    severity: str  # high / medium / info
    title: str
    detail: str


def _share_by(listings, key, display=None) -> tuple[str | None, float | None]:
    """The biggest group's share of estimated sales. `key` groups listings
    (e.g. brand, ignoring case); `display` gives the name to show."""
    totals: Counter = Counter()
    spellings: dict[str, Counter] = {}
    for listing in listings:
        group = key(listing)
        if listing.monthly_sales and group:
            totals[group] += listing.monthly_sales
            name = display(listing) if display else group
            spellings.setdefault(group, Counter())[name] += listing.monthly_sales
    overall = sum(l.monthly_sales for l in listings if l.monthly_sales)
    if not totals or not overall:
        return None, None
    group, sales = totals.most_common(1)[0]
    # Show the spelling used by the listings with the most sales.
    return spellings[group].most_common(1)[0][0], sales / overall * 100


def risk_flags(market: Market, score: Score, season: Season, reviews: ReviewInsight, cfg: dict) -> list[Flag]:
    flags: list[Flag] = []
    product = market.product

    # Product-type rules (title, store category, linked keywords).
    haystack = " ".join(filter(None, [
        product.title if product else "", product.category if product else "",
        *(k.phrase for k in market.keywords),
    ])).lower()
    for rule in cfg.get("keyword_rules", []):
        hits = [w for w in rule["words"] if re.search(rf"\b{re.escape(w)}s?\b", haystack)]
        if hits:
            flags.append(Flag(rule["severity"], rule["name"], f"{rule['note']} (matched: {', '.join(hits)})"))

    # Market concentration.
    risk = cfg["risk"]
    brand, brand_share = _share_by(market.listings, lambda l: (l.brand or "").strip().lower() or None,
                                   display=lambda l: l.brand.strip())
    if brand_share is not None and brand_share >= risk["brand_share_pct"]:
        flags.append(Flag("high", "One brand dominates",
                          f"“{brand}” holds {brand_share:.0f}% of this market's estimated sales."))
    top, top_share = _share_by(market.listings, lambda l: l.asin)
    if top_share is not None and top_share >= risk["top_listing_share_pct"] and len(market.listings) > 1:
        flags.append(Flag("medium", "One listing dominates",
                          f"{top} holds {top_share:.0f}% of this market's estimated sales."))

    # Seasonality and demand trend.
    if season.label == "Highly seasonal":
        flags.append(Flag("medium", "Highly seasonal",
                          f"Busiest month is {season.ratio:.1f}x the quietest; peaks in "
                          f"{', '.join(season.peak_months) or 'a few months'}. Time stock carefully."))
    if season.trend == "Declining":
        flags.append(Flag("high", "Demand is declining",
                          f"{season.change_pct:+.0f}% over the last 12 months compared with the 12 before."))
    elif season.trend == "Growing":
        flags.append(Flag("info", "Demand is growing",
                          f"{season.change_pct:+.0f}% over the last 12 months compared with the 12 before."))

    # Reviews.
    fastest = reviews.fastest
    if fastest and fastest[1] >= cfg["reviews"]["fast_velocity"]:
        flags.append(Flag("medium", "Competitors gain reviews fast",
                          f"{fastest[0]} adds about {fastest[1]:.0f} reviews a month; hard to catch organically."))
    if reviews.complaint_share is not None and reviews.complaint_share >= cfg["reviews"]["high_complaint_share"]:
        flags.append(Flag("info", "Customers are unhappy with what exists",
                          f"{reviews.complaint_share:.0f}% of imported reviews are 1–"
                          f"{cfg['reviews']['low_star_max']} stars: a better product could win, "
                          "if the complaints are fixable."))

    # Profit-side checks already found by scoring.
    if score.status == "Not viable":
        flags.append(Flag("high", "Loses money with no advertising", score.warnings[0] if score.warnings else ""))
    for warning in score.warnings:
        if warning.startswith(("Fragile price", "Research data", "Bulky", "Apparel")):
            flags.append(Flag("medium", warning.split(":")[0].split(".")[0], warning))

    # What hasn't been checked yet.
    if season.missing:
        flags.append(Flag("info", "Seasonality not checked", season.missing))
    if reviews.missing:
        flags.append(Flag("info", "Reviews not checked",
                          reviews.missing + " Import reviews to see common complaints."))

    flags.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return flags


# --- checklist and decision -----------------------------------------------------------

@dataclass
class Decision:
    decision: str = "Undecided"
    checklist: dict[str, bool] = field(default_factory=dict)
    notes: str = ""
    updated_at: datetime | None = None


def get_decision(engine: Engine, asin: str) -> Decision:
    create_tables(engine)
    with Session(engine) as session:
        row = session.scalars(select(ProductValidation).where(ProductValidation.asin == asin)).first()
        if row is None:
            return Decision()
        return Decision(row.decision, json.loads(row.checklist or "{}"), row.notes or "", row.updated_at)


def save_decision(engine: Engine, asin: str, decision: str, checklist: dict[str, bool], notes: str = "") -> None:
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of {DECISIONS}")
    create_tables(engine)
    with Session(engine) as session, session.begin():
        row = session.scalars(select(ProductValidation).where(ProductValidation.asin == asin)).first()
        if row is None:
            row = ProductValidation(asin=asin)
            session.add(row)
        row.decision = decision
        row.checklist = json.dumps(checklist)
        row.notes = notes


def all_decisions(engine: Engine) -> dict[str, str]:
    create_tables(engine)
    with Session(engine) as session:
        return dict(session.execute(select(ProductValidation.asin, ProductValidation.decision)).all())


# --- loading -----------------------------------------------------------------------------

@dataclass
class Validation:
    asin: str
    season: Season
    reviews: ReviewInsight
    flags: list[Flag]
    history: list[dict]  # the product's own numbers in each import, oldest first

    def count(self, severity: str) -> int:
        return sum(1 for f in self.flags if f.severity == severity)


def _load(engine: Engine, asins: set[str], subjects: set[str]):
    """Trend points, reviews and review-count history for the given ASINs."""
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        imported = dict(session.execute(select(ResearchSnapshot.id, ResearchSnapshot.imported_at)).all())
        points = list(session.scalars(select(TrendPoint).where(TrendPoint.subject.in_(subjects)))) if subjects else []
        reviews = list(session.scalars(select(Review).where(Review.asin.in_(asins)))) if asins else []
        products = list(session.scalars(select(Product).where(Product.asin.in_(asins)))) if asins else []
        competitors = list(session.scalars(select(CompetitorAsin).where(CompetitorAsin.asin.in_(asins)))) if asins else []

    by_subject: dict[str, list[TrendPoint]] = defaultdict(list)
    for p in points:
        by_subject[p.subject].append(p)
    reviews_by_asin: dict[str, list[Review]] = defaultdict(list)
    # Same review imported twice: keep one.
    seen = set()
    for r in sorted(reviews, key=lambda r: -r.snapshot_id):
        key = (r.asin, r.review_date, r.title, r.body)
        if key not in seen:
            seen.add(key)
            reviews_by_asin[r.asin].append(r)
    counts: dict[str, dict[int, tuple[datetime, int]]] = defaultdict(dict)
    product_rows: dict[str, list[Product]] = defaultdict(list)
    for row in products + competitors:
        counts[row.asin][row.snapshot_id] = (imported[row.snapshot_id], row.review_count)
    for row in products:
        product_rows[row.asin].append(row)
    history = {asin: list(by_snap.values()) for asin, by_snap in counts.items()}
    return by_subject, reviews_by_asin, history, product_rows, imported


def _product_history(rows: list[Product], imported: dict) -> list[dict]:
    return [
        {"imported": imported[r.snapshot_id], "price": r.price, "monthly_sales": r.monthly_sales,
         "review_count": r.review_count, "rating": r.rating, "bsr": r.bsr}
        for r in sorted(rows, key=lambda r: r.snapshot_id)
    ]


def _validate(market: Market, score: Score, loaded, cfg: dict) -> Validation:
    by_subject, reviews_by_asin, history, product_rows, imported = loaded
    subject, metric, points = pick_trend(market, by_subject)
    season = analyze_season(monthly_series(points), subject, metric, cfg["seasonality"])
    asins = [l.asin for l in market.listings]
    reviews = analyze_reviews(
        [r for a in asins for r in reviews_by_asin.get(a, [])],
        {a: history.get(a, []) for a in asins},
        cfg["reviews"],
    )
    flags = risk_flags(market, score, season, reviews, cfg)
    asin = market.product.asin if market.product else score.asin
    return Validation(asin, season, reviews, flags, _product_history(product_rows.get(asin, []), imported))


def _subjects(market: Market) -> set[str]:
    subjects = {k.phrase for k in market.keywords}
    if market.product:
        subjects.add(market.product.asin)
    return subjects


def validate(engine: Engine, market: Market, score: Score, cfg: dict | None = None) -> Validation:
    cfg = cfg or load_validation_config()
    loaded = _load(engine, {l.asin for l in market.listings}, _subjects(market))
    return _validate(market, score, loaded, cfg)


def validate_all(engine: Engine, markets: list[Market], scores: list[Score],
                 cfg: dict | None = None) -> dict[str, Validation]:
    """Validation for many products with one set of queries."""
    cfg = cfg or load_validation_config()
    asins = {l.asin for m in markets for l in m.listings}
    subjects = set().union(*(_subjects(m) for m in markets)) if markets else set()
    loaded = _load(engine, asins, subjects)
    by_asin = {s.asin: s for s in scores}
    return {
        m.product.asin: _validate(m, by_asin[m.product.asin], loaded, cfg)
        for m in markets if m.product and m.product.asin in by_asin
    }
