"""Read research data from a CSV file (any tool's export, or a hand-made sheet)."""
from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.engine import Engine

from vendorus.importers.base import ImportFileError, ImportReport, RawRow, run_import
from vendorus.importers.fields import ImportKind

# Excel on Windows often saves as cp1252; latin-1 accepts any byte as a last resort.
_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def _decode(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFileError("Could not read the file's text encoding.")  # pragma: no cover


class CsvSource:
    source_name = "csv"

    def __init__(self, file: str | Path | BinaryIO, label: str | None = None):
        if isinstance(file, (str, Path)):
            data = Path(file).read_bytes()
            default_label = Path(file).name
        else:
            data = file.read()
            if isinstance(data, str):
                data = data.encode("utf-8")
            default_label = Path(getattr(file, "name", "") or "upload.csv").name
        self.label = label or default_label

        text = _decode(data)
        if not text.strip():
            raise ImportFileError("The file is empty.")
        try:
            table = list(csv.reader(io.StringIO(text, newline="")))
        except csv.Error as exc:
            raise ImportFileError(f"Could not read the file as CSV: {exc}") from None

        self._header = [cell.strip() for cell in table[0]]
        self._body = table[1:]

    def columns(self) -> list[str]:
        return list(self._header)

    def rows(self) -> Iterator[RawRow]:
        for offset, cells in enumerate(self._body):
            if not any(cell.strip() for cell in cells):
                continue  # blank lines are common at the end of exports
            values = {
                column: cells[i] if i < len(cells) else ""
                for i, column in enumerate(self._header)
                if column
            }
            yield RawRow(number=offset + 2, values=values)


def import_csv(
    engine: Engine,
    file: str | Path | BinaryIO,
    kind: ImportKind | str,
    label: str | None = None,
) -> ImportReport:
    return run_import(engine, CsvSource(file, label=label), kind)
