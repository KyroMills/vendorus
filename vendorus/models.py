"""Database tables for product research data.

Every product, keyword and competitor row belongs to the research snapshot
(import) it came from. Importing again later adds a new point in time instead
of overwriting older numbers, so trends and seasonality can be measured.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    trends: Mapped[list[TrendPoint]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[Review]] = relationship(
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
    brand: Mapped[str | None] = mapped_column(String(255))
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
    # The supplier quote these costs were copied from, if any (Phase 6).
    quote_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )


class TrendPoint(_TracksMissingFields, Base):
    """One month of history for a keyword (search volume) or ASIN (sales)."""

    __tablename__ = "trend_points"
    __table_args__ = (UniqueConstraint("snapshot_id", "subject", "month", "metric"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("research_snapshots.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(500), index=True)  # ASIN or keyword phrase
    month: Mapped[date] = mapped_column(Date)  # first day of the month
    value: Mapped[float] = mapped_column(Float)
    metric: Mapped[str] = mapped_column(String(30), default="search_volume")

    snapshot: Mapped[ResearchSnapshot] = relationship(back_populates="trends")


class Review(_TracksMissingFields, Base):
    """A customer review of a product or one of its competitors."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("research_snapshots.id", ondelete="CASCADE"), index=True
    )
    asin: Mapped[str] = mapped_column(String(10), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    review_date: Mapped[date | None] = mapped_column(Date)
    title: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[str | None] = mapped_column(String(20))

    snapshot: Mapped[ResearchSnapshot] = relationship(back_populates="reviews")


class ProductValidation(Base):
    """Your validation checklist and go / no-go decision for one ASIN."""

    __tablename__ = "product_validation"

    id: Mapped[int] = mapped_column(primary_key=True)
    asin: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    decision: Mapped[str] = mapped_column(String(20), default="Undecided")
    checklist: Mapped[str] = mapped_column(Text, default="{}")  # JSON: item -> done
    notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )


class Supplier(Base):
    """A supplier you've found or talked to. Stays in your local database."""

    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(100))
    platform: Mapped[str | None] = mapped_column(String(100))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default="Prospect")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )

    quotes: Mapped[list[Quote]] = relationship(back_populates="supplier", cascade="all, delete-orphan")


class Quote(Base):
    """One supplier's price for one product (ASIN).

    Product price, packaging, tooling and samples are in `currency`;
    freight and import fees are in US dollars.
    """

    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="CASCADE"), index=True)
    asin: Mapped[str] = mapped_column(String(10), index=True)
    received_on: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    exchange_rate: Mapped[float] = mapped_column(Float, default=1.0)  # US dollars per 1 unit of currency
    unit_price: Mapped[float] = mapped_column(Float)
    moq: Mapped[int | None] = mapped_column(Integer)
    price_breaks: Mapped[str] = mapped_column(Text, default="[]")  # JSON [[min quantity, unit price], ...]
    order_qty: Mapped[int | None] = mapped_column(Integer)  # the quantity you plan to order
    incoterm: Mapped[str] = mapped_column(String(10), default="FOB")
    payment_terms: Mapped[str | None] = mapped_column(String(255))
    packaging_per_unit: Mapped[float] = mapped_column(Float, default=0.0)
    tooling_cost: Mapped[float] = mapped_column(Float, default=0.0)
    sample_cost: Mapped[float] = mapped_column(Float, default=0.0)
    shipping_method: Mapped[str | None] = mapped_column(String(50))
    freight_mode: Mapped[str] = mapped_column(String(20), default="per_shipment")  # or per_unit
    freight_amount: Mapped[float] = mapped_column(Float, default=0.0)  # USD
    transit_days: Mapped[int | None] = mapped_column(Integer)
    duty_pct: Mapped[float] = mapped_column(Float, default=0.0)
    broker_fee: Mapped[float] = mapped_column(Float, default=0.0)  # USD per order
    inspection_fee: Mapped[float] = mapped_column(Float, default=0.0)  # USD per order
    other_fees: Mapped[float] = mapped_column(Float, default=0.0)  # USD per order
    production_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    supplier: Mapped[Supplier] = relationship(back_populates="quotes")
