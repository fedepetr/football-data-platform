"""Helpers shared by all ingestion pipelines."""

from pathlib import Path

import duckdb

# Root of the local data folder, relative to the project root (where commands are run).
DATA_DIR = Path("data")
# Single DuckDB file for all layers (raw, staging, core, marts); git-ignored.
DB_PATH = DATA_DIR / "fdp.duckdb"


def raw_dir(source: str, season: str, data_dir: Path = DATA_DIR) -> Path:
    """Return the folder for raw files of one source and season.

    Example: raw_dir("fdorg", "2627") -> data/raw/fdorg/2627.
    The folder is not created here: the writer creates it when saving a file.
    `data_dir` can be overridden in tests to avoid touching the real data folder.
    """
    return data_dir / "raw" / source / season


def save_raw(content: bytes, path: Path) -> Path:
    """Write a raw file exactly as received from the source and return its path.

    Works with bytes, so the same function saves JSON (football-data.org) and CSV
    (football-data.co.uk). Creates missing folders and overwrites an existing file,
    so re-running a season replaces its raw files.
    """
    # Same as `mkdir -p`: create data/raw/<source>/<season>/ if needed.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def connect(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open the project DuckDB database, creating the file and its folder if missing."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def load_raw_json(con: duckdb.DuckDBPyConnection, path: Path, table: str, season: str) -> None:
    """Load one raw JSON file into raw.<table> as a single row, replacing its previous load.

    The whole file goes into a `payload` JSON column, untouched (schema-on-read):
    unnesting and typing happen later in dbt staging, so a change in the source
    structure cannot break the raw load. Technical columns record where the row
    comes from (_source_file), which season it belongs to (_season) and when it
    was loaded (_loaded_at).

    Idempotent per file: rows from the same file are deleted before inserting,
    so loading twice never duplicates data and other files are left untouched.
    """
    # Table names cannot be passed as SQL parameters: accept only plain identifiers
    # (letters, digits, underscore) so the f-strings below cannot inject SQL.
    if not table.isidentifier():
        raise ValueError(f"Invalid table name: {table!r}")

    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS raw.{table} (
            payload JSON,
            _source_file VARCHAR,
            _season VARCHAR,
            _loaded_at TIMESTAMPTZ
        )
        """
    )

    source_file = str(path)
    payload = path.read_text(encoding="utf-8")

    # Delete + insert in one transaction: either both succeed or the table is left
    # as it was (e.g. invalid JSON does not wipe the previous load).
    con.begin()
    try:
        con.execute(f"DELETE FROM raw.{table} WHERE _source_file = ?", [source_file])
        con.execute(
            f"INSERT INTO raw.{table} VALUES (?::JSON, ?, ?, current_timestamp)",
            [payload, source_file, season],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
