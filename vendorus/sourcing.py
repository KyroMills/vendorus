"""Suppliers, quotes and landed cost.

A quote becomes a per-unit landed cost for the quantity you plan to order:

    unit price (at that quantity's price break) x exchange rate
  + packaging x exchange rate
  + freight (per unit, or per shipment / quantity), unless the Incoterm includes it
  + duty % of the unit price, unless the Incoterm is DDP
  + broker, inspection and other per-order fees / quantity
  + tooling / quantity (first order)

Applying a quote copies these parts into the product's costs, so the Profit,
Score, Validate and Opportunities pages use it.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from vendorus.config import PROJECT_ROOT
from vendorus.costs import assumptions_for, get_cost, inputs_for, save_cost
from vendorus.db import create_tables
from vendorus.fees import FeeTables
from vendorus.models import ProductCost, Quote, Supplier
from vendorus.profit import Assumptions, analyze
from vendorus.scoring import ScoringConfig, gather_market, score_market

SOURCING_FILE = PROJECT_ROOT / "config" / "sourcing.toml"
CENT = Decimal("0.01")
ZERO = Decimal("0")

SUPPLIER_FIELDS = ("name", "country", "platform", "contact_name", "email", "website", "status", "notes")
QUOTE_FIELDS = (
    "supplier_id", "asin", "received_on", "valid_until", "currency", "exchange_rate", "unit_price", "moq",
    "price_breaks", "order_qty", "incoterm", "payment_terms", "packaging_per_unit", "tooling_cost",
    "sample_cost", "shipping_method", "freight_mode", "freight_amount", "transit_days", "duty_pct",
    "broker_fee", "inspection_fee", "other_fees", "production_days", "notes",
)


def load_sourcing_config(path: Path = SOURCING_FILE) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _d(value) -> Decimal:
    return ZERO if value is None else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --- suppliers ----------------------------------------------------------------------

def save_supplier(engine: Engine, supplier_id: int | None = None, **values) -> int:
    unknown = set(values) - set(SUPPLIER_FIELDS)
    if unknown:
        raise ValueError(f"Unknown supplier fields: {sorted(unknown)}")
    if supplier_id is None and not (values.get("name") or "").strip():
        raise ValueError("A supplier needs a name.")
    create_tables(engine)
    with Session(engine) as session, session.begin():
        supplier = session.get(Supplier, supplier_id) if supplier_id else Supplier()
        if supplier is None:
            raise ValueError(f"No supplier {supplier_id}")
        for name, value in values.items():
            setattr(supplier, name, value.strip() if isinstance(value, str) else value)
        session.add(supplier)
        session.flush()
        return supplier.id


def list_suppliers(engine: Engine) -> list[tuple[Supplier, int]]:
    """Suppliers with their number of quotes, most recently updated first."""
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        counts = dict(session.execute(select(Quote.supplier_id, func.count(Quote.id)).group_by(Quote.supplier_id)).all())
        suppliers = session.scalars(select(Supplier).order_by(Supplier.updated_at.desc(), Supplier.id.desc())).all()
        return [(s, counts.get(s.id, 0)) for s in suppliers]


def delete_supplier(engine: Engine, supplier_id: int) -> None:
    """Delete a supplier and its quotes. Products using one of its quotes keep
    their copied costs but lose the link."""
    with Session(engine) as session, session.begin():
        supplier = session.get(Supplier, supplier_id)
        if supplier is None:
            return
        quote_ids = [q.id for q in supplier.quotes]
        if quote_ids:
            for cost in session.scalars(select(ProductCost).where(ProductCost.quote_id.in_(quote_ids))):
                cost.quote_id = None
        session.delete(supplier)


# --- quotes ---------------------------------------------------------------------------

def save_quote(engine: Engine, quote_id: int | None = None, **values) -> int:
    unknown = set(values) - set(QUOTE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown quote fields: {sorted(unknown)}")
    if "price_breaks" in values and not isinstance(values["price_breaks"], str):
        values["price_breaks"] = json.dumps(sorted([int(q), float(p)] for q, p in values["price_breaks"]))
    create_tables(engine)
    with Session(engine) as session, session.begin():
        quote = session.get(Quote, quote_id) if quote_id else Quote()
        if quote is None:
            raise ValueError(f"No quote {quote_id}")
        for name, value in values.items():
            setattr(quote, name, value)
        # Column defaults only apply when saved; set them now so the checks see them.
        for name, default in (("currency", "USD"), ("exchange_rate", 1.0), ("incoterm", "FOB"),
                              ("price_breaks", "[]"), ("freight_mode", "per_shipment")):
            if getattr(quote, name) is None:
                setattr(quote, name, default)
        if quote.currency == "USD":
            quote.exchange_rate = 1.0
        if quote.unit_price is None or quote.unit_price < 0:
            raise ValueError("A quote needs a unit price.")
        if not quote.exchange_rate or quote.exchange_rate <= 0:
            raise ValueError("The exchange rate must be greater than zero.")
        session.add(quote)
        session.flush()
        return quote.id


def quotes_for(engine: Engine, asin: str) -> list[Quote]:
    create_tables(engine)
    with Session(engine, expire_on_commit=False) as session:
        return list(session.scalars(
            select(Quote).options(selectinload(Quote.supplier))
            .where(Quote.asin == asin).order_by(Quote.id)
        ))


def get_quote(engine: Engine, quote_id: int) -> Quote | None:
    with Session(engine, expire_on_commit=False) as session:
        return session.scalars(
            select(Quote).options(selectinload(Quote.supplier)).where(Quote.id == quote_id)
        ).first()


def delete_quote(engine: Engine, quote_id: int) -> None:
    with Session(engine) as session, session.begin():
        quote = session.get(Quote, quote_id)
        if quote is not None:
            for cost in session.scalars(select(ProductCost).where(ProductCost.quote_id == quote_id)):
                cost.quote_id = None
            session.delete(quote)


# --- landed cost -------------------------------------------------------------------------

@dataclass
class Landed:
    quantity: int
    unit_price: Decimal  # in the quote's currency, after price breaks
    goods: Decimal  # unit price in USD
    packaging: Decimal
    freight: Decimal
    duty: Decimal
    fees: Decimal  # broker + inspection + other, per unit
    tooling: Decimal  # per unit, first order
    cash_needed: Decimal  # the whole first order, plus samples
    days_to_amazon: int | None
    warnings: list[str] = field(default_factory=list)

    @property
    def per_unit(self) -> Decimal:
        return self.goods + self.packaging + self.freight + self.duty + self.fees + self.tooling

    @property
    def lines(self) -> list[tuple[str, Decimal]]:
        return [("Product", self.goods), ("Packaging and prep", self.packaging), ("Freight", self.freight),
                ("Duty", self.duty), ("Broker, inspection, other fees", self.fees),
                ("Tooling (first order)", self.tooling)]


def price_breaks(quote: Quote) -> list[tuple[int, Decimal]]:
    try:
        return sorted((int(q), _d(p)) for q, p in json.loads(quote.price_breaks or "[]"))
    except (ValueError, TypeError):
        return []


def unit_price_at(quote: Quote, quantity: int) -> Decimal:
    """The price for this quantity: the best price break you qualify for,
    otherwise the base unit price."""
    price = _d(quote.unit_price)
    for minimum, tier_price in price_breaks(quote):
        if quantity >= minimum:
            price = tier_price
    return price


def order_quantity(quote: Quote) -> int:
    return int(quote.order_qty or quote.moq or 1)


def landed_cost(quote: Quote, cfg: dict, quantity: int | None = None, today: date | None = None) -> Landed:
    today = today or date.today()
    qty = max(1, int(quantity or order_quantity(quote)))
    rate = _d(quote.exchange_rate or 1)
    terms = cfg["incoterms"].get(quote.incoterm, {"freight": False, "duty": False})
    warnings = []

    unit_price = unit_price_at(quote, qty)
    goods = _money(unit_price * rate)
    packaging = _money(_d(quote.packaging_per_unit) * rate)

    if terms["freight"]:
        freight = ZERO
        if quote.freight_amount:
            warnings.append(f"Freight entered but ignored: {quote.incoterm} includes shipping in the price.")
    elif quote.freight_mode == "per_unit":
        freight = _money(_d(quote.freight_amount))
    else:
        freight = _money(_d(quote.freight_amount) / qty)

    if terms["duty"]:
        duty = ZERO
        if quote.duty_pct:
            warnings.append("Duty entered but ignored: DDP includes US duty in the price.")
    else:
        duty = _money(goods * _d(quote.duty_pct) / 100)

    fees = _money((_d(quote.broker_fee) + _d(quote.inspection_fee) + _d(quote.other_fees)) / qty)
    tooling = _money(_d(quote.tooling_cost) * rate / qty)
    landed = Landed(qty, unit_price, goods, packaging, freight, duty, fees, tooling, ZERO, None, warnings)
    landed.cash_needed = _money(landed.per_unit * qty + _d(quote.sample_cost) * rate)

    if quote.production_days is not None or quote.transit_days is not None:
        landed.days_to_amazon = (int(quote.production_days or 0) + int(quote.transit_days or 0)
                                 + int(cfg["receiving_buffer_days"]))
    if quote.moq and qty < quote.moq:
        warnings.append(f"Order of {qty:,} is below the minimum order of {quote.moq:,}.")
    if quote.valid_until and quote.valid_until < today:
        warnings.append(f"Quote expired on {quote.valid_until:%b %d, %Y}; ask for a new one.")
    if not terms["duty"] and not quote.duty_pct:
        warnings.append("No duty rate entered. Look up your product's tariff code before ordering.")
    if not terms["freight"] and not quote.freight_amount:
        warnings.append("No freight entered: landed cost is missing shipping.")
    return landed


def cost_values(quote: Quote, landed: Landed) -> dict:
    """The product-cost fields a quote sets. Duty is entered as a percent of
    the unit cost, matching how the Profit page calculates it."""
    duty_pct = ZERO if landed.goods == 0 else landed.duty / landed.goods * 100
    return {
        "unit_cost": landed.goods,
        "duty_pct": duty_pct.quantize(Decimal("0.0001")),
        "freight_per_unit": landed.freight,
        "prep_per_unit": landed.packaging,
        "other_per_unit": landed.fees + landed.tooling,
    }


def apply_quote(engine: Engine, quote_id: int, cfg: dict | None = None) -> ProductCost:
    """Use a quote: copy its landed-cost parts into the product's costs."""
    cfg = cfg or load_sourcing_config()
    quote = get_quote(engine, quote_id)
    if quote is None:
        raise ValueError(f"No quote {quote_id}")
    values = cost_values(quote, landed_cost(quote, cfg))
    return save_cost(engine, quote.asin, quote_id=quote.id, **values)


