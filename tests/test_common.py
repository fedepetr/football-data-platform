import gzip
from pathlib import Path

import duckdb
import pytest

from fdp.ingest.common import (
    check_csv,
    load_raw_json,
    raw_dir,
    rebuild_raw_csv_table,
    save_raw,
    season_start_year,
)


def test_raw_dir_default_data_folder() -> None:
    """By default raw files live under data/raw/<source>/<season>."""
    assert raw_dir("fdorg", "2627") == Path("data/raw/fdorg/2627")


def test_raw_dir_custom_data_folder() -> None:
    """A custom data folder is used as the root of the path."""
    assert raw_dir("couk", "2425", data_dir=Path("/tmp/x")) == Path("/tmp/x/raw/couk/2425")


def test_save_raw_creates_missing_folders(tmp_path: Path) -> None:
    """Saving into a folder that does not exist creates it and writes the content."""
    path = raw_dir("fdorg", "2627", data_dir=tmp_path) / "SA_matches.json"

    written = save_raw(b'{"matches": []}', path)

    assert written == path
    assert path.read_bytes() == b'{"matches": []}'


def test_save_raw_overwrites_existing_file(tmp_path: Path) -> None:
    """Saving twice to the same path keeps only the latest content."""
    path = tmp_path / "SA_matches.json"

    save_raw(b"old", path)
    save_raw(b"new", path)

    assert path.read_bytes() == b"new"


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB: each test gets an empty database, nothing written to disk."""
    return duckdb.connect()


def rows(con: duckdb.DuckDBPyConnection) -> list[tuple]:
    """Return (count from payload, source file, season) for each row of raw.fdorg_matches."""
    return con.execute(
        "SELECT payload->>'$.count', _source_file, _season FROM raw.fdorg_matches ORDER BY 2"
    ).fetchall()


def test_load_raw_json_inserts_one_row_with_technical_columns(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """A file becomes one row: untouched payload plus source file, season and load time."""
    path = save_raw(b'{"count": 380}', tmp_path / "SA_matches.json")

    load_raw_json(con, path, "fdorg_matches", "2627")

    assert rows(con) == [("380", str(path), "2627")]
    # Checked in SQL: fetching a TIMESTAMPTZ into Python would require pytz.
    loaded = con.execute("SELECT _loaded_at IS NOT NULL FROM raw.fdorg_matches").fetchone()
    assert loaded == (True,)


def test_load_raw_json_reload_replaces_previous_row(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Loading the same file again replaces its row instead of duplicating it."""
    path = save_raw(b'{"count": 88}', tmp_path / "SA_matches.json")
    load_raw_json(con, path, "fdorg_matches", "2627")

    save_raw(b'{"count": 98}', path)
    load_raw_json(con, path, "fdorg_matches", "2627")

    assert rows(con) == [("98", str(path), "2627")]


def test_load_raw_json_reload_leaves_other_files_untouched(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Reloading Serie A does not delete Premier League rows of the same season."""
    pl = save_raw(b'{"count": 1}', tmp_path / "PL_matches.json")
    sa = save_raw(b'{"count": 2}', tmp_path / "SA_matches.json")
    load_raw_json(con, pl, "fdorg_matches", "2627")
    load_raw_json(con, sa, "fdorg_matches", "2627")

    load_raw_json(con, sa, "fdorg_matches", "2627")

    assert rows(con) == [("1", str(pl), "2627"), ("2", str(sa), "2627")]


def test_load_raw_json_invalid_json_keeps_previous_load(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """A broken file raises and rolls back: the previous good load is not lost."""
    path = save_raw(b'{"count": 88}', tmp_path / "SA_matches.json")
    load_raw_json(con, path, "fdorg_matches", "2627")

    save_raw(b"not json", path)
    with pytest.raises(duckdb.Error):
        load_raw_json(con, path, "fdorg_matches", "2627")

    assert rows(con) == [("88", str(path), "2627")]


def test_load_raw_json_rejects_unsafe_table_name(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Table names that are not plain identifiers are refused before running any SQL."""
    path = save_raw(b"{}", tmp_path / "x.json")

    with pytest.raises(ValueError):
        load_raw_json(con, path, "matches; DROP TABLE x", "2627")


def test_rebuild_raw_csv_table_unions_columns_by_name(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Files with different columns are aligned by name; missing values are NULL, all text."""
    save_raw(b"Div,FTHG\nI1,2\n", raw_dir("couk", "2425", tmp_path) / "I1.csv")
    save_raw(b"Div,FTHG,HxG\nI1,1,1.05\n", raw_dir("couk", "2627", tmp_path) / "I1.csv")

    rebuild_raw_csv_table(con, "couk_matches", tmp_path / "raw" / "couk" / "*" / "*.csv")

    rows = con.execute(
        "SELECT _season, Div, FTHG, HxG FROM raw.couk_matches ORDER BY _season"
    ).fetchall()
    assert rows == [("2425", "I1", "2", None), ("2627", "I1", "1", "1.05")]


def test_rebuild_raw_csv_table_reflects_files_on_disk(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Rebuilding after a file is replaced gives its new rows only: no duplicates."""
    path = raw_dir("couk", "2627", tmp_path) / "I1.csv"
    pattern = tmp_path / "raw" / "couk" / "*" / "*.csv"
    save_raw(b"Div,FTHG\nI1,1\n", path)
    rebuild_raw_csv_table(con, "couk_matches", pattern)

    save_raw(b"Div,FTHG\nI1,1\nI1,3\n", path)  # weekly refresh: one more match played
    rebuild_raw_csv_table(con, "couk_matches", pattern)

    rows = con.execute("SELECT FTHG, _source_file FROM raw.couk_matches ORDER BY 1").fetchall()
    assert rows == [("1", str(path)), ("3", str(path))]


def test_season_start_year() -> None:
    """A season code is converted to its start year (full cases in test_fdorg.py)."""
    assert season_start_year("2627") == 2026


# Arrival check settings used below: like football-data.co.uk.
COLUMNS = ["Div", "HomeTeam"]
CSV_WITH_BOM = "\ufeffDiv,HomeTeam\nI1,Udinese\n"


def test_check_csv_accepts_valid_file() -> None:
    """A file with the required columns and enough rows passes, BOM included."""
    check_csv(CSV_WITH_BOM.encode("utf-8"), COLUMNS, 1, "I1 2627")


def test_check_csv_reads_gzip_files() -> None:
    """Compressed files (Transfermarkt .csv.gz) are checked like plain ones."""
    check_csv(gzip.compress(b"Div,HomeTeam\nI1,Udinese\n"), COLUMNS, 1, "games")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "empty"),  # nothing downloaded
        ("Div\nI1\n", "missing columns"),  # a required column is gone
        ("Div,HomeTeam\n", "at least 1"),  # header only
        ("Div,HomeTeam\n,\n", "at least 1"),  # padding line, not data
    ],
)
def test_check_csv_rejects_bad_files(content: str, message: str) -> None:
    """Each rule raises ValueError naming the failed file and the reason."""
    with pytest.raises(ValueError, match=message) as error:
        check_csv(content.encode("utf-8"), COLUMNS, 1, "I1 2627")
    assert "I1 2627" in str(error.value)
