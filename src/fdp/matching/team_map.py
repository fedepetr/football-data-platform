"""Draft of the team map across sources, for manual review before it becomes a dbt seed.

football-data.org teams are the hub: every couk name and Transfermarkt club is
matched to a football-data.org team with two independent methods:
- names: ordered letters (LCS) with tie-break and consistency checks (teams.py);
- fixtures: the same match has the same date and score in both sources, so the
  teams of matching rows "vote" for each other; the most voted pair wins.
A match is "sure" only if both methods agree and no check fails; otherwise it is
marked "review" with the reasons. Output: data/team_map_review.csv.

Run: `uv run python -m fdp.matching.team_map` (reads the staging views in DuckDB).
"""

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb

from fdp.config import load_config
from fdp.ingest.common import DATA_DIR, DB_PATH, setup_logging
from fdp.matching.teams import (
    NameMatch,
    find_collisions,
    find_not_mutual,
    match_names,
    normalize_words,
)

logger = logging.getLogger(__name__)

REVIEW_PATH = DATA_DIR / "team_map_review.csv"
# Seed columns first (what the reviewed seed keeps), then the review columns.
COLUMNS = [
    "team_key", "team_name", "country", "source", "source_team_ref",
    "source_team_name", "name_score", "fixture_votes", "fixture_share", "status", "reasons",
]  # fmt: skip

# Fixture votes: one row per (source team, football-data.org team) seen in the
# same match, home with home and away with away; keeps the best pair per source team.
FIXTURE_VOTES_SQL = """
with fdorg as (
    select competition_code, match_date_utc as match_date, home_team_id, away_team_id,
           home_goals, away_goals
    from staging.stg_fdorg__matches
    where status = 'FINISHED'
),
other as ({other_sql}),
pairs as (
    select other.home_ref as source_ref, fdorg.home_team_id as fdorg_team_id
    from other join fdorg using (competition_code, match_date, home_goals, away_goals)
    union all
    select other.away_ref, fdorg.away_team_id
    from other join fdorg using (competition_code, match_date, home_goals, away_goals)
),
votes as (
    select source_ref, fdorg_team_id, count(*) as votes
    from pairs
    group by all
),
ranked as (
    select
        *,
        votes / sum(votes) over (partition by source_ref) as share,
        row_number() over (partition by source_ref order by votes desc) as rank
    from votes
)
select source_ref, fdorg_team_id, votes, share from ranked where rank = 1
"""

# Matches of each source in the shape the vote query expects, with the
# football-data.org competition code so only the same league is compared.
# unnest($a), unnest($b) pairs the two code lists row by row (SA-I1, PL-E0).
COUK_MATCHES_SQL = """
select codes.fdorg as competition_code, match_date, home_team_name as home_ref,
       away_team_name as away_ref, home_goals, away_goals
from staging.stg_couk__matches
join (select unnest($fdorg) as fdorg, unnest($other) as other) as codes
    on competition_code = codes.other
"""
TRANSFERMARKT_GAMES_SQL = """
select codes.fdorg as competition_code, match_date, home_tm_club_id as home_ref,
       away_tm_club_id as away_ref, home_goals, away_goals
from staging.stg_transfermarkt__games
join (select unnest($fdorg) as fdorg, unnest($other) as other) as codes
    on tm_competition_id = codes.other
"""


@dataclass(frozen=True)
class FixtureVote:
    """football-data.org team most often found in the same matches as a source team."""

    fdorg_team_id: int
    votes: int
    # Share of the source team's votes going to this team (the rest is noise:
    # other matches on the same day with the same score).
    share: float


def fixture_votes(
    con: duckdb.DuckDBPyConnection, other_sql: str, fdorg_codes: list[str], other_codes: list[str]
) -> dict[str | int, FixtureVote]:
    """Run the vote query for one source; returns source team ref -> best vote."""
    rows = con.execute(
        FIXTURE_VOTES_SQL.format(other_sql=other_sql),
        {"fdorg": fdorg_codes, "other": other_codes},
    ).fetchall()
    return {ref: FixtureVote(team_id, votes, share) for ref, team_id, votes, share in rows}


def review_reasons(
    match: NameMatch,
    fixture_choice: object | None,
    collisions: set[str],
    not_mutual: set[str],
) -> list[str]:
    """Why a match needs a human look; an empty list means "sure".

    `fixture_choice` is what the fixtures picked (None if no shared matches),
    compared with what the names picked (`match.candidate_id`).
    """
    reasons = []
    if fixture_choice is None:
        reasons.append("no_fixtures")
    elif fixture_choice != match.candidate_id:
        reasons.append("disagree")
    if match.tie:
        reasons.append("tie")
    if match.name in collisions:
        reasons.append("collision")
    if match.name in not_mutual:
        reasons.append("not_mutual")
    return reasons


def slug(name: str) -> str:
    """Readable project key from a name: "Man United" -> "man_united"."""
    return "_".join(normalize_words(name))


def fdorg_teams(con: duckdb.DuckDBPyConnection, competition: str) -> dict[int, tuple[str, ...]]:
    """football-data.org teams of one league: id -> (official name, short name, country)."""
    rows = con.execute(
        "select distinct fdorg_team_id, team_name, short_name, country "
        "from staging.stg_fdorg__teams where competition_code = ?",
        [competition],
    ).fetchall()
    return {team_id: (name, short, country) for team_id, name, short, country in rows}


