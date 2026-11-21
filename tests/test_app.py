"""The dashboard pages load without errors."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(autouse=True)
def temp_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")


@pytest.mark.parametrize("page", ["Home.py", "pages/1_Import.py"])
def test_page_loads(page):
    at = AppTest.from_file(str(APP / page), default_timeout=30).run()
    assert not at.exception


def test_import_page_switches_types():
    at = AppTest.from_file(str(APP / "pages/1_Import.py"), default_timeout=30).run()
    at.radio[0].set_value(at.radio[0].options[1]).run()  # keywords
    assert not at.exception
    assert "Nothing imported yet." in [m.value for m in at.markdown]
