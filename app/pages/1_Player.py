"""Player page: profile, seasons, market value history and identity in each source."""

import pandas as pd
import streamlit as st

from demo_data import query

st.set_page_config(page_title="Player", page_icon="👤", layout="wide")
st.title("👤 Player")

# Every player with at least one season in the profile. The label adds the latest
# team and position: different players can share a name.
players = query(
    """
    select
        player_key,
        arg_max(full_name, season_key)
            || ' (' || coalesce(arg_max(team_name, season_key), '?')
            || ', ' || coalesce(arg_max(position, season_key), '?') || ')' as label
    from mart_player_profile
    group by player_key
    order by label
    """
)
labels = dict(zip(players.label, players.player_key, strict=True))
default = next((i for i, label in enumerate(labels) if label.startswith("Lautaro Martínez")), 0)
player_key = labels[st.selectbox("Search a player", list(labels), index=default)]

seasons = query(
    "select * from mart_player_profile where player_key = ? order by season_key",
    (player_key,),
)
latest = seasons.iloc[-1]

# --- Profile ------------------------------------------------------------------
st.header(latest.full_name)
columns = st.columns(5)
columns[0].metric("Age", latest.age)
columns[1].metric("Nationality", latest.nationality or "-")
columns[2].metric("Position", latest.position or "-")
columns[3].metric("Team", latest.team_name or "-")
value = latest.market_value_eur
columns[4].metric("Market value", f"€{value / 1e6:.1f}m" if pd.notna(value) else "-")

# --- Seasons --------------------------------------------------------------------
st.subheader("Seasons")
st.dataframe(
    seasons[
        [
            "season_label",
            "team_name",
            "competition_key",
            "appearances",
            "minutes_played",
            "goals",
            "assists",
            "yellow_cards",
            "red_cards",
            "market_value_eur",
            "stats_source",
        ]
    ],
    hide_index=True,
    column_config={
        "season_label": "Season",
        "team_name": "Team",
        "competition_key": "League",
        "appearances": "Apps",
        "minutes_played": "Minutes",
        "goals": "Goals",
        "assists": "Assists",
        "yellow_cards": "Yellow",
        "red_cards": "Red",
        "market_value_eur": st.column_config.NumberColumn("Value (€)", format="compact"),
        "stats_source": "Source",
    },
)
st.caption(
    "One source per season: Transfermarkt up to 2025/26 (every appearance), "
    "football-data.org for 2026/27 (scorers only, no minutes or cards: the free plan "
    "has no line-ups). Assists are counted differently by the two sources."
)

# --- Market value history -------------------------------------------------------
st.subheader("Market value")
valuations = query(
    """
    select valuation_date as date, market_value_eur / 1e6 as "Value (€m)"
    from fct_player_valuation
    where player_key = ?
    order by valuation_date
    """,
    (player_key,),
)
if valuations.empty:
    st.info("No Transfermarkt valuation for this player.")
else:
    st.line_chart(valuations, x="date", y="Value (€m)")

# --- Identity in each source ----------------------------------------------------
st.subheader("In each source")
identity = query(
    """
    select fdorg_person_id, tm_player_id, match_method, match_score
    from map_player
    where player_key = ?
    """,
    (player_key,),
)
if identity.empty:
    # Transfermarkt-only player: appearances in scope but in no football-data.org squad.
    st.write(
        f"Transfermarkt id **{player_key.removeprefix('tm_')}**; not in football-data.org squads."
    )
else:
    st.dataframe(
        identity,
        hide_index=True,
        column_config={
            "fdorg_person_id": "football-data.org id",
            "tm_player_id": "Transfermarkt id",
            "match_method": "How it was matched",
            "match_score": st.column_config.NumberColumn("Name score", format="%.1f"),
        },
    )
    if len(identity) > 1:
        st.caption(
            "Several football-data.org ids for one person: duplicates in the source, "
            "merged by the reconciliation."
        )
if player_key.startswith("tm_"):
    st.markdown(
        f"[Transfermarkt profile](https://www.transfermarkt.com/-/profil/spieler/"
        f"{player_key.removeprefix('tm_')})"
    )
