"""Helpers shared by all ingestion pipelines."""

import csv
import gzip
import io
import logging
from pathlib import Path

import duckdb

# Root of the local data folder, relative to the project root (where commands are run).
DATA_DIR = Path("data")
# Single DuckDB file for all layers (raw, staging, core, marts); git-ignored.
DB_PATH = DATA_DIR / "fdp.duckdb"


def setup_logging() -> None:
    """Show INFO messages with time and module, for command line runs.

    Called only by the `main()` entry points: when the code runs under pytest or
    Dagster, they configure logging themselves.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def season_start_year(season: str) -> int:
    """Convert a project season code into its start year: "2627" -> 2026.

    The project identifies seasons as "2627"; football-data.org and Transfermarkt
    use the start year (2026).
    Raises ValueError on malformed codes, so a typo fails before any API call.
    """
    # Exactly 4 digits: rejects "26-27", "262", " 2627".
    if len(season) != 4 or not season.isdigit():
        raise ValueError(f"Season must be 4 digits like '2627', got {season!r}")
    # The two halves must be consecutive years: rejects calendar years like "2026".
    start, end = int(season[:2]), int(season[2:])
    if end != start + 1:
        raise ValueError(f"Season years must be consecutive like '2627', got {season!r}")
    return 2000 + start


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def check_csv(content: bytes, required_columns: list[str], min_rows: int, label: str) -> None:
    """Arrival checks on a downloaded CSV (plain or gzip), before it is saved or loaded.

    Raises ValueError if a required column is missing or there are fewer than
    `min_rows` data rows. `label` (e.g. "I1 2627", "games") says which file failed.
    Rows are read as a stream and reading stops once `min_rows` is reached, so a
    large file (1.9M appearances) is checked without loading it all in memory.
    """
    # Gzip files start with the bytes 1f 8b: decompress them on the fly.
    binary = io.BytesIO(content)
    if content[:2] == b"\x1f\x8b":
        binary = gzip.GzipFile(fileobj=binary)
    # "utf-8-sig" drops a BOM, so the first column reads "Div" and not "\ufeffDiv".
    reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8-sig", newline=""))

    header = next(reader, None)
    if header is None:
        raise ValueError(f"{label}: empty file")

    # Required columns: without them the file cannot be used downstream.
    missing = [column for column in required_columns if column not in header]
    if missing:
        raise ValueError(f"{label}: missing columns {missing}")

    # Minimum rows: lines made only of commas are padding, not data.
    rows = 0
    for line in reader:
        if any(line):
            rows += 1
            if rows >= min_rows:
                return
    raise ValueError(f"{label}: expected at least {min_rows} rows, got {rows}")


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


def rebuild_raw_csv_table(con: duckdb.DuckDBPyConnection, table: str, pattern: Path) -> None:
    """Rebuild raw.<table> from every CSV file matching `pattern`, one row per CSV line.

    Files of different seasons have different columns: `union_by_name` aligns them
    by name, filling missing ones with NULL. All values are read as text
    (`all_varchar`): no type guessing in the raw layer, typing happens in staging.

    Idempotent by construction: the raw files on disk are the source of truth and
    the table is recreated from them in one statement, so it can never hold
    duplicated or stale rows. If the statement fails, the previous table stays.
    """
    # Same protection as load_raw_json: the table name goes into the SQL text.
    if not table.isidentifier():
        raise ValueError(f"Invalid table name: {table!r}")

    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE raw.{table} AS
        SELECT
            * EXCLUDE (filename),
            filename AS _source_file,
            -- data/raw/<source>/<season>/<file>.csv: the season is the parent folder.
            string_split(filename, '/')[-2] AS _season,
            current_timestamp AS _loaded_at
        FROM read_csv(?, union_by_name = true, all_varchar = true, filename = true)
        """,
        [str(pattern)],
    )
