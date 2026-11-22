"""The dashboard pages load and behave, run through the app's navigation."""
import io
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def temp_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")


@pytest.fixture
def engine(tmp_path):
    from vendorus.config import load_settings
    from vendorus.db import get_engine

    return get_engine(load_settings(env_file=tmp_path / "missing.env"))


def open_page(page: str | None = None, **query) -> AppTest:
    """Start the app (Home.py) and go to `page`, like a user would."""
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=30)
    at.run()
    if page:
        at.switch_page(page)
        for key, value in query.items():
            at.query_params[key] = value
        at.run()
    assert not at.exception, at.exception
    return at


def import_fixtures(engine, *names):
    from vendorus.importers import import_csv

    kinds = {"products_clean": "products", "keywords": "keywords", "competitors": "competitors"}
    for name in names:
        import_csv(engine, FIXTURES / f"{name}.csv", kinds[name])


def import_garlic_market(engine):
    """The hand-worked market from test_scoring (scores 81 with costs)."""
    from test_scoring import COMPETITORS, KEYWORDS, PRODUCTS, with_costs

    from vendorus.importers import import_csv

    import_csv(engine, io.BytesIO(PRODUCTS), "products")
    import_csv(engine, io.BytesIO(COMPETITORS), "competitors")
    import_csv(engine, io.BytesIO(KEYWORDS), "keywords")
    with_costs(engine)


@pytest.mark.parametrize(
    "page", ["views/opportunities.py", "views/import_data.py", "views/profit.py", "views/score.py",
             "views/validate.py"]
)
def test_every_page_loads_when_empty(page):
    open_page(page)


def test_app_opens_on_overview_with_navigation():
    at = open_page()
    assert at.title[0].value == "Product research"
    assert [m.label for m in at.metric] == ["Products", "Keywords", "With costs", "Imports"]


def test_import_page_switches_types():
    at = open_page("views/import_data.py")
    control = at.button_group[0]
    control.set_value(control.options[1].content if hasattr(control.options[1], "content") else "keywords").run()
    assert not at.exception
    assert any("Nothing imported yet." in m.value for m in at.markdown)


def test_profit_page_by_hand_shows_organic_warning_and_needs_inputs():
    at = open_page("views/profit.py")
    assert "not guaranteed" in at.warning[0].value
    errors = " ".join(e.value for e in at.error)
    assert "Choose a referral fee category" in errors
    assert "dimensions and weight" in errors


def test_profit_page_calculates_an_imported_product(engine):
    from vendorus.costs import save_cost

    import_fixtures(engine, "products_clean")
    save_cost(engine, "B0TEST0002", fee_category="Home and Kitchen", unit_cost=3,
              ads_enabled=True, ad_spend_pct=10)
    at = open_page("views/profit.py", asin="B0TEST0002")
    labels = [m.label for m in at.metric]
    # Organic and with-ads results side by side.
    assert labels[:2] == ["Organic (no ads): profit per unit", "With ads (10%): profit per unit"]
    assert not at.error


def test_score_page_shows_breakdown_for_an_imported_product(engine):
    import_fixtures(engine, "products_clean", "keywords", "competitors")
    at = open_page("views/score.py")
    assert at.metric[0].label == "Score"
    # B0TEST0001 has 6 of 10 factors: enough for a score.
    assert at.metric[0].value == "68 / 100"


def test_score_page_hides_a_partial_score(engine):
    import_fixtures(engine, "products_clean")
    at = open_page("views/score.py")
    assert at.metric[0].value == "—"  # 1 of 10 factors: no misleading headline number


def test_score_page_without_products_points_to_import():
    at = open_page("views/score.py")
    assert any("No products yet" in m.value for m in at.markdown)


def test_score_page_opens_the_product_in_the_address(engine):
    import_garlic_market(engine)
    import_fixtures(engine, "products_clean")  # newer import, listed first
    at = open_page("views/score.py", asin="B0MAIN0001")
    assert at.selectbox[0].value.startswith("B0MAIN0001")
    assert at.metric[0].value == "81 / 100"


