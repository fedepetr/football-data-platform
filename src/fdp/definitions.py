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
from fdp.matching import player_map

_CONFIG = load_config()
# Season partitions from config/pipeline.yaml: "2425", "2526", "2627".
SEASONS = dg.StaticPartitionsDefinition(_CONFIG["seasons"])
CURRENT_SEASON: str = _CONFIG["current_season"]

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
        raise RuntimeError(
            "FOOTBALL_DATA_ORG_TOKEN is missing: set it in .env (locally) "
            "or as a repository secret (GitHub Actions)"
        )

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


PLAYER_MATCHES = dg.AssetKey(["matching", "player_matches"])
MIN_MATCH_RATE: float = _CONFIG["matching"]["players"]["min_match_rate"]


@dg.asset(
    key=PLAYER_MATCHES,
    group_name="matching",
    kinds={"python", "duckdb"},
    description="football-data.org players matched to Transfermarkt players, one row per player",
    # Inputs: the staging players of both sources, the team map (team and season
    # candidates) and the reviewed corrections. dbt builds them before this asset.
    deps=[
        dg.AssetKey(["staging", "stg_fdorg__squad_players"]),
        dg.AssetKey(["staging", "stg_fdorg__scorers"]),
        dg.AssetKey(["staging", "stg_transfermarkt__players"]),
        dg.AssetKey(["staging", "stg_transfermarkt__appearances"]),
        dg.AssetKey(["core", "map_team"]),
        dg.AssetKey(["seeds", "player_corrections"]),
    ],
)
def player_matches() -> dg.MaterializeResult:
    """Python step between staging and core: run the player reconciliation."""
    setup_logging()
    with connect() as con:
        rows = player_map.run(con)
    bands = player_map.band_counts(rows)
    return dg.MaterializeResult(
        metadata={
            "players": len(rows),
            "match_rate": round(player_map.match_rate(rows), 4),
            **{f"band_{band}": count for band, count in sorted(bands.items())},
        }
    )


@dg.asset_check(asset=PLAYER_MATCHES, blocking=True)
def player_match_rate() -> dg.AssetCheckResult:
    """At least 90% of football-data.org players matched (auto or manual).

    Blocking: if it fails, the models that use the matches (map_player and the
    player tables built on it) are not materialized with bad data.
    """
    with connect() as con:
        players, matched = con.execute(
            "select count(*), count(*) filter (where band in ('auto', 'manual')) "
            "from matching.player_matches"
        ).fetchone()
    rate = matched / players if players else 0.0
    return dg.AssetCheckResult(
        passed=rate >= MIN_MATCH_RATE,
        metadata={"match_rate": round(rate, 4), "threshold": MIN_MATCH_RATE, "matched": matched},
    )


@dbt_assets(manifest=DBT_PROJECT.manifest_path)
def dbt_models(context: dg.AssetExecutionContext, dbt: DbtCliResource) -> Iterator:
    """Every dbt model is an asset and every dbt test an asset check.

    `dbt build` runs models and their tests in dependency order; Dagster passes the
    selection, so materializing one model only builds that model.
    """
    yield from dbt.cli(["build"], context=context).stream()


# Weekly refresh: the seasonal sources (football-data.org, football-data.co.uk) for
# one season, and everything downstream (player matching, dbt staging, core,
# marts and their tests). Transfermarkt is a one-off snapshot: not refreshed.
weekly_refresh = dg.define_asset_job(
    name="weekly_refresh",
    # Partitioned by season, inferred from the fdorg and couk assets.
    selection=dg.AssetSelection.groups("fdorg", "couk").downstream(),
)


@dg.schedule(
    job=weekly_refresh,
    cron_schedule=_CONFIG["schedule"]["cron"],
    execution_timezone=_CONFIG["schedule"]["timezone"],
)
def weekly_current_season() -> dg.RunRequest:
    """Every week, refresh the partition of the season in progress only."""
    return dg.RunRequest(partition_key=CURRENT_SEASON)


defs = dg.Definitions(
    assets=[fdorg_raw, couk_raw, transfermarkt_raw, player_matches, dbt_models],
    asset_checks=[player_match_rate],
    jobs=[weekly_refresh],
    schedules=[weekly_current_season],
    resources={"dbt": DbtCliResource(project_dir=DBT_PROJECT)},
    # Steps of a run execute one after the other: DuckDB allows a single writer.
    executor=dg.in_process_executor,
)
