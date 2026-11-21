"""Database connection.

SQLAlchemy lets the same code run on SQLite now and PostgreSQL later:
switching is a change to DATABASE_URL, not a rewrite.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from vendorus.config import Settings, load_settings


def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
    # SQLite ignores foreign keys unless asked; PostgreSQL always enforces them.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(settings: Settings | None = None) -> Engine:
    settings = settings or load_settings()
    if settings.is_sqlite:
        # Make sure the folder for the SQLite file exists.
        db_path = settings.database_url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.database_url, future=True)
    if settings.is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_tables(engine: Engine) -> None:
    """Create any missing tables. Safe to call every time; existing data is kept."""
    from vendorus.models import Base

    Base.metadata.create_all(engine)


def check_connection(engine: Engine) -> bool:
    """Return True if the database answers a trivial query."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1")).scalar() == 1
