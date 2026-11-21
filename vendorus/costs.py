"""Read and save your per-product costs, and gather everything the profit
engine needs for one product."""
from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from vendorus.db import create_tables
from vendorus.fees import FeeTables
from vendorus.models import Product, ProductCost

COST_FIELDS = (
    "fee_category", "planned_price", "unit_cost", "freight_per_unit", "duty_pct", "prep_per_unit",
    "other_per_unit", "length_in", "width_in", "height_in", "weight_lb", "apparel", "dangerous_goods",
    "returns_pct", "storage_months", "ads_enabled", "ad_spend_pct", "planned_units_per_month", "notes",
)


def latest_products(engine: Engine) -> list[Product]:
    """The most recently imported row for each ASIN, newest imports first."""
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        rows = session.scalars(select(Product).order_by(Product.snapshot_id.desc(), Product.id))
        seen: dict[str, Product] = {}
        for product in rows:
            seen.setdefault(product.asin, product)
        return list(seen.values())


def get_cost(engine: Engine, asin: str) -> ProductCost | None:
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        return session.scalars(select(ProductCost).where(ProductCost.asin == asin)).first()


def save_cost(engine: Engine, asin: str, **values) -> ProductCost:
    unknown = set(values) - set(COST_FIELDS)
    if unknown:
        raise ValueError(f"Unknown cost fields: {sorted(unknown)}")
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        cost = session.scalars(select(ProductCost).where(ProductCost.asin == asin)).first()
        if cost is None:
            cost = ProductCost(asin=asin)
            session.add(cost)
        for name, value in values.items():
            setattr(cost, name, float(value) if isinstance(value, Decimal) else value)
    return cost


def _words(text: str) -> str:
    text = text.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def suggest_fee_category(tables: FeeTables, category: str | None) -> str | None:
    """Match an Amazon store category (e.g. "Home & Kitchen") to a referral
    fee category. Returns None rather than guessing when there's no clear match.

    The store category and the fee category are not always the same, so the
    page asks you to confirm the suggestion.
    """
    if not category:
        return None
    wanted = _words(category)
    by_words = {_words(name): name for name in tables.referral}
    if wanted in by_words:
        return by_words[wanted]
    starts = [name for words, name in by_words.items() if words.startswith(wanted) or wanted.startswith(words)]
    return starts[0] if len(starts) == 1 else None
