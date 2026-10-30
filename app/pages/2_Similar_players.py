"""Similar players page: the five closest players by statistics per 90 minutes.

Neighbours come from mart_similar_players (exact k-nearest neighbours on per-90
z-scores, within season and position). The per-90 comparison below is recomputed
from the appearances with the same formulas, to show why the players are close.
"""

import streamlit as st

from demo_data import query

st.set_page_config(page_title="Similar players", page_icon="🧭", layout="wide")
st.title("🧭 Similar players")
st.markdown(
    "The five players closest to a player in a completed season, by **goals, assists, "
    "yellow and red cards per 90 minutes** and **share of minutes played**, compared "
    "only with players of the same position (at least 900 minutes)."
)

# Players with neighbours: completed seasons only (minutes come from Transfermarkt).
players = query(
    """
    select distinct
        s.player_key,
        s.player_name || ' (' || p.team_name || ', ' || s.position || ')' as label
    from mart_similar_players as s
    join mart_player_profile as p using (player_key, season_key)
    -- One label per player: the team of his latest completed season.
    qualify row_number() over (partition by s.player_key order by s.season_key desc) = 1
    order by label
    """
)
labels = dict(zip(players.label, players.player_key, strict=True))
default = next((i for i, label in enumerate(labels) if label.startswith("Lautaro Martínez")), 0)

left, middle, right = st.columns([3, 1, 2])
player_key = labels[left.selectbox("Player", list(labels), index=default)]
seasons = query(
    "select distinct season_key from mart_similar_players where player_key = ? order by 1 desc",
    (player_key,),
).season_key.tolist()
season_key = middle.selectbox("Season", seasons, format_func=lambda s: f"20{s[:2]}/{s[2:]}")
scope = right.radio(
    "Compare with",
    ["same_competition", "all_competitions"],
    format_func={"same_competition": "His league", "all_competitions": "Both leagues"}.get,
    horizontal=True,
)

# --- The five neighbours ------------------------------------------------------------
neighbours = query(
    """
    select
        s.similarity_rank,
        s.similar_player_key,
        s.similar_player_name,
        p.team_name,
        s.similar_competition_key,
        s.distance,
        s.similarity
    from mart_similar_players as s
    left join mart_player_profile as p
        on p.player_key = s.similar_player_key and p.season_key = s.season_key
    where s.player_key = ? and s.season_key = ? and s.comparison_scope = ?
    order by s.similarity_rank
    """,
    (player_key, season_key, scope),
)
st.subheader("Most similar")
st.dataframe(
    neighbours.drop(columns="similar_player_key"),
    hide_index=True,
    column_config={
        "similarity_rank": "#",
        "similar_player_name": "Player",
        "team_name": "Team",
        "similar_competition_key": "League",
        "distance": st.column_config.NumberColumn("Distance", format="%.3f"),
        "similarity": st.column_config.ProgressColumn(
            "Similarity", min_value=0, max_value=100, format="%.0f"
        ),
    },
)

# --- Why they are similar: the statistics side by side -----------------------------
# Same formulas as the mart: per 90 minutes, and minutes over 38 full matches.
keys = [player_key, *neighbours.similar_player_key]
placeholders = ", ".join("?" for _ in keys)
per_90 = query(
    f"""
    select
        p.full_name as player,
        sum(a.minutes_played) as minutes,
        sum(a.goals) * 90.0 / sum(a.minutes_played) as goals_per_90,
        sum(a.assists) * 90.0 / sum(a.minutes_played) as assists_per_90,
        sum(a.yellow_cards) * 90.0 / sum(a.minutes_played) as yellow_per_90,
        sum(a.red_cards) * 90.0 / sum(a.minutes_played) as red_per_90,
        sum(a.minutes_played) / (38 * 90.0) as minutes_share
    from fct_player_appearance as a
    join dim_player as p using (player_key)
    where a.season_key = ? and a.player_key in ({placeholders})
    group by p.full_name, a.player_key
    -- The selected player first, then the neighbours in rank order.
    order by array_position(?, a.player_key)
    """,
    (season_key, *keys, keys),
)

st.subheader("Statistics per 90 minutes")
st.dataframe(
    per_90,
    hide_index=True,
    column_config={
        "player": "Player",
        "minutes": "Minutes",
        "goals_per_90": st.column_config.NumberColumn("Goals / 90", format="%.2f"),
        "assists_per_90": st.column_config.NumberColumn("Assists / 90", format="%.2f"),
        "yellow_per_90": st.column_config.NumberColumn("Yellow / 90", format="%.2f"),
        "red_per_90": st.column_config.NumberColumn("Red / 90", format="%.3f"),
        "minutes_share": st.column_config.NumberColumn("Share of minutes", format="percent"),
    },
)
st.bar_chart(
    per_90.set_index("player")[["goals_per_90", "assists_per_90"]].rename(
        columns={"goals_per_90": "Goals / 90", "assists_per_90": "Assists / 90"}
    ),
    stack=False,
    horizontal=True,
)
st.caption(
    "Similarity uses five output statistics, standardised within season and position: "
    "it means a similar output (scoring, creating, discipline, playing time), not a "
    "similar playing style, which would need event data."
)
