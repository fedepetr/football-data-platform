"""Data quality page: how well the sources were reconciled, and the checks that prove it.

Every number is computed from the published tables at each run, so the page stays
true after the weekly refresh (the README quotes the values of one day).
"""

import streamlit as st

from demo_data import query

st.set_page_config(page_title="Data quality", page_icon="✅", layout="wide")
st.title("✅ Data quality")
st.markdown(
    "Three sources describe the same players and matches with different ids and "
    "names. This page shows **how many** were reconciled and **whether they were "
    "reconciled right**."
)

# --- 1. Player reconciliation -------------------------------------------------------
st.header("1. Player reconciliation")
st.markdown(
    "Each football-data.org player is matched to a Transfermarkt player: candidates "
    "with the same birth date or the same team and season, then a name score 0-100. "
    "Above 92 automatic, 80-92 review, below 80 rejected; reviewed cases are decided "
    "by hand in a corrections file."
)

bands = query(
    """
    select
        band,
        count(*) as players,
        count(*) / sum(count(*)) over () as share
    from player_matches
    group by band
    order by players desc
    """
)
matched = bands.loc[bands.band.isin(["auto", "manual"]), "share"].sum()

columns = st.columns(3)
columns[0].metric(
    "Players matched", f"{matched:.1%}", help="auto + manual; the pipeline stops below 90%"
)
columns[1].metric("Football-data.org players", f"{bands.players.sum():,}")
columns[2].metric(
    "Reviewed by hand", int(bands.loc[bands.band.str.startswith("manual"), "players"].sum())
)

left, right = st.columns(2)
with left:
    st.subheader("Players per band")
    st.dataframe(
        bands,
        hide_index=True,
        column_config={
            "band": "Band",
            "players": "Players",
            "share": st.column_config.NumberColumn("Share", format="percent"),
        },
    )
with right:
    st.subheader("Score of the best candidate")
    # Buckets of the 0-100 name score; the review band is 80-92.
    scores = query(
        """
        select
            case
                when match_score is null then 'no candidate'
                when match_score = 100 then '100'
                when match_score > 92 then '92-99'
                when match_score >= 80 then '80-92 (review)'
                when match_score >= 60 then '60-79'
                else 'below 60'
            end as score,
            count(*) as players
        from player_matches
        group by 1
        """
    )
    order = ["100", "92-99", "80-92 (review)", "60-79", "below 60", "no candidate"]
    st.bar_chart(scores.set_index("score").reindex(order).dropna(), horizontal=True)

st.subheader("Hard cases decided by hand")
st.caption(
    "Names that no string similarity would match: the sources use different parts of "
    "the full name. Flagged because birth date and team agree, then confirmed by hand."
)
st.dataframe(
    query(
        """
        select fdorg_player_name, tm_player_name, match_score, flags as note
        from player_matches
        where band = 'manual'
        order by match_score
        """
    ),
    hide_index=True,
    column_config={
        "fdorg_player_name": "football-data.org",
        "tm_player_name": "Transfermarkt",
        "match_score": st.column_config.NumberColumn("Name score", format="%.1f"),
        "note": "Why",
    },
)

# --- 2. Are the matches right? --------------------------------------------------------
st.header("2. Are the matches right?")
st.markdown(
    "Two independent sources count the goals of each matched player: football-data.org "
    "(scorers) and Transfermarkt (sum of appearances). If a player had been matched to "
    "the wrong person, his goals would not agree."
)

goals = query(
    """
    with transfermarkt as (
        select player_key, season_key, competition_key, sum(goals) as tm_goals
        from fct_player_appearance
        group by all
    )
    select
        abs(scoring.goals - transfermarkt.tm_goals)::integer as goal_difference,
        count(*) as player_seasons
    from fct_player_season_scoring as scoring
    join transfermarkt using (player_key, season_key, competition_key)
    group by 1
    order by 1
    """
)
equal = goals.loc[goals.goal_difference == 0, "player_seasons"].sum() / goals.player_seasons.sum()
columns = st.columns(2)
columns[0].metric("Player-seasons with identical goals", f"{equal:.1%}")
columns[1].metric(
    "Differing by more than one goal",
    int(goals.loc[goals.goal_difference > 1, "player_seasons"].sum()),
)
st.dataframe(
    goals,
    hide_index=True,
    column_config={"goal_difference": "Goal difference", "player_seasons": "Player-seasons"},
)

st.subheader("Season totals in the two sources")
st.dataframe(
    query(
        """
        with fdorg as (
            select season_key, competition_key, sum(goals) as fdorg_goals
            from fct_player_season_scoring
            group by all
        ),
        transfermarkt as (
            select season_key, competition_key, sum(goals) as tm_goals
            from fct_player_appearance
            group by all
        )
        select season_key, competition_key, fdorg_goals::integer as fdorg_goals,
               tm_goals::integer as tm_goals
        from fdorg
        join transfermarkt using (season_key, competition_key)
        order by season_key, competition_key
        """
    ),
    hide_index=True,
    column_config={
        "season_key": "Season",
        "competition_key": "League",
        "fdorg_goals": "Goals (football-data.org)",
        "tm_goals": "Goals (Transfermarkt)",
    },
)

# --- 3. Match coverage --------------------------------------------------------------
st.header("3. Matches in each source")
st.markdown(
    "Every played match of football-data.org is linked to the same match in the other "
    "sources (date + home team + away team, after team reconciliation). Transfermarkt "
    "stops in July 2026, so it has no 2026/27 matches."
)
st.dataframe(
    query(
        """
        select
            season_key,
            competition_key,
            count(*) filter (where status = 'FINISHED') as played,
            count(*) filter (where status = 'FINISHED' and in_couk) as in_football_data_co_uk,
            count(*) filter (where status = 'FINISHED' and in_transfermarkt) as in_transfermarkt
        from fct_match
        group by all
        order by season_key, competition_key
        """
    ),
    hide_index=True,
    column_config={
        "season_key": "Season",
        "competition_key": "League",
        "played": "Played",
        "in_football_data_co_uk": "Linked in football-data.co.uk",
        "in_transfermarkt": "Linked in Transfermarkt",
    },
)

st.caption(
    "Also checked by the pipeline (dbt tests, 171 in total): unique keys, relationships "
    "between facts and dimensions, identical scores in every source, and completed "
    "seasons with every match played."
)
