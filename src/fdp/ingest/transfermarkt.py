"""Ingestion of the Transfermarkt dataset (dcaribou/transfermarkt-datasets) into the raw layer.

One-off full load: each table is a single file covering every season, so files are
not split by season. Games and appearances are filtered to the project scope when
loaded and get a `_season` column ("2425") to join with the other sources.
"""

import logging
from pathlib import Path
from typing import Any

import duckdb
import httpx

from fdp.config import load_config
from fdp.ingest.common import (
    DATA_DIR,
    check_csv,
    connect,
    save_raw,
    season_start_year,
    setup_logging,
)

SOURCE = "transfermarkt"
# One logger per module: messages show where they come from (fdp.ingest.transfermarkt).
logger = logging.getLogger(__name__)

# All parameters come from config/pipeline.yaml (read once, when the module is imported).
_CONFIG = load_config()
_SOURCE_CONFIG = _CONFIG["sources"][SOURCE]

# Transfermarkt codes of the competitions in scope, e.g. ("IT1", "GB1").
COMPETITIONS = [competition[SOURCE] for competition in _CONFIG["competitions"]]
# Project season codes and the start years Transfermarkt uses in games.season,
# e.g. ["2425", ...] and ["2024", ...], kept in the same order.
SEASONS: list[str] = _CONFIG["seasons"]
START_YEARS = [str(season_start_year(season)) for season in SEASONS]

BASE_URL: str = _SOURCE_CONFIG["base_url"]
TIMEOUT_SECONDS: float = _SOURCE_CONFIG["timeout_seconds"]
MIN_ROWS: int = _SOURCE_CONFIG["min_rows"]
# Table name -> {"required_columns": [...], "scope": "games" | "appearances" (optional)}.
TABLES: dict[str, dict[str, Any]] = _SOURCE_CONFIG["tables"]


def table_url(table: str) -> str:
    """Return the download URL of one table, e.g. .../data/games.csv.gz."""
    return f"{BASE_URL}/{table}.csv.gz"


def make_client() -> httpx.Client:
    """Create an HTTP client with a timeout long enough for the largest file."""
    return httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS)


def download_table(client: httpx.Client, table: str) -> bytes:
    """Download one compressed table and return its bytes exactly as received."""
    response = client.get(table_url(table))
    response.raise_for_status()
    return response.content


def load_table(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    """Rebuild raw.transfermarkt_<table> from its file, applying the scope filter if any.

    - games: only competitions and seasons in scope; `_season` is the project code
      matching games.season (2024 -> "2425").
    - appearances: only appearances in the loaded games, with the game's `_season`.
    - every other table: all rows, `_season` empty (players, clubs... have no season).
    The file on disk stays complete: the filter only applies to what is loaded.
    One CREATE OR REPLACE statement: if it fails, the previous table stays.
    """
    # Same protection as the other loaders: the table name goes into the SQL text.
    if not table.isidentifier():
        raise ValueError(f"Invalid table name: {table!r}")

    target = f"raw.{SOURCE}_{table}"
    # Named parameters ($path, ...) are filled from the dict passed to execute.
    # All values as text, like the other raw tables: typing happens in staging.
    source = "read_csv($path, all_varchar = true)"
    technical = "$path AS _source_file, current_timestamp AS _loaded_at"
    scope = TABLES[table].get("scope")

    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    if scope == "games":
        # unnest(list_a), unnest(list_b) in one SELECT pairs the lists row by row:
        # ("2024", "2425"), ("2025", "2526"), ...
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {target} AS
            SELECT g.*, s.code AS _season, {technical}
            FROM {source} AS g
            JOIN (SELECT unnest($start_years) AS start_year, unnest($seasons) AS code) AS s
                ON g.season = s.start_year
            WHERE list_contains($competitions, g.competition_id)
            """,
            {
                "path": str(path),
                "start_years": START_YEARS,
                "seasons": SEASONS,
                "competitions": COMPETITIONS,
            },
        )
    elif scope == "appearances":
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {target} AS
            SELECT a.*, g._season, {technical}
            FROM {source} AS a
            JOIN raw.{SOURCE}_games AS g ON a.game_id = g.game_id
            """,
            {"path": str(path)},
        )
    else:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE {target} AS
            SELECT *, NULL::VARCHAR AS _season, {technical}
            FROM {source}
            """,
            {"path": str(path)},
        )


def ingest(
    client: httpx.Client, con: duckdb.DuckDBPyConnection, data_dir: Path = DATA_DIR
) -> list[Path]:
    """Download, check, save and load every table, in the order of the config.

    Files are saved to data/raw/transfermarkt/<table>.csv.gz (complete, untouched).
    A failed check stops before saving that table, so its previous file and rows stay.
    Returns the paths of the saved files.
    """
    folder = data_dir / "raw" / SOURCE
    saved = []
    for table, spec in TABLES.items():
        logger.info("downloading %s", table_url(table))
        content = download_table(client, table)
        check_csv(content, spec["required_columns"], MIN_ROWS, table)
        path = save_raw(content, folder / f"{table}.csv.gz")
        load_table(con, table, path)
        rows = con.execute(f"SELECT count(*) FROM raw.{SOURCE}_{table}").fetchone()[0]
        logger.info("loaded %s into raw.%s_%s (%d rows)", path, SOURCE, table, rows)
        saved.append(path)
    return saved


def main() -> None:
    """Command line entry point: `uv run python -m fdp.ingest.transfermarkt`."""
    setup_logging()
    # `with` closes the HTTP client and the database even if an error occurs.
    with make_client() as client, connect() as con:
        ingest(client, con)


# Runs only when the file is executed directly, not when it is imported (tests, Dagster).
if __name__ == "__main__":
    main()
