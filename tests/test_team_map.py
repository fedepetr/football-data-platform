import duckdb

from fdp.matching.team_map import (
    COUK_MATCHES_SQL,
    FixtureVote,
    fixture_votes,
    review_reasons,
    slug,
)
from fdp.matching.teams import NameMatch

MATCH = NameMatch("Inter", 108, "FC Internazionale Milano", 1.0, tie=False)


def test_review_reasons_sure_when_everything_agrees() -> None:
    """Names and fixtures agree, no tie, no failed check: nothing to review."""
    assert review_reasons(MATCH, 108, collisions=set(), not_mutual=set()) == []


def test_review_reasons_lists_every_problem() -> None:
    """Each failed check adds its reason, so the reviewer knows what to look at."""
    tied = NameMatch("Milan", 108, "FC Internazionale Milano", 1.0, tie=True)
    reasons = review_reasons(tied, 98, collisions={"Milan"}, not_mutual={"Milan"})
    assert reasons == ["disagree", "tie", "collision", "not_mutual"]
    assert review_reasons(MATCH, None, set(), set()) == ["no_fixtures"]


def test_slug() -> None:
    """Team keys are readable lowercase words joined by underscores."""
    assert slug("Man United") == "man_united"


def test_fixture_votes_pairs_teams_of_the_same_matches() -> None:
    """Same date and score: the teams vote for each other; noise loses on share."""
    con = duckdb.connect()
    con.execute("create schema staging")
    # Two fdorg matches on the same day with the same score: one is noise for the other.
    con.execute(
        """
        create table staging.stg_fdorg__matches as select * from (values
            ('SA', date '2026-08-22', 115, 7397, 1, 1, 'FINISHED'),
            ('SA', date '2026-08-22', 98, 107, 1, 1, 'FINISHED'),
            ('SA', date '2026-08-29', 7397, 110, 2, 0, 'FINISHED')
        ) as t(competition_code, match_date_utc, home_team_id, away_team_id,
               home_goals, away_goals, status)
        """
    )
    con.execute(
        """
        create table staging.stg_couk__matches as select * from (values
            ('I1', date '2026-08-22', 'Udinese', 'Como', 1, 1),
            ('I1', date '2026-08-29', 'Como', 'Lazio', 2, 0)
        ) as t(competition_code, match_date, home_team_name, away_team_name,
               home_goals, away_goals)
        """
    )

    votes = fixture_votes(con, COUK_MATCHES_SQL, ["SA"], ["I1"])

    # Como: 2 votes for 7397 (both matches), 1 for 107 (same-day noise).
    assert votes["Como"] == FixtureVote(7397, 2, 2 / 3)
    assert votes["Lazio"] == FixtureVote(110, 1, 1.0)
