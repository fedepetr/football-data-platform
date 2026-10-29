"""Player reconciliation run: football-data.org players -> Transfermarkt players.

For every football-data.org player (squads and scorers):
1. Candidates (brief rule 2): Transfermarkt players with the same birth date, or
   in the same team and season. Team and season come from Transfermarkt
   appearances (2024/25, 2025/26) and, for 2026/27, the current club in the
   Transfermarkt snapshot (July 2026); teams are linked through core.map_team.
2. Best candidate by the brief's score (players.py), banded auto/review/rejected.
3. Checks demote an "auto" match to "review": a tie, a collision (football-data.org
   players with different birth dates picked the same Transfermarkt player) or
   the LCS second opinion rating another candidate higher. football-data.org
   sometimes has two ids for one person (same name and birth date): both may
   point to the same Transfermarkt player, that is not a collision.
4. Reverse check on rejected matches: if the candidate was proposed by both rules
   (same birth date AND same team and season) the name is probably a variant the
   score cannot see (`Max` / `Maximilian Kilman`). The band stays "rejected", as
   the brief asks, but the flag same_birth_date_and_team lists it for review.
5. Manual corrections (dbt/seeds/player_corrections.csv), re-read at every run,
   override everything.
The result is written to matching.player_matches (one row per football-data.org
player); the dbt model map_player keeps the accepted matches.

Run: `uv run python -m fdp.matching.player_map`.
"""

import csv
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import duckdb

from fdp.config import load_config
from fdp.ingest.common import connect, setup_logging
from fdp.matching.players import PlayerMatch, best_candidate

logger = logging.getLogger(__name__)

CORRECTIONS_PATH = Path("dbt/seeds/player_corrections.csv")
TABLE = "matching.player_matches"
# Bands that count as matched: auto and manual corrections. "review" is not
# accepted until confirmed in the corrections file.
MATCHED_BANDS = ("auto", "manual")

# football-data.org players: squads and scorers (19 scorers are in no squad).
FDORG_PLAYERS_SQL = """
select fdorg_person_id, any_value(player_name), any_value(date_of_birth)
from (
    select fdorg_person_id, player_name, date_of_birth from staging.stg_fdorg__squad_players
    union all
    select fdorg_person_id, player_name, date_of_birth from staging.stg_fdorg__scorers
)
group by fdorg_person_id
"""

# Candidates with the same birth date.
BIRTH_DATE_CANDIDATES_SQL = """
select distinct fd.fdorg_person_id, tm.tm_player_id
from (
    select fdorg_person_id, date_of_birth from staging.stg_fdorg__squad_players
    union
    select fdorg_person_id, date_of_birth from staging.stg_fdorg__scorers
) as fd
join staging.stg_transfermarkt__players as tm using (date_of_birth)
"""

# Candidates in the same team and season (team linked through map_team).
TEAM_SEASON_CANDIDATES_SQL = """
with fdorg_memberships as (
    select fdorg_person_id, season, fdorg_team_id from staging.stg_fdorg__squad_players
    union
    select fdorg_person_id, season, fdorg_team_id from staging.stg_fdorg__scorers
),
tm_memberships as (
    -- Who played for which club in each season.
    select distinct season, tm_club_id, tm_player_id from staging.stg_transfermarkt__appearances
    union
    -- Transfermarkt has no current-season games: use the club in the snapshot.
    select $current_season, current_tm_club_id, tm_player_id
    from staging.stg_transfermarkt__players
    where current_tm_club_id is not null
)
select distinct fd.fdorg_person_id, tm.tm_player_id
from fdorg_memberships as fd
join core.map_team as team on team.fdorg_team_id = fd.fdorg_team_id
join tm_memberships as tm
    on tm.season = fd.season and tm.tm_club_id = team.tm_club_id
"""


@dataclass(frozen=True)
class Correction:
    """A manual decision: the right Transfermarkt player, or None for "no match"."""

    tm_player_id: int | None
    note: str


