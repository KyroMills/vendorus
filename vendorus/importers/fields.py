"""What each import type expects: fields, value types, limits and the column
names that are accepted for each field.

Column names are matched loosely: case, spaces, punctuation and anything in
brackets are ignored, so "Price ($)", "price" and "PRICE" all mean price.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class ImportKind(str, Enum):
    PRODUCTS = "products"
    KEYWORDS = "keywords"
    COMPETITORS = "competitors"
    TRENDS = "trends"
    REVIEWS = "reviews"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    # asin, text, phrase (keyword), target (ASIN or keyword), number, integer,
    # date, month (any date in the month; stored as the 1st)
    type: str
    required: bool = False
    aliases: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None  # "in" or "lb": values are converted to this unit

    @property
    def label(self) -> str:
        return self.name.replace("_", " ")

    @property
    def accepted_columns(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class FieldError(ValueError):
    """A single value failed a check. The message is shown to the user."""


# Values that mean "no data". Treated as blank, not as an error.
BLANK_VALUES = {"", "n/a", "na", "-", "--", "none", "null", "nan"}

_ASIN = re.compile(r"^B0[A-Z0-9]{8}$")
_ISBN10 = re.compile(r"^\d{9}[\dX]$")  # books use their ISBN-10 as the ASIN
_CURRENCY_AND_SEPARATORS = re.compile(r"[$£€,\s]")


def is_asin(value: str) -> bool:
    return bool(_ASIN.match(value) or _ISBN10.match(value))


def normalize_header(header: str) -> str:
    header = re.sub(r"\(.*?\)|\[.*?\]", "", header.lower())
    return re.sub(r"[^a-z0-9]", "", header)


def normalize_phrase(text: str) -> str:
    return " ".join(text.lower().split())


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y",
                 "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y")
_MONTH_FORMATS = ("%Y-%m", "%Y/%m", "%m/%Y", "%m-%Y", "%b %Y", "%B %Y", "%b-%Y", "%b-%y", "%B-%Y")


def parse_date(text: str, month_only: bool = False) -> date | None:
    """Read common date spellings. US order (month/day) for slashes, since
    the marketplace is Amazon US."""
    text = text.strip()
    if "T" in text and text[:4].isdigit():
        text = text.split("T")[0]  # ISO timestamps
    formats = _DATE_FORMATS + (_MONTH_FORMATS if month_only else ())
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _format_limit(limit: float) -> str:
    return str(int(limit)) if float(limit).is_integer() else str(limit)


def parse_value(spec: FieldSpec, raw: str | None):
    """Turn one raw cell into a clean value, or raise FieldError.

    Returns None for blank cells; whether blank is allowed is decided by the caller.
    """
    text = (raw or "").strip()
    if text.lower() in BLANK_VALUES:
        return None

    if spec.type == "text":
        return text
    if spec.type in ("date", "month"):
        parsed = parse_date(text, month_only=spec.type == "month")
        if parsed is None:
            kind = "month" if spec.type == "month" else "date"
            raise FieldError(f"{spec.label} '{text}' is not a {kind} (try 2026-03-15 or Mar 2026)")
        return parsed.replace(day=1) if spec.type == "month" else parsed
    if spec.type == "phrase":
        return normalize_phrase(text)
    if spec.type in ("asin", "target"):
        upper = text.upper()
        if is_asin(upper):
            return upper
        if spec.type == "target":
            return normalize_phrase(text)  # a keyword phrase
        raise FieldError(f"{spec.label} '{text}' is not a valid ASIN")

    try:
        number = float(_CURRENCY_AND_SEPARATORS.sub("", text))
    except ValueError:
        raise FieldError(f"{spec.label} '{text}' is not a number") from None
    if not math.isfinite(number):
        raise FieldError(f"{spec.label} '{text}' is not a number")
    if spec.type == "integer":
        if not number.is_integer():
            raise FieldError(f"{spec.label} '{text}' must be a whole number")
        number = int(number)
    if spec.minimum is not None and number < spec.minimum:
        if spec.minimum == 0:
            raise FieldError(f"{spec.label} '{text}' cannot be negative")
        raise FieldError(f"{spec.label} '{text}' must be at least {_format_limit(spec.minimum)}")
    if spec.maximum is not None and number > spec.maximum:
        raise FieldError(f"{spec.label} '{text}' must be at most {_format_limit(spec.maximum)}")
    return number


# Shared listing fields.
_TITLE = FieldSpec("title", "text", aliases=("product title", "product name", "name", "product details"))
_PRICE = FieldSpec("price", "number", aliases=("price usd", "list price", "sale price"), minimum=0)
_REVIEWS = FieldSpec(
    "review_count", "integer", minimum=0,
    aliases=("reviews", "review count", "number of reviews", "ratings", "ratings count", "number of ratings"),
)
_RATING = FieldSpec(
    "rating", "number", minimum=0, maximum=5,
    aliases=("star rating", "stars", "review rating", "average rating"),
)
_SALES = FieldSpec(
    "monthly_sales", "integer", minimum=0,
    aliases=("sales", "est monthly sales", "estimated monthly sales", "asin sales"),
)
_REVENUE = FieldSpec(
    "monthly_revenue", "number", minimum=0,
    aliases=("revenue", "est monthly revenue", "estimated monthly revenue", "asin revenue"),
)

SPECS: dict[ImportKind, tuple[FieldSpec, ...]] = {
    ImportKind.PRODUCTS: (
        FieldSpec("asin", "asin", required=True, aliases=("product asin",)),
        _TITLE,
        FieldSpec("brand", "text"),
        FieldSpec("category", "text", aliases=("main category",)),
        _PRICE,
        FieldSpec("bsr", "integer", minimum=1, aliases=("best sellers rank", "sales rank", "rank")),
        _REVIEWS,
        _RATING,
        _SALES,
        _REVENUE,
        # Packaged size and weight. A unit in brackets, e.g. "Weight (oz)" or
        # "Length (cm)", is converted; no unit means inches and pounds.
        FieldSpec("length_in", "number", minimum=0, unit="in", aliases=("length", "package length", "item length")),
        FieldSpec("width_in", "number", minimum=0, unit="in", aliases=("width", "package width", "item width")),
        FieldSpec("height_in", "number", minimum=0, unit="in", aliases=("height", "package height", "item height")),
        FieldSpec(
            "weight_lb", "number", minimum=0, unit="lb",
            aliases=("weight", "package weight", "item weight", "shipping weight", "unit weight"),
        ),
    ),
    ImportKind.KEYWORDS: (
        FieldSpec(
            "phrase", "phrase", required=True,
            aliases=("keyword", "keyword phrase", "keywords", "search term"),
        ),
        FieldSpec("search_volume", "integer", minimum=0, aliases=("search volume", "monthly search volume", "volume")),
        FieldSpec("competing_products", "integer", minimum=0, aliases=("competing products", "results")),
        FieldSpec("product_asin", "asin", aliases=("asin", "product asin")),
    ),
    ImportKind.COMPETITORS: (
        FieldSpec("asin", "asin", required=True, aliases=("competitor asin",)),
        FieldSpec(
            "competes_with", "target", required=True,
            aliases=("competes with", "target", "target asin", "parent asin", "keyword", "keyword phrase"),
        ),
        _TITLE,
        FieldSpec("brand", "text"),
        _PRICE,
        _REVIEWS,
        _RATING,
        _SALES,
        _REVENUE,
    ),
    # Monthly history for a keyword (search volume) or an ASIN (sales), e.g.
    # from a research tool's history export, Keepa or Google Trends.
    ImportKind.TRENDS: (
        FieldSpec(
            "subject", "target", required=True,
            aliases=("keyword", "keyword phrase", "search term", "asin", "product asin", "product"),
        ),
        FieldSpec("month", "month", required=True, aliases=("date", "period", "month year")),
        FieldSpec(
            "value", "number", required=True, minimum=0,
            aliases=("search volume", "volume", "sales", "units", "monthly sales", "interest", "trend"),
        ),
        FieldSpec("metric", "text", aliases=("measure", "type")),
    ),
    # Customer reviews for a product or its competitors.
    ImportKind.REVIEWS: (
        FieldSpec("asin", "asin", required=True, aliases=("product asin", "reviewed asin")),
        FieldSpec("rating", "integer", required=True, minimum=1, maximum=5,
                  aliases=("stars", "star rating", "review rating", "score")),
        FieldSpec("review_date", "date", aliases=("date", "review date", "posted", "created")),
        FieldSpec("title", "text", aliases=("review title", "headline", "summary")),
        FieldSpec("body", "text", aliases=("review", "text", "content", "review text", "comment", "body text")),
        FieldSpec("verified", "text", aliases=("verified purchase", "verified buyer")),
    ),
}

# Fields that identify a row; two rows with the same values are duplicates.
KEY_FIELDS: dict[ImportKind, tuple[str, ...]] = {
    ImportKind.PRODUCTS: ("asin",),
    ImportKind.KEYWORDS: ("phrase",),
    ImportKind.COMPETITORS: ("asin", "competes_with"),
    ImportKind.TRENDS: ("subject", "month", "metric"),
    ImportKind.REVIEWS: ("asin", "review_date", "title", "body"),
}


def trend_metric(value_column: str) -> str:
    """What a trend's numbers measure, from the value column's name."""
    name = normalize_header(value_column)
    if any(word in name for word in ("sales", "units")):
        return "sales"
    if any(word in name for word in ("interest", "trend")):
        return "interest"
    return "search_volume"


_UNIT_FACTORS = {
    "lb": {"lb": 1, "lbs": 1, "pound": 1, "pounds": 1, "oz": 1 / 16, "ounce": 1 / 16,
           "ounces": 1 / 16, "kg": 2.20462262185, "g": 0.00220462262185, "grams": 0.00220462262185},
    "in": {"in": 1, "inch": 1, "inches": 1, "cm": 1 / 2.54, "mm": 1 / 25.4},
}
_BRACKETED = re.compile(r"[\(\[]\s*([a-z.]+)\s*[\)\]]")


def unit_factor(spec: FieldSpec, column: str) -> float:
    """Multiplier that converts a column's values into the field's unit.

    Raises FieldError for a unit that can't be converted, so a weight in an
    unknown unit is never silently read as pounds.
    """
    if spec.unit is None:
        return 1
    match = _BRACKETED.search(column.lower())
    if not match:
        return 1
    unit = match.group(1).strip(".")
    factors = _UNIT_FACTORS[spec.unit]
    if unit not in factors:
        raise FieldError(
            f"column '{column}': unit '{unit}' not understood for {spec.label} "
            f"(use one of: {', '.join(factors)})"
        )
    return factors[unit]


def match_columns(
    columns: list[str], specs: tuple[FieldSpec, ...]
) -> tuple[dict[str, str], list[str]]:
    """Match file columns to fields.

    Returns ({field name: column name}, [columns that were not used]).
    If two columns match the same field, the first one wins.
    """
    lookup: dict[str, str] = {}
    for spec in specs:
        for alias in spec.accepted_columns:
            lookup.setdefault(normalize_header(alias), spec.name)

    mapping: dict[str, str] = {}
    unused: list[str] = []
    for column in columns:
        field = lookup.get(normalize_header(column))
        if field and field not in mapping:
            mapping[field] = column
        elif column.strip():
            unused.append(column)
    return mapping, unused
