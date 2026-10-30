"""Export the tables of the demo contract to Parquet, for the online app.

The online app cannot read data/fdp.duckdb (it is not in the repository), so the
weekly pipeline publishes one Parquet file per contract table to a GitHub release
(`demo-data`), and the app reads them from there (FDP_DEMO_DATA_URL).
Only the contract columns are exported: the published data is exactly what the
app needs, nothing more.

Run from the project root: `uv run python app/export_parquet.py`.
"""

import sys
from pathlib import Path

import duckdb

from demo_contract import CONTRACT

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fdp.duckdb"
# Under data/, so git ignores it.
OUTPUT_DIR = ROOT / "data" / "demo"


def export_tables(con: duckdb.DuckDBPyConnection, database: str, output_dir: Path) -> list[Path]:
    """Write <table>.parquet for every contract table, with the contract columns only.

    `database` is the name the project database is attached as. Zstandard
    compression keeps the files small for the download by the online app.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for table, (schema, columns) in CONTRACT.items():
        path = output_dir / f"{table}.parquet"
        con.execute(
            f"copy (select {', '.join(columns)} from {database}.{schema}.{table}) "
            f"to '{path}' (format parquet, compression zstd)"
        )
        written.append(path)
    return written


def main() -> None:
    """Export from data/fdp.duckdb (read-only) to data/demo/ and print the sizes."""
    if not DB_PATH.exists():
        sys.exit(f"{DB_PATH} not found: run the pipeline first")
    con = duckdb.connect()
    con.execute(f"attach '{DB_PATH}' as fdp (read_only)")
    for path in export_tables(con, "fdp", OUTPUT_DIR):
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
