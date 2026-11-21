"""Database tables for product research data.

Every product, keyword and competitor row belongs to the research snapshot
(import) it came from. Importing again later adds a new point in time instead
of overwriting older numbers, so trends and seasonality can be measured.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResearchSnapshot(Base):
    """One import: where the data came from, when, and how many rows passed."""

    __tablename__ = "research_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # products / keywords / competitors
    source: Mapped[str] = mapped_column(String(50))  # csv now; helium10 later
    label: Mapped[str] = mapped_column(String(255))  # file name or description
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)

    products: Mapped[list[Product]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    keywords: Mapped[list[Keyword]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    competitors: Mapped[list[CompetitorAsin]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class _TracksMissingFields:
    """Optional fields that were blank, so scoring can say "not enough data"
    instead of guessing."""

    missing_fields: Mapped[str] = mapped_column(String(500), default="")

    @property
    def missing(self) -> list[str]:
        return [name for name in self.missing_fields.split(",") if name]


class Product(_TracksMissingFields, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("snapshot_id", "asin"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("research_snapshots.id", ondelete="CASCADE"), index=True
    )
    asin: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(255))
    price: Mapped[float | None] = mapped_column(Float)
    bsr: Mapped[int | None] = mapped_column(Integer)
    review_count: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    monthly_sales: Mapped[int | None] = mapped_column(Integer)
    monthly_revenue: Mapped[float | None] = mapped_column(Float)
    # Packaged dimensions (inches) and weight (pounds); FBA fees depend on them.
    length_in: Mapped[float | None] = mapped_column(Float)
    width_in: Mapped[float | None] = mapped_column(Float)
    height_in: Mapped[float | None] = mapped_column(Float)
    weight_lb: Mapped[float | None] = mapped_column(Float)

    snapshot: Mapped[ResearchSnapshot] = relationship(back_populates="products")


class Keyword(_TracksMissingFields, Base):
    __tablename__ = "keywords"
    __table_args__ = (UniqueConstraint("snapshot_id", "phrase"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("research_snapshots.id", ondelete="CASCADE"), index=True
    )
    phrase: Mapped[str] = mapped_column(String(500), index=True)
    search_volume: Mapped[int | None] = mapped_column(Integer)
    competing_products: Mapped[int | None] = mapped_column(Integer)
    product_asin: Mapped[str | None] = mapped_column(String(10), index=True)

    snapshot: Mapped[ResearchSnapshot] = relationship(back_populates="keywords")


class CompetitorAsin(_TracksMissingFields, Base):
    __tablename__ = "competitor_asins"
    __table_args__ = (UniqueConstraint("snapshot_id", "asin", "competes_with"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("research_snapshots.id", ondelete="CASCADE"), index=True
    )
    asin: Mapped[str] = mapped_column(String(10), index=True)
    # The product ASIN or keyword phrase this listing competes with.
    competes_with: Mapped[str] = mapped_column(String(500), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    price: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    monthly_sales: Mapped[int | None] = mapped_column(Integer)
    monthly_revenue: Mapped[float | None] = mapped_column(Float)

    snapshot: Mapped[ResearchSnapshot] = relationship(back_populates="competitors")


class ProductCost(Base):
    """Your costs and planning choices for one ASIN.

    Kept apart from research snapshots, so re-importing research data never
    overwrites them. Blank fields fall back to the import or to defaults.
    """

    __tablename__ = "product_costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    asin: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    fee_category: Mapped[str | None] = mapped_column(String(255))
    planned_price: Mapped[float | None] = mapped_column(Float)
    # Landed cost parts, per unit.
    unit_cost: Mapped[float | None] = mapped_column(Float)
    freight_per_unit: Mapped[float | None] = mapped_column(Float)
    duty_pct: Mapped[float | None] = mapped_column(Float)
    prep_per_unit: Mapped[float | None] = mapped_column(Float)
    other_per_unit: Mapped[float | None] = mapped_column(Float)
    # Measurements you entered; used instead of imported ones when set.
    length_in: Mapped[float | None] = mapped_column(Float)
    width_in: Mapped[float | None] = mapped_column(Float)
    height_in: Mapped[float | None] = mapped_column(Float)
    weight_lb: Mapped[float | None] = mapped_column(Float)
    apparel: Mapped[bool] = mapped_column(Boolean, default=False)
    dangerous_goods: Mapped[bool] = mapped_column(Boolean, default=False)
    # Planning assumptions; blank means use config/planning.toml.
    returns_pct: Mapped[float | None] = mapped_column(Float)
    storage_months: Mapped[float | None] = mapped_column(Float)
    ads_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ad_spend_pct: Mapped[float | None] = mapped_column(Float)
    planned_units_per_month: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
