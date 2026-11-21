"""The dashboard pages load without errors."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture(autouse=True)
def temp_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")


@pytest.mark.parametrize("page", ["Home.py", "pages/1_Import.py", "pages/2_Profit.py"])
def test_page_loads(page):
    at = AppTest.from_file(str(APP / page), default_timeout=30).run()
    assert not at.exception


def test_import_page_switches_types():
    at = AppTest.from_file(str(APP / "pages/1_Import.py"), default_timeout=30).run()
    at.radio[0].set_value(at.radio[0].options[1]).run()  # keywords
    assert not at.exception
    assert "Nothing imported yet." in [m.value for m in at.markdown]


def test_profit_page_by_hand_shows_organic_warning_and_needs_inputs():
    at = AppTest.from_file(str(APP / "pages/2_Profit.py"), default_timeout=30).run()
    assert not at.exception
    assert "not guaranteed" in at.warning[0].value
    errors = " ".join(e.value for e in at.error)
    assert "Choose a referral fee category" in errors
    assert "dimensions and weight" in errors


def test_profit_page_calculates_an_imported_product(tmp_path):
    from vendorus.config import load_settings
    from vendorus.costs import save_cost
    from vendorus.db import get_engine
    from vendorus.importers import import_csv

    engine = get_engine(load_settings(env_file=tmp_path / "missing.env"))
    import_csv(engine, Path(__file__).parent / "fixtures" / "products_clean.csv", "products")
    save_cost(engine, "B0TEST0002", fee_category="Home and Kitchen", unit_cost=3,
              ads_enabled=True, ad_spend_pct=10)

    at = AppTest.from_file(str(APP / "pages/2_Profit.py"), default_timeout=30).run()
    at.selectbox[0].set_value(at.selectbox[0].options[2]).run()  # B0TEST0002
    assert not at.exception
    labels = [m.label for m in at.metric]
    # Organic and with-ads results side by side.
    assert labels[:2] == ["Organic (no ads): profit per unit", "With ads (10%): profit per unit"]
    assert not at.error
