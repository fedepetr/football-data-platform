"""Data access for the demo app: the only module that knows where the data lives.

The app reads the published tables of the platform (marts, a few core tables and
the player matches) and never imports the pipeline code: the tables are the
contract between the two. Two backends, same table names for the pages:

- local: the project database data/fdp.duckdb, attached read-only;
- online: Parquet files published by the weekly pipeline, read from the URL in
  the FDP_DEMO_DATA_URL environment variable (e.g. a GitHub release download URL).

Each table becomes a view in an in-memory DuckDB, so pages write plain SQL like
`select * from mart_player_profile` whatever the backend.
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

# Table name -> schema in data/fdp.duckdb. Online, each one is <name>.parquet.
TABLES = {
    "mart_player_profile": "marts",
    "mart_similar_players": "marts",
    "dim_player": "core",
    "dim_team": "core",
    "fct_match": "core",
    "map_player": "core",
    "fct_player_valuation": "core",
    "fct_player_appearance": "core",
    "fct_player_season_scoring": "core",
    "player_matches": "matching",
}

# app/data.py -> project root -> data/fdp.duckdb, wherever streamlit is started from.
LOCAL_DB = Path(__file__).resolve().parents[1] / "data" / "fdp.duckdb"


def data_source() -> str:
    """Where the data comes from: the Parquet base URL online, the local file otherwise."""
    return os.getenv("FDP_DEMO_DATA_URL") or str(LOCAL_DB)


@st.cache_resource
def connect() -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB with one view per table (cached: once per app process).

    Local: the project database is attached READ_ONLY, so the app never changes it.
    While the app runs, the pipeline cannot write to it (DuckDB allows one writer).
    """
    con = duckdb.connect()
    base_url = os.getenv("FDP_DEMO_DATA_URL")
    if base_url:
        for table in TABLES:
            con.execute(
                f"create view {table} as "
                f"select * from read_parquet('{base_url.rstrip('/')}/{table}.parquet')"
            )
    else:
        if not LOCAL_DB.exists():
            raise FileNotFoundError(
                f"{LOCAL_DB} not found: load the data first (see README, Quick start)"
            )
        con.execute(f"attach '{LOCAL_DB}' as fdp (read_only)")
        for table, schema in TABLES.items():
            con.execute(f"create view {table} as select * from fdp.{schema}.{table}")
    return con


@st.cache_data(ttl="1h")
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run a query and return a DataFrame; results are cached for an hour.

    A new cursor per query: Streamlit serves each user in its own thread, and
    a DuckDB connection should not be shared between threads at the same time.
    """
    return connect().cursor().execute(sql, list(params)).df()
