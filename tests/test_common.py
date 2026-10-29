from pathlib import Path

import duckdb
import pytest

from fdp.ingest.common import load_raw_json, raw_dir, save_raw


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
