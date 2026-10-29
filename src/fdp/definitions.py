"""Dagster definitions: wires the ingestion functions as assets, with no logic of its own.

Each raw table is an asset with key ["raw", "<table>"], the same name it has in
DuckDB: dagster-dbt links the dbt sources (source "raw", same table names) to these
assets automatically, so the graph goes raw -> staging -> ... without extra config.
Run the UI with `uv run dagster dev` (reads .env for the football-data.org token).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from fdp.config import load_config
from fdp.ingest import couk, fdorg, transfermarkt
from fdp.ingest.common import connect, setup_logging

# Season partitions from config/pipeline.yaml: "2425", "2526", "2627".
SEASONS = dg.StaticPartitionsDefinition(load_config()["seasons"])

# The dbt project lives in <repo>/dbt; this file is <repo>/src/fdp/definitions.py.
DBT_PROJECT = DbtProject(project_dir=Path(__file__).resolve().parents[2] / "dbt")
# Under `dagster dev`, run `dbt parse` at startup so the manifest (the list of dbt
# models, tests and sources Dagster reads) always matches the current SQL files.
DBT_PROJECT.prepare_if_dev()

# A backfill of several seasons runs as one run, looping over the seasons:
# separate runs in parallel would try to write the same DuckDB file at once.
SINGLE_RUN = dg.BackfillPolicy.single_run()


def raw_spec(table: str, group: str, description: str) -> dg.AssetSpec:
    """Describe one raw DuckDB table as a Dagster asset."""
    return dg.AssetSpec(
        key=["raw", table], group_name=group, description=description, kinds={"duckdb"}
    )


@dg.multi_asset(
    name="fdorg_raw",
    specs=[
        raw_spec(f"fdorg_{endpoint}", "fdorg", f"football-data.org /{endpoint}, one row per file")
        for endpoint in fdorg.ENDPOINTS
    ],
    partitions_def=SEASONS,
    backfill_policy=SINGLE_RUN,
)
def fdorg_raw(context: dg.AssetExecutionContext) -> Iterator[dg.MaterializeResult]:
    """Load football-data.org matches, teams and scorers for the selected seasons."""
    # Our modules log with the standard `logging`: show their INFO lines in the run logs.
    setup_logging()
    token = os.getenv("FOOTBALL_DATA_ORG_TOKEN")
    if not token:
        raise RuntimeError("FOOTBALL_DATA_ORG_TOKEN is missing: set it in .env")

    with fdorg.make_client(token) as client, connect() as con:
        for season in context.partition_keys:
            for competition in fdorg.COMPETITIONS:
                fdorg.ingest_season(client, con, competition, season)

        # One result per asset, with the number of items loaded (shown in the UI).
        for endpoint, spec in fdorg.ENDPOINTS.items():
            items = con.execute(
                f"SELECT sum(json_array_length(payload->'$.{spec['items_key']}')) "
                f"FROM raw.fdorg_{endpoint} WHERE list_contains(?, _season)",
                [context.partition_keys],
            ).fetchone()[0]
            yield dg.MaterializeResult(
                asset_key=["raw", f"fdorg_{endpoint}"], metadata={"items": int(items)}
            )


@dg.multi_asset(
    name="couk_raw",
    specs=[raw_spec(couk.TABLE, "couk", "football-data.co.uk match CSVs, one row per match")],
    partitions_def=SEASONS,
    backfill_policy=SINGLE_RUN,
)
def couk_raw(context: dg.AssetExecutionContext) -> Iterator[dg.MaterializeResult]:
    """Load football-data.co.uk Serie A and Premier League CSVs for the selected seasons."""
    setup_logging()
    with couk.make_client() as client, connect() as con:
        for season in context.partition_keys:
            for competition in couk.COMPETITIONS:
                couk.ingest_season(client, con, competition, season)

        matches = con.execute(
            f"SELECT count(*) FROM raw.{couk.TABLE} WHERE list_contains(?, _season)",
            [context.partition_keys],
        ).fetchone()[0]
        yield dg.MaterializeResult(asset_key=["raw", couk.TABLE], metadata={"matches": matches})


@dg.multi_asset(
    name="transfermarkt_raw",
    specs=[
        raw_spec(f"transfermarkt_{table}", "transfermarkt", f"Transfermarkt {table} (snapshot)")
        for table in transfermarkt.TABLES
    ],
)
def transfermarkt_raw() -> Iterator[dg.MaterializeResult]:
    """Load the Transfermarkt snapshot: one-off full load, not partitioned by season."""
    setup_logging()
    with transfermarkt.make_client() as client, connect() as con:
        transfermarkt.ingest(client, con)

        for table in transfermarkt.TABLES:
            rows = con.execute(f"SELECT count(*) FROM raw.transfermarkt_{table}").fetchone()[0]
            yield dg.MaterializeResult(
                asset_key=["raw", f"transfermarkt_{table}"], metadata={"rows": rows}
            )


@dbt_assets(manifest=DBT_PROJECT.manifest_path)
def dbt_models(context: dg.AssetExecutionContext, dbt: DbtCliResource) -> Iterator:
    """Every dbt model is an asset and every dbt test an asset check.

    `dbt build` runs models and their tests in dependency order; Dagster passes the
    selection, so materializing one model only builds that model.
    """
    yield from dbt.cli(["build"], context=context).stream()


defs = dg.Definitions(
    assets=[fdorg_raw, couk_raw, transfermarkt_raw, dbt_models],
    resources={"dbt": DbtCliResource(project_dir=DBT_PROJECT)},
    # Steps of a run execute one after the other: DuckDB allows a single writer.
    executor=dg.in_process_executor,
)