def team_row(
    teams: dict[int, tuple[str, ...]], team_id: int, source: str, ref: object,
    source_name: str, **review: object,
) -> dict[str, object]:  # fmt: skip
    """One draft row: project team (from the fdorg hub) + source reference + review info."""
    _, short, country = teams[team_id]
    return {
        "team_key": slug(short),
        "team_name": short,
        "country": country,
        "source": source,
        "source_team_ref": ref,
        "source_team_name": source_name,
        **review,
    }


def review_columns(
    match: NameMatch, vote: FixtureVote | None, reasons: list[str]
) -> dict[str, object]:
    """Columns that explain how sure a match is."""
    return {
        "name_score": round(match.score, 2),
        "fixture_votes": vote.votes if vote else "",
        "fixture_share": round(vote.share, 2) if vote else "",
        "status": "review" if reasons else "sure",
        "reasons": " ".join(reasons),
    }


def couk_rows(
    con: duckdb.DuckDBPyConnection,
    teams: dict[int, tuple[str, ...]],
    couk_code: str,
    votes: dict[str | int, FixtureVote],
) -> list[dict[str, object]]:
    """football-data.co.uk names of one league -> football-data.org official names."""
    names = [
        name
        for (name,) in con.execute(
            "select distinct home_team_name from staging.stg_couk__matches "
            "where competition_code = ?",
            [couk_code],
        ).fetchall()
    ]
    official = {team_id: name for team_id, (name, _, _) in teams.items()}
    matches = match_names(names, official)
    collisions, not_mutual = find_collisions(matches), find_not_mutual(matches)

    rows = []
    for name, match in matches.items():
        vote = votes.get(name)
        fixture_choice = vote.fdorg_team_id if vote else None
        reasons = review_reasons(match, fixture_choice, collisions, not_mutual)
        # Fixtures are the stronger evidence: they win when both exist.
        team_id = fixture_choice or match.candidate_id
        rows.append(
            team_row(teams, team_id, "couk", name, name, **review_columns(match, vote, reasons))
        )
    return rows


def transfermarkt_rows(
    con: duckdb.DuckDBPyConnection,
    teams: dict[int, tuple[str, ...]],
    tm_code: str,
    votes: dict[str | int, FixtureVote],
) -> list[dict[str, object]]:
    """football-data.org short names of one league -> Transfermarkt clubs.

    This direction (fdorg -> club) also covers teams with no Transfermarkt games
    (promoted in 2026/27): their candidates come from the clubs table.
    """
    clubs = dict(
        con.execute(
            "select tm_club_id, club_name from staging.stg_transfermarkt__clubs "
            "where tm_competition_id = $tm "
            "union select home_tm_club_id, home_club_name "
            "from staging.stg_transfermarkt__games where tm_competition_id = $tm",
            {"tm": tm_code},
        ).fetchall()
    )
    # Votes are indexed by club; here we need them by football-data.org team.
    vote_by_team = {vote.fdorg_team_id: (club, vote) for club, vote in votes.items()}
    short_names = {short: team_id for team_id, (_, short, _) in teams.items()}
    matches = match_names(short_names, clubs)
    collisions, not_mutual = find_collisions(matches), find_not_mutual(matches)

    rows = []
    for short, match in matches.items():
        team_id = short_names[short]
        fixture_club, vote = vote_by_team.get(team_id, (None, None))
        reasons = review_reasons(match, fixture_club, collisions, not_mutual)
        club = fixture_club or match.candidate_id
        rows.append(
            team_row(
                teams,
                team_id,
                "transfermarkt",
                club,
                clubs.get(club, ""),
                **review_columns(match, vote, reasons),
            )  # fmt: skip
        )
    return rows


def build_review_rows(con: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    """One row per team and source (fdorg, couk, transfermarkt); rows to review first."""
    competitions = load_config()["competitions"]
    fdorg_codes = [c["fdorg"] for c in competitions]
    couk_votes = fixture_votes(
        con, COUK_MATCHES_SQL, fdorg_codes, [c["couk"] for c in competitions]
    )
    tm_votes = fixture_votes(
        con, TRANSFERMARKT_GAMES_SQL, fdorg_codes, [c["transfermarkt"] for c in competitions]
    )

    rows: list[dict[str, object]] = []
    for competition in competitions:
        teams = fdorg_teams(con, competition["fdorg"])
        # football-data.org is the hub: its own rows are sure by definition.
        rows += [
            team_row(teams, team_id, "fdorg", team_id, name, status="sure", reasons="")
            for team_id, (name, _, _) in teams.items()
        ]
        rows += couk_rows(con, teams, competition["couk"], couk_votes)
        rows += transfermarkt_rows(con, teams, competition["transfermarkt"], tm_votes)

    return sorted(rows, key=lambda row: (row["status"] != "review", row["team_key"], row["source"]))


def write_review(rows: list[dict[str, object]], path: Path = REVIEW_PATH) -> Path:
    """Write the draft as CSV (git-ignored, under data/)."""
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, restval="")
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    """Command line entry point: `uv run python -m fdp.matching.team_map`."""
    setup_logging()
    # Read-only: the draft never changes the database (and does not lock it for writes).
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        rows = build_review_rows(con)
    path = write_review(rows)
    to_review = sum(row["status"] == "review" for row in rows)
    logger.info("wrote %s: %d rows, %d to review", path, len(rows), to_review)


# Runs only when the file is executed directly, not when it is imported (tests, Dagster).
if __name__ == "__main__":
    main()