def read_corrections(path: Path = CORRECTIONS_PATH) -> dict[int, Correction]:
    """Read the manual corrections (re-read at every run, as the brief asks).

    Columns: fdorg_person_id, tm_player_id (empty = the player has no match), note.
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as file:
        return {
            int(row["fdorg_person_id"]): Correction(
                int(row["tm_player_id"]) if row["tm_player_id"] else None, row["note"]
            )
            for row in csv.DictReader(file)
        }


def pairs_by_player(rows: list[tuple[int, int]]) -> dict[int, set[int]]:
    """Group (fdorg_person_id, tm_player_id) pairs into fdorg id -> set of tm ids."""
    grouped: dict[int, set[int]] = {}
    for fdorg_id, tm_id in rows:
        grouped.setdefault(fdorg_id, set()).add(tm_id)
    return grouped


def candidate_source(tm_id: int, by_birth_date: set[int], by_team: set[int]) -> str:
    """Which rule proposed the candidate: birth_date, team_season or both."""
    if tm_id in by_birth_date and tm_id in by_team:
        return "both"
    return "birth_date" if tm_id in by_birth_date else "team_season"


def flags(match: PlayerMatch, collided: bool, source: str) -> list[str]:
    """Reasons to look at a match by hand.

    On an "auto" match, any flag turns it into "review". On a "rejected" match,
    same_birth_date_and_team marks strong evidence against the low name score.
    """
    reasons = []
    if match.band == "rejected":
        return ["same_birth_date_and_team"] if source == "both" else []
    if match.tie:
        reasons.append("tie")
    if collided:
        reasons.append("collision")
    if not match.lcs_agrees:
        reasons.append("lcs_disagrees")
    return reasons


def transfermarkt_names(con: duckdb.DuckDBPyConnection) -> dict[int, str]:
    """Transfermarkt player id -> name, for every player."""
    rows = con.execute(
        "select tm_player_id, player_name from staging.stg_transfermarkt__players"
    ).fetchall()
    return dict(rows)


def collisions(matches: dict[int, PlayerMatch | None], birth_dates: dict[int, object]) -> set[int]:
    """Transfermarkt ids picked by football-data.org players with different birth dates.

    Two football-data.org ids with the same birth date on one Transfermarkt player
    are duplicates of the same person in football-data.org (e.g. Nikola Štulić),
    so they do not count; different birth dates mean at least one pick is wrong.
    """
    dates_by_pick: dict[int, set[object]] = {}
    for pid, match in matches.items():
        if match and match.band != "rejected":
            dates_by_pick.setdefault(match.tm_player_id, set()).add(birth_dates[pid])
    return {tm_id for tm_id, dates in dates_by_pick.items() if len(dates) > 1}


def match_players(
    con: duckdb.DuckDBPyConnection, tm_names: dict[int, str]
) -> list[dict[str, object]]:
    """Match every football-data.org player; one result row per player."""
    current_season = load_config()["current_season"]
    players = {pid: (name, dob) for pid, name, dob in con.execute(FDORG_PLAYERS_SQL).fetchall()}
    by_birth_date = pairs_by_player(con.execute(BIRTH_DATE_CANDIDATES_SQL).fetchall())
    by_team = pairs_by_player(
        con.execute(TEAM_SEASON_CANDIDATES_SQL, {"current_season": current_season}).fetchall()
    )

    matches: dict[int, PlayerMatch | None] = {}
    for pid, (name, _) in players.items():
        candidate_ids = by_birth_date.get(pid, set()) | by_team.get(pid, set())
        matches[pid] = best_candidate(name, {tm_id: tm_names[tm_id] for tm_id in candidate_ids})

    collided = collisions(matches, {pid: dob for pid, (_, dob) in players.items()})

    rows = []
    for pid, match in matches.items():
        name, dob = players[pid]
        row: dict[str, object] = {
            "fdorg_person_id": pid,
            "fdorg_player_name": name,
            "date_of_birth": dob,
        }
        if match is None:
            row |= {"band": "no_candidates", "flags": ""}
        else:
            source = candidate_source(
                match.tm_player_id, by_birth_date.get(pid, set()), by_team.get(pid, set())
            )
            reasons = flags(match, collided=match.tm_player_id in collided, source=source)
            band = "review" if match.band == "auto" and reasons else match.band
            row |= {
                "tm_player_id": match.tm_player_id,
                "tm_player_name": match.tm_player_name,
                "match_score": match.score,
                "match_method": match.method,
                "candidate_source": source,
                "band": band,
                "flags": " ".join(reasons),
            }
        rows.append(row)
    return rows


def apply_corrections(
    rows: list[dict[str, object]], corrections: dict[int, Correction], tm_names: dict[int, str]
) -> list[dict[str, object]]:
    """Manual decisions override the algorithm (the algorithm's score is kept for reference)."""
    for row in rows:
        correction = corrections.get(row["fdorg_person_id"])
        if correction is None:
            continue
        row["tm_player_id"] = correction.tm_player_id
        row["tm_player_name"] = (
            tm_names.get(correction.tm_player_id) if correction.tm_player_id else None
        )
        row["match_method"] = "manual"
        # A correction without a Transfermarkt id means "this player has no match".
        row["band"] = "manual" if correction.tm_player_id else "manual_no_match"
        row["flags"] = correction.note
    return rows


COLUMNS = {
    "fdorg_person_id": "integer",
    "fdorg_player_name": "varchar",
    "date_of_birth": "date",
    "tm_player_id": "integer",
    "tm_player_name": "varchar",
    "match_score": "double",
    "match_method": "varchar",
    "candidate_source": "varchar",
    "band": "varchar",
    "flags": "varchar",
}


def write_matches(con: duckdb.DuckDBPyConnection, rows: list[dict[str, object]]) -> None:
    """Replace matching.player_matches with the new results, in one transaction."""
    con.execute("create schema if not exists matching")
    con.begin()
    try:
        columns = ", ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
        con.execute(f"create or replace table {TABLE} ({columns}, matched_at timestamptz)")
        placeholders = ", ".join("?" for _ in COLUMNS)
        con.executemany(
            f"insert into {TABLE} values ({placeholders}, current_timestamp)",
            [[row.get(name) for name in COLUMNS] for row in rows],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise


def band_counts(rows: list[dict[str, object]]) -> Counter:
    """How many players per band (auto, review, rejected, manual, ...)."""
    return Counter(row["band"] for row in rows)


def match_rate(rows: list[dict[str, object]]) -> float:
    """Share of football-data.org players with an accepted match (auto or manual)."""
    matched = sum(row["band"] in MATCHED_BANDS for row in rows)
    return matched / len(rows) if rows else 0.0


def run(con: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    """Match, apply corrections, write the table; returns the rows for metrics."""
    tm_names = transfermarkt_names(con)
    rows = match_players(con, tm_names)
    rows = apply_corrections(rows, read_corrections(), tm_names)
    write_matches(con, rows)
    return rows


def main() -> None:
    """Command line entry point: `uv run python -m fdp.matching.player_map`."""
    setup_logging()
    with connect() as con:
        rows = run(con)
    logger.info("bands: %s", dict(band_counts(rows)))
    logger.info("match rate: %.1f%% of %d players", 100 * match_rate(rows), len(rows))


# Runs only when the file is executed directly, not when it is imported (tests, Dagster).
if __name__ == "__main__":
    main()
