"""Database connection.

SQLAlchemy lets the same code run on SQLite now and PostgreSQL later:
switching is a change to DATABASE_URL, not a rewrite.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from vendorus.config import Settings, load_settings


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or load_settings()
    if settings.is_sqlite:
        # Make sure the folder for the SQLite file exists.
        db_path = settings.database_url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(settings.database_url, future=True)


def check_connection(engine: Engine) -> bool:
    """Return True if the database answers a trivial query."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar() == 1
