"""Central settings for Vendorus.

All settings come from environment variables (loaded from a local `.env`
file if present). Secrets never live in source code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "vendorus.db"


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    log_level: str

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def load_settings(env_file: Path | None = None) -> Settings:
    """Read settings from the environment, loading `.env` first if it exists.

    Real environment variables always win over values in `.env`.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    # Path.as_posix() keeps the SQLite URL valid on both macOS and Windows.
    default_url = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

    return Settings(
        environment=os.getenv("VENDORUS_ENV", "development"),
        database_url=os.getenv("DATABASE_URL") or default_url,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