def test_opportunities_page_ranks_into_tabs(engine):
    from vendorus.costs import save_cost

    import_garlic_market(engine)  # B0MAIN0001: 81, Strong
    import_fixtures(engine, "products_clean", "keywords", "competitors")  # B0TEST0001: 68
    save_cost(engine, "0306406152", fee_category="Media - Books, DVD, Music, Software, Video",
              unit_cost=40)  # costs more than its $24 price
    at = open_page("views/opportunities.py")
    labels = [t.label for t in at.tabs]
    assert labels == ["Ranked · 2", "Needs data · 1", "Not viable · 1"]
    assert any("Showing 4 of 4 products" in c.value for c in at.caption)


def test_opportunities_filters_are_applied(engine):
    import_garlic_market(engine)
    import_fixtures(engine, "products_clean", "keywords", "competitors")
    at = open_page("views/opportunities.py")
    at.text_input(key="opp_search").set_value("garlic").run()
    assert not at.exception
    # Two garlic presses: the researched one ranks, the other needs data.
    assert [t.label for t in at.tabs] == ["Ranked · 1", "Needs data · 1", "Not viable · 0"]
    at.slider(key="opp_min").set_value(90).run()
    assert [t.label for t in at.tabs][0] == "Ranked · 0"  # 81 is below 90


def test_filters_are_remembered_across_pages(engine):
    import_garlic_market(engine)
    at = open_page("views/opportunities.py")
    at.text_input(key="opp_search").set_value("B0MAIN").run()
    at.switch_page("views/score.py").run()
    at.switch_page("views/opportunities.py").run()
    assert not at.exception
    assert at.text_input(key="opp_search").value == "B0MAIN"


def test_overview_lists_top_opportunities(engine):
    import_garlic_market(engine)
    at = open_page()
    assert any("Top opportunities" in h.value for h in at.subheader)
    assert any("81" in m.value and "Strong" in m.value for m in at.markdown)


def test_validate_page_shows_flags_season_reviews_and_saves_a_decision(engine):
    from test_validation import REVIEWS, trend_csv, two_years

    from vendorus.importers import import_csv
    from vendorus.validation import get_decision

    import_garlic_market(engine)
    import_csv(engine, io.BytesIO(trend_csv(two_years(second_year_factor=0.75))), "trends")
    import_csv(engine, io.BytesIO(REVIEWS.encode()), "reviews")
    at = open_page("views/validate.py", asin="B0MAIN0001")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Score"] == "81 / 100"
    assert metrics["High risks"] == "1"  # demand is declining
    assert any("Highly seasonal" in m.value for m in at.markdown)  # badges render as markdown
    assert metrics["Peak months"] == "Nov, Dec"
    assert metrics["Reviews"] == "8"

    at.checkbox(key="chk-B0MAIN0001-0").check()
    at.button_group(key="dec-B0MAIN0001").set_value("Watch")
    at.text_area(key="notes-B0MAIN0001").input("Waiting for samples")
    at.button[0].click().run()
    assert not at.exception
    saved = get_decision(engine, "B0MAIN0001")
    assert saved.decision == "Watch" and saved.notes == "Waiting for samples"
    assert any(saved.checklist.values())


def test_opportunities_show_risks_season_and_decision(engine):
    from test_validation import trend_csv, two_years

    from vendorus.importers import import_csv
    from vendorus.validation import save_decision

    import_garlic_market(engine)
    import_csv(engine, io.BytesIO(trend_csv(two_years(second_year_factor=0.75))), "trends")
    save_decision(engine, "B0MAIN0001", "Go", {})
    at = open_page("views/opportunities.py")
    frame = at.dataframe[0].value
    row = frame[frame["ASIN"] == "B0MAIN0001"].iloc[0]
    assert row["Decision"] == "Go"
    assert row["Season"] == "Highly seasonal, declining"
    assert row["Risks"].startswith("1 high")
    at.multiselect(key="opp_decisions").set_value(["No-go"]).run()
    assert [t.label for t in at.tabs][0] == "Ranked · 0"
