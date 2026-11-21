"""The import pipeline, shared by every data source.

A source (a CSV file today, the Helium 10 MCP connector later) only has to
provide column names and raw rows. Matching, checking and saving happen here,
so every source gets the same rules and the same report.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from vendorus.db import create_tables
from vendorus.importers.fields import (
    KEY_FIELDS,
    SPECS,
    FieldError,
    ImportKind,
    match_columns,
    parse_value,
    unit_factor,
)
from vendorus.models import CompetitorAsin, Keyword, Product, ResearchSnapshot

MODELS = {
    ImportKind.PRODUCTS: Product,
    ImportKind.KEYWORDS: Keyword,
    ImportKind.COMPETITORS: CompetitorAsin,
}


@dataclass(frozen=True)
class RawRow:
    number: int  # row number as a spreadsheet shows it (the header is row 1)
    values: Mapping[str, str]


class RowSource(Protocol):
    source_name: str  # "csv"; later "helium10"
    label: str  # file name or description, shown in import history

    def columns(self) -> list[str]: ...

    def rows(self) -> Iterator[RawRow]: ...


class ImportFileError(Exception):
    """The whole file cannot be imported (empty, unreadable, no ASIN column...)."""


@dataclass(frozen=True)
class RowError:
    row: int
    reasons: list[str]

    def __str__(self) -> str:
        return f"row {self.row}: " + "; ".join(self.reasons)


@dataclass
class ImportReport:
    kind: ImportKind
    source_name: str
    label: str
    snapshot_id: int
    rows_total: int
    rows_accepted: int
    rejected: list[RowError] = field(default_factory=list)
    unused_columns: list[str] = field(default_factory=list)  # not recognised
    absent_fields: list[str] = field(default_factory=list)  # optional, no column

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected)

    def summary(self) -> str:
        return (
            f"{self.rows_accepted} of {self.rows_total} {self.kind.value} rows imported, "
            f"{self.rows_rejected} rejected"
        )


def run_import(engine: Engine, source: RowSource, kind: ImportKind | str) -> ImportReport:
    """Check every row from `source` and save the good ones as a new snapshot.

    Bad rows are skipped and listed in the report with their reasons; they
    never stop the good rows from being saved.
    """
    kind = ImportKind(kind)
    specs = SPECS[kind]
    mapping, unused = match_columns(source.columns(), specs)

    missing_required = [spec for spec in specs if spec.required and spec.name not in mapping]
    if missing_required:
        wanted = "; ".join(
            f"{spec.label} (accepted names: {', '.join(spec.accepted_columns)})"
            for spec in missing_required
        )
        found = ", ".join(c for c in source.columns() if c.strip()) or "none"
        raise ImportFileError(f"Required column missing: {wanted}. Columns found: {found}.")

    try:
        factors = {
            spec.name: unit_factor(spec, mapping[spec.name]) for spec in specs if spec.name in mapping
        }
    except FieldError as exc:
        raise ImportFileError(str(exc)[0].upper() + str(exc)[1:] + ".") from None

    records: list[dict] = []
    rejected: list[RowError] = []
    first_seen: dict[tuple, int] = {}
    total = 0

    for raw in source.rows():
        total += 1
        record: dict = {}
        reasons: list[str] = []
        blank: list[str] = []
        for spec in specs:
            column = mapping.get(spec.name)
            try:
                value = parse_value(spec, raw.values.get(column, "") if column else "")
            except FieldError as exc:
                reasons.append(str(exc))
                continue
            if value is None:
                if spec.required:
                    reasons.append(f"{spec.label} is missing")
                else:
                    blank.append(spec.name)
            elif factors.get(spec.name, 1) != 1:
                value = round(value * factors[spec.name], 4)
            record[spec.name] = value

        if not reasons:
            key = tuple(record[name] for name in KEY_FIELDS[kind])
            if key in first_seen:
                reasons.append(f"duplicate of row {first_seen[key]}")
            else:
                first_seen[key] = raw.number

        if reasons:
            rejected.append(RowError(raw.number, reasons))
        else:
            record["missing_fields"] = ",".join(blank)
            records.append(record)

    if total == 0:
        raise ImportFileError("The file has a header row but no data rows.")

    create_tables(engine)
    model = MODELS[kind]
    with Session(engine) as session, session.begin():
        snapshot = ResearchSnapshot(
            kind=kind.value,
            source=source.source_name,
            label=source.label,
            rows_total=total,
            rows_accepted=len(records),
            rows_rejected=len(rejected),
        )
        session.add(snapshot)
        session.flush()
        session.add_all(model(snapshot_id=snapshot.id, **record) for record in records)
        snapshot_id = snapshot.id

    return ImportReport(
        kind=kind,
        source_name=source.source_name,
        label=source.label,
        snapshot_id=snapshot_id,
        rows_total=total,
        rows_accepted=len(records),
        rejected=rejected,
        unused_columns=unused,
        absent_fields=[spec.name for spec in specs if spec.name not in mapping],
    )


def import_history(engine: Engine, limit: int = 50) -> list[ResearchSnapshot]:
    """Most recent imports first."""
    create_tables(engine)
    with Session(engine) as session:
        query = select(ResearchSnapshot).order_by(ResearchSnapshot.id.desc()).limit(limit)
        return list(session.scalars(query))
