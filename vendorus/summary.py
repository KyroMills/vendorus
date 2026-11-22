"""Headline numbers for the dashboard overview."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from vendorus.db import create_tables
from vendorus.models import Keyword, Product, ProductCost, ResearchSnapshot


@dataclass(frozen=True)
class Summary:
    products: int  # distinct ASINs imported
    keywords: int  # distinct keyword phrases
    imports: int
    costed: int  # ASINs with a unit cost entered
    last_import: datetime | None


def summary(engine: Engine) -> Summary:
    create_tables(engine)
    with Session(engine) as session:
        return Summary(
            products=session.scalar(select(func.count(func.distinct(Product.asin)))) or 0,
            keywords=session.scalar(select(func.count(func.distinct(Keyword.phrase)))) or 0,
            imports=session.scalar(select(func.count(ResearchSnapshot.id))) or 0,
            costed=session.scalar(
                select(func.count(ProductCost.id)).where(ProductCost.unit_cost.is_not(None))
            ) or 0,
            last_import=session.scalar(select(func.max(ResearchSnapshot.imported_at))),
        )
