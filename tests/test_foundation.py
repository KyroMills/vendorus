from pathlib import Path

import pytest

from vendorus.config import load_settings


def test_defaults_to_local_sqlite(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = load_settings(env_file=tmp_path / "missing.env")
    assert settings.is_sqlite
    assert settings.database_url.endswith("data/vendorus.db")


def test_env_variable_overrides_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost/vendorus")
    settings = load_settings(env_file=tmp_path / "missing.env")
    assert not settings.is_sqlite


def test_no_secrets_committed():
    root = Path(__file__).resolve().parent.parent
    ignored = (root / ".gitignore").read_text()
    assert ".env" in ignored.splitlines()


def test_database_connects(monkeypatch, tmp_path):
    pytest.importorskip("sqlalchemy")
    from vendorus.db import check_connection, get_engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    assert check_connection(engine)
