"""Demo app tests: the data contract and every page, against the real database.

They need data/fdp.duckdb (built by the pipeline) and the demo dependencies
(`uv sync --group demo`): skipped otherwise, e.g. in the pull request CI, which has
no data. The weekly pipeline runs them right after rebuilding the database.
"""

from pathlib import Path

import duckdb
import pytest

from demo_contract import CONTRACT, missing_columns

DB_PATH = Path("data/fdp.duckdb")
# AppTest resolves relative paths from this file: give it the absolute path.
HOME_PAGE = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
pytestmark = pytest.mark.skipif(
    not DB_PATH.exists(), reason="needs data/fdp.duckdb: run the pipeline first"
)


def test_missing_columns_reports_what_breaks_the_contract() -> None:
    """A renamed column or a missing table is reported by name."""
    available = {table: set(columns) for table, (_, columns) in CONTRACT.items()}
    available["mart_player_profile"].discard("goals")
    del available["player_matches"]

    missing = missing_columns(available)

    assert missing["mart_player_profile"] == ["goals"]
    assert missing["player_matches"] == CONTRACT["player_matches"][1]


def test_database_honours_the_contract() -> None:
    """Every table the app reads exists with every column the pages use."""
    con = duckdb.connect(str(DB_PATH), read_only=True)
    rows = con.execute(
        "select table_schema, table_name, column_name from information_schema.columns"
    ).fetchall()
    available: dict[str, set[str]] = {}
    for schema, table, column in rows:
        if CONTRACT.get(table, ("",))[0] == schema:
            available.setdefault(table, set()).add(column)

    assert missing_columns(available) == {}


@pytest.mark.parametrize(
    "page", [None, "pages/1_Player.py", "pages/2_Similar_players.py", "pages/3_Data_quality.py"]
)
def test_page_renders_without_errors(page: str | None) -> None:
    """Each page runs from the home, like in the app, and raises no exception."""
    testing = pytest.importorskip("streamlit.testing.v1", reason="needs the demo dependencies")
    app = testing.AppTest.from_file(str(HOME_PAGE), default_timeout=60).run()
    if page:
        app.switch_page(page).run()

    assert not app.exception, [error.value for error in app.exception]
    assert app.title  # the page drew its title


def test_export_writes_contract_columns_only(tmp_path: Path) -> None:
    """Each contract table becomes a Parquet file with exactly the contract columns."""
    from export_parquet import export_tables

    con = duckdb.connect()
    con.execute(f"attach '{DB_PATH}' as fdp (read_only)")

    written = export_tables(con, "fdp", tmp_path)

    assert len(written) == len(CONTRACT)
    for path in written:
        table = path.stem
        exported = con.execute(f"select * from read_parquet('{path}') limit 0").description
        assert [column[0] for column in exported] == CONTRACT[table][1]
