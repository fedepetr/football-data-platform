"""Ingestion of football-data.co.uk match CSV files into the raw layer."""

import csv
import io
import sys
from pathlib import Path

import duckdb
import httpx

from fdp.config import load_config
from fdp.ingest.common import DATA_DIR, connect, raw_dir, rebuild_raw_csv_table, save_raw

SOURCE = "couk"
# All CSV files of every season and competition end up in this table.
TABLE = f"{SOURCE}_matches"

# All parameters come from config/pipeline.yaml (read once, when the module is imported).
_CONFIG = load_config()
_SOURCE_CONFIG = _CONFIG["sources"][SOURCE]

# football-data.co.uk codes of the competitions in scope, e.g. ("I1", "E0").
COMPETITIONS = tuple(competition[SOURCE] for competition in _CONFIG["competitions"])
BASE_URL: str = _SOURCE_CONFIG["base_url"]
TIMEOUT_SECONDS: float = _SOURCE_CONFIG["timeout_seconds"]
MIN_ROWS: int = _SOURCE_CONFIG["min_rows"]
REQUIRED_COLUMNS: list[str] = _SOURCE_CONFIG["required_columns"]


def csv_url(competition: str, season: str) -> str:
    """Return the download URL of one season file, e.g. .../mmz4281/2627/I1.csv."""
    return f"{BASE_URL}/{season}/{competition}.csv"


def make_client() -> httpx.Client:
    """Create an HTTP client that follows the site's redirect, with a timeout."""
    return httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS)


def download_csv(client: httpx.Client, competition: str, season: str) -> bytes:
    """Download one season file and return its bytes exactly as received.

    No retry: the site has no documented rate limit and a failed run is simply re-run.
    Raises httpx.HTTPStatusError on errors (e.g. 404 for a wrong season or code).
    """
    response = client.get(csv_url(competition, season))
    response.raise_for_status()
    return response.content


def check_csv(content: bytes, label: str) -> None:
    """Arrival checks on a downloaded CSV, before it is saved or loaded.

    Raises ValueError if a required column is missing or there are fewer than
    MIN_ROWS matches. `label` (e.g. "I1 2627") says which file failed.
    """
    # "utf-8-sig" drops the BOM, so the first column reads "Div" and not "﻿Div".
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    if not rows:
        raise ValueError(f"{label}: empty file")
    header, lines = rows[0], rows[1:]

    # Required columns: without them the file cannot describe a match.
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"{label}: missing columns {missing}")

    # Minimum rows: lines made only of commas are padding, not matches.
    matches = sum(1 for line in lines if any(line))
    if matches < MIN_ROWS:
        raise ValueError(f"{label}: expected at least {MIN_ROWS} matches, got {matches}")


def ingest_season(
    client: httpx.Client,
    con: duckdb.DuckDBPyConnection,
    competition: str,
    season: str,
    data_dir: Path = DATA_DIR,
) -> Path:
    """Download, check, save and load one competition and season.

    The file is saved to data/raw/couk/<season>/<competition>.csv (replacing the
    previous one), then raw.couk_matches is rebuilt from all couk files on disk.
    A failed check stops before saving, so the previous good file and rows stay.
    Returns the path of the saved file.
    """
    content = download_csv(client, competition, season)
    check_csv(content, f"{competition} {season}")
    path = save_raw(content, raw_dir(SOURCE, season, data_dir) / f"{competition}.csv")
    rebuild_raw_csv_table(con, TABLE, data_dir / "raw" / SOURCE / "*" / "*.csv")
    return path


def main() -> None:
    """Command line entry point: `uv run python -m fdp.ingest.couk 2627`."""
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m fdp.ingest.couk <season>, e.g. 2627")
    season = sys.argv[1]

    # `with` closes the HTTP client and the database even if an error occurs.
    with make_client() as client, connect() as con:
        for competition in COMPETITIONS:
            print(f"loaded {ingest_season(client, con, competition, season)}")


# Runs only when the file is executed directly, not when it is imported (tests, Dagster).
if __name__ == "__main__":
    main()