def quote_label(quote: Quote) -> str:
    supplier = quote.supplier.name if quote.supplier else "?"
    received = f", {quote.received_on:%b %d, %Y}" if quote.received_on else ""
    return f"{supplier} · {quote.incoterm} · {order_quantity(quote):,} units{received}"


# --- comparing quotes ------------------------------------------------------------------

@dataclass
class QuoteResult:
    quote: Quote
    landed: Landed
    profit: Decimal | None
    margin_pct: Decimal | None
    break_even: Decimal | None
    score: float | None
    score_status: str
    in_use: bool


def _what_if_cost(saved: ProductCost | None, asin: str, values: dict) -> ProductCost:
    """An unsaved copy of the product's costs with a quote's numbers in."""
    copy = ProductCost(asin=asin)
    if saved is not None:
        for column in ProductCost.__table__.columns.keys():
            if column not in ("id",):
                setattr(copy, column, getattr(saved, column))
    for name, value in values.items():
        setattr(copy, name, float(value))
    return copy


def compare_quotes(
    engine: Engine, asin: str, tables: FeeTables, planning: Assumptions, scoring: ScoringConfig,
    cfg: dict | None = None, today: date | None = None,
) -> list[QuoteResult]:
    """Every quote for a product, with the profit and opportunity score the
    product would have if you used it. Cheapest landed cost first."""
    cfg = cfg or load_sourcing_config()
    market = gather_market(engine, asin)
    saved = get_cost(engine, asin)
    results = []
    for quote in quotes_for(engine, asin):
        landed = landed_cost(quote, cfg, today=today)
        what_if = _what_if_cost(saved, asin, cost_values(quote, landed))
        inputs = inputs_for(market.product, what_if)
        analysis = analyze(tables, tables.default_period(today or date.today()), inputs,
                           assumptions_for(planning, what_if)) if inputs else None
        market.cost = what_if
        score = score_market(market, tables, planning, scoring, today)
        organic = analysis.organic if analysis else None
        results.append(QuoteResult(
            quote=quote, landed=landed,
            profit=organic.profit if organic else None,
            margin_pct=organic.margin_pct if organic else None,
            break_even=analysis.break_even_organic if analysis else None,
            score=score.total if score.status == "Scored" else None,
            score_status=score.status,
            in_use=bool(saved and saved.quote_id == quote.id),
        ))
    results.sort(key=lambda r: (r.landed.per_unit, r.quote.id))
    return results
