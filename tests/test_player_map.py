from datetime import date
from pathlib import Path

import duckdb
import pytest

from fdp.matching.player_map import (
    Correction,
    apply_corrections,
    candidate_source,
    collisions,
    match_players,
    match_rate,
    read_corrections,
    write_matches,
)
from fdp.matching.players import PlayerMatch


def player_match(tm_id: int, band: str = "auto") -> PlayerMatch:
    """A PlayerMatch with only the fields these tests care about."""
    return PlayerMatch(tm_id, "name", 100.0, "damerau_levenshtein", band, False, True)


def test_candidate_source() -> None:
    """Records which rule proposed the chosen candidate."""
    assert candidate_source(1, {1}, {1}) == "both"
    assert candidate_source(1, {1}, set()) == "birth_date"
    assert candidate_source(1, set(), {1}) == "team_season"


def test_collisions_ignore_duplicate_ids_of_one_person() -> None:
    """Same Transfermarkt pick: same birth date is a duplicate, different dates a collision."""
    matches = {10: player_match(1), 11: player_match(1), 20: player_match(2), 21: player_match(2)}
    birth_dates = {
        10: date(2001, 9, 8),
        11: date(2001, 9, 8),
        20: date(1989, 3, 13),
        21: date(1999, 7, 7),
    }
    assert collisions(matches, birth_dates) == {2}


def test_read_corrections(tmp_path: Path) -> None:
    """An empty tm_player_id means "this player has no match"."""
    path = tmp_path / "corrections.csv"
    path.write_text("fdorg_person_id,tm_player_id,note\n1,99,nickname\n2,,youth player\n")
    assert read_corrections(path) == {
        1: Correction(99, "nickname"),
        2: Correction(None, "youth player"),
    }


def test_apply_corrections_override_the_algorithm() -> None:
    """Manual decisions win; the band says whether a match was confirmed or refused."""
    rows = [
        {"fdorg_person_id": 1, "tm_player_id": 5, "band": "review"},
        {"fdorg_person_id": 2, "tm_player_id": 6, "band": "review"},
        {"fdorg_person_id": 3, "tm_player_id": 7, "band": "auto"},
    ]
    corrections = {1: Correction(99, "nickname"), 2: Correction(None, "different person")}
    rows = apply_corrections(rows, corrections, {99: "Will Smallbone"})
    assert [(r["tm_player_id"], r["band"]) for r in rows] == [
        (99, "manual"),
        (None, "manual_no_match"),
        (7, "auto"),
    ]


def test_match_rate_counts_auto_and_manual_only() -> None:
    """Review is not a match until confirmed."""
    rows = [{"band": b} for b in ("auto", "manual", "review", "rejected")]
    assert match_rate(rows) == 0.5


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """Tiny staging/core schema: one fdorg squad player, two Transfermarkt players."""
    con = duckdb.connect()
    con.execute("create schema staging; create schema core")
    con.execute(
        """
        create table staging.stg_fdorg__squad_players as select * from (values
            (1, '2425', 108, 'Martínez Lautaro', date '1997-08-22')
        ) as t(fdorg_person_id, season, fdorg_team_id, player_name, date_of_birth)
        """
    )
    con.execute(
        "create table staging.stg_fdorg__scorers as "
        "select * from staging.stg_fdorg__squad_players where false"
    )
    con.execute(
        """
        create table staging.stg_transfermarkt__players as select * from (values
            (406625, 'Lautaro Martínez', date '1997-08-22', 46),
            (500000, 'Lautaro Acosta', date '1988-03-14', 1)
        ) as t(tm_player_id, player_name, date_of_birth, current_tm_club_id)
        """
    )
    con.execute(
        """
        create table staging.stg_transfermarkt__appearances as select * from (values
            ('2425', 46, 406625)
        ) as t(season, tm_club_id, tm_player_id)
        """
    )
    con.execute("create table core.map_team as select 46 as tm_club_id, 108 as fdorg_team_id")
    return con


def test_match_players_end_to_end(con: duckdb.DuckDBPyConnection) -> None:
    """Same birth date and same team: the right player, matched automatically."""
    rows = match_players(con, {406625: "Lautaro Martínez", 500000: "Lautaro Acosta"})
    assert len(rows) == 1
    assert rows[0]["tm_player_id"] == 406625
    assert (rows[0]["band"], rows[0]["candidate_source"]) == ("auto", "both")

    write_matches(con, rows)
    saved = con.execute("select fdorg_person_id, tm_player_id, band from matching.player_matches")
    assert saved.fetchall() == [(1, 406625, "auto")]
