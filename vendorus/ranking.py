"""Rank scored products, filter them, and export the ranking."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from vendorus.config import PROJECT_ROOT
from vendorus.db import create_tables
from vendorus.models import ProductCost, ResearchSnapshot
from vendorus.scoring import Score

# Files whose changes should refresh cached scores.
SETTINGS_FILES = (
    PROJECT_ROOT / "config" / "scoring.toml",
    PROJECT_ROOT / "config" / "planning.toml",
    PROJECT_ROOT / "vendorus" / "fees" / "us.toml",
)


@dataclass
class Ranking:
    ranked: list[Score]  # a real score: enough data, doesn't lose money
    needs_data: list[Score]  # too few factors to score
    not_viable: list[Score]  # loses money with no advertising

    @property
    def all(self) -> list[Score]:
        return self.ranked + self.needs_data + self.not_viable


def rank(scores: list[Score]) -> Ranking:
    """Ranked: highest score first; ties go to more coverage, then higher
    margin. Needs data: most coverage first. Not viable: smallest loss first."""
    def margin(s: Score) -> float:
        return float(s.margin_pct) if s.margin_pct is not None else float("-inf")

    ranked = sorted(
        (s for s in scores if s.status == "Scored"),
        key=lambda s: (-s.total, -s.coverage, -margin(s), s.asin),
    )
    needs = sorted(
        (s for s in scores if s.status == "Not enough data"),
        key=lambda s: (-s.coverage, s.asin),
    )
    not_viable = sorted(
        (s for s in scores if s.status == "Not viable"),
        key=lambda s: (-float(s.profit) if s.profit is not None else float("inf"), s.asin),
    )
    return Ranking(ranked, needs, not_viable)


@dataclass
class Filters:
    search: str = ""
    min_score: int = 0
    bands: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    price_min: float | None = None
    price_max: float | None = None
    hide_warnings: bool = False


def apply_filters(scores: list[Score], f: Filters) -> list[Score]:
    needle = f.search.strip().lower()
    out = []
    for s in scores:
        if needle and needle not in s.asin.lower() and needle not in (s.title or "").lower():
            continue
        if f.min_score and (s.total is None or s.status != "Scored" or s.total < f.min_score):
            continue
        if f.bands and s.band not in f.bands:
            continue
        if f.categories and (s.category or "Uncategorised") not in f.categories:
            continue
        price = float(s.price) if s.price is not None else None
        if f.price_min is not None and (price is None or price < f.price_min):
            continue
        if f.price_max is not None and (price is None or price > f.price_max):
            continue
        if f.hide_warnings and s.warnings:
            continue
        out.append(s)
    return out


def _num(value):
    return None if value is None else float(value)


def table(scores: list[Score], numbered: bool = False) -> pd.DataFrame:
    """Rows for the on-screen tables."""
    rows = []
    for i, s in enumerate(scores, start=1):
        row = {"#": i} if numbered else {}
        row.update({
            "ASIN": s.asin,
            "Product": s.title or "",
            "Score": s.total if s.status == "Scored" else None,
            "Band": s.band or s.status,
            "Coverage": f"{s.coverage}/{len(s.factors)}",
            "Price": _num(s.price),
            "Profit/unit": _num(s.profit),
            "Margin %": _num(s.margin_pct),
            "Break-even": _num(s.break_even),
            "Sales/mo (est.)": s.monthly_sales,
            "Warnings": len(s.warnings),
            "Researched": s.researched_on,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def export_frame(ranking: Ranking) -> pd.DataFrame:
    """Everything behind the ranking, one row per product, for a CSV."""
    rows = []
    groups = (("Ranked", ranking.ranked), ("Needs data", ranking.needs_data),
              ("Not viable", ranking.not_viable))
    for group, scores in groups:
        for i, s in enumerate(scores, start=1):
            row = {
                "list": group,
                "rank": i if group == "Ranked" else None,
                "asin": s.asin,
                "title": s.title,
                "category": s.category,
                "score": s.total if s.status == "Scored" else None,
                "status": s.status,
                "band": s.band,
                "factors_scored": s.coverage,
                "price": _num(s.price),
                "organic_profit_per_unit": _num(s.profit),
                "organic_margin_pct": _num(s.margin_pct),
                "break_even_price": _num(s.break_even),
                "monthly_sales_estimate": s.monthly_sales,
                "main_keyword": s.main_keyword,
                "listings_in_market": s.listings,
                "warnings": " | ".join(s.warnings),
                "researched_on": s.researched_on.date().isoformat() if s.researched_on else None,
            }
            for f in s.factors:
                row[f"{f.spec.key}_points"] = f.points
                row[f"{f.spec.key}_based_on"] = f.value if f.scored else f"no data: {f.missing}"
            rows.append(row)
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class DataVersion:
    """Changes whenever anything that affects scores changes."""

    latest_import: int | None
    costs_count: int
    costs_updated: str | None
    settings: tuple[float, ...] = field(default=())


def data_version(engine: Engine, settings_files: tuple[Path, ...] = SETTINGS_FILES) -> DataVersion:
    create_tables(engine)
    with Session(engine) as session:
        latest = session.scalar(select(func.max(ResearchSnapshot.id)))
        count, updated = session.execute(
            select(func.count(ProductCost.id), func.max(ProductCost.updated_at))
        ).one()
    mtimes = tuple(p.stat().st_mtime if p.exists() else 0.0 for p in settings_files)
    return DataVersion(latest, count, str(updated) if updated else None, mtimes)
