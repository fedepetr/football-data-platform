"""Data access for the demo app: the only module that knows where the data lives.

The app reads the published tables of the platform (marts, a few core tables and
the player matches) and never imports the pipeline code: the tables are the
contract between the two. Where the data comes from, in this order:

1. FDP_DEMO_DATA_URL, if set: a folder or URL with one <table>.parquet per table;
2. the project database data/fdp.duckdb, if it exists (local runs), read-only;
3. otherwise the Parquet files of the public `demo-data` release, refreshed every
   week by the pipeline (Streamlit Community Cloud: no configuration needed).

Each table becomes a view in an in-memory DuckDB, so pages write plain SQL like
`select * from mart_player_profile` whatever the backend.
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from demo_contract import CONTRACT, missing_columns

# Table name -> schema in data/fdp.duckdb (from the contract). Online, <name>.parquet.
TABLES = {table: schema for table, (schema, _) in CONTRACT.items()}

# app/demo_data.py -> project root -> data/fdp.duckdb, wherever streamlit is started from.
LOCAL_DB = Path(__file__).resolve().parents[1] / "data" / "fdp.duckdb"
# Public download URL of the release assets published by the weekly pipeline.
RELEASE_URL = "https://github.com/fedepetr/football-data-platform/releases/download/demo-data"


def parquet_base() -> str | None:
    """Where to read Parquet files from, or None to read the local database."""
    if url := os.getenv("FDP_DEMO_DATA_URL"):
        return url.rstrip("/")
    return None if LOCAL_DB.exists() else RELEASE_URL


def data_source() -> str:
    """Human-readable data source, shown at the bottom of the home page."""
    return parquet_base() or str(LOCAL_DB)


@st.cache_resource
def connect() -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB with one view per table (cached: once per app process).

    Local: the project database is attached READ_ONLY, so the app never changes it.
    While the app runs, the pipeline cannot write to it (DuckDB allows one writer).
    """
    con = duckdb.connect()
    if base := parquet_base():
        for table in TABLES:
            con.execute(
                f"create view {table} as select * from read_parquet('{base}/{table}.parquet')"
            )
    else:
        con.execute(f"attach '{LOCAL_DB}' as fdp (read_only)")
        for table, schema in TABLES.items():
            con.execute(f"create view {table} as select * from fdp.{schema}.{table}")

    # Fail at start-up with a clear message if the data breaks the contract.
    available: dict[str, set[str]] = {}
    for table in TABLES:
        columns = con.execute(f"select * from {table} limit 0").description
        available[table] = {column[0] for column in columns}
    if missing := missing_columns(available):
        raise RuntimeError(f"The data does not match the app's contract: {missing}")
    return con


@st.cache_data(ttl="1h")
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a query and return a DataFrame; results are cached for an hour.

    A new cursor per query: Streamlit serves each user in its own thread, and
    a DuckDB connection should not be shared between threads at the same time.
    """
    return connect().cursor().execute(sql, list(params)).df()
