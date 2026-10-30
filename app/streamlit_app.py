"""Home page of the demo: what the platform is and what is inside.

Run locally from the project root: `uv run --group demo streamlit run app/streamlit_app.py`.
Pages in app/pages/ appear in the sidebar automatically (Streamlit multipage apps).
"""

import streamlit as st

from data import data_source, query

st.set_page_config(page_title="Football Data Platform", page_icon="⚽", layout="wide")

st.title("⚽ Football Data Platform")
st.markdown(
    "Serie A and Premier League data from **three sources** "
    "(football-data.org, football-data.co.uk, Transfermarkt), "
    "**reconciled** into one model: the same player, team and match "
    "has one key whatever the source."
)

# Key numbers, read from the published tables.
numbers = query(
    """
    select
        (select count(*) from dim_player) as players,
        (select count(*) from dim_team) as teams,
        (select count(*) from fct_match where status = 'FINISHED') as matches_played,
        (select max(match_date) from fct_match where status = 'FINISHED') as last_match,
        (select count(*) filter (where band in ('auto', 'manual')) / count(*)
            from player_matches) as match_rate
    """
).iloc[0]

columns = st.columns(4)
columns[0].metric("Players", f"{numbers.players:,}")
columns[1].metric("Teams", numbers.teams)
columns[2].metric("Matches played", f"{numbers.matches_played:,}")
columns[3].metric("Players matched across sources", f"{numbers.match_rate:.1%}")
st.caption(f"Data up to {numbers.last_match:%d %B %Y} · refreshed every Tuesday")

st.subheader("Pages")
st.markdown(
    """
- **Player**: profile, season statistics, market value history, and the player's
  identity in each source.
- **Similar players**: the five closest players by statistics per 90 minutes,
  across both leagues or within the player's own league.
- **Data quality**: how well the sources were reconciled, and the checks that
  prove it.
"""
)

st.subheader("Sources and limits")
st.markdown(
    """
| Source | Used for | Limit |
| --- | --- | --- |
| football-data.org | fixtures, scores, squads, scorers | free plan: no line-ups |
| football-data.co.uk | match statistics, xG | xG only from 2026/27 |
| Transfermarkt | appearances, market values, transfers | frozen on 6 July 2026 |
"""
)

with st.expander("About this demo"):
    st.markdown(
        "Built with Python, DuckDB, dbt and Dagster; the weekly pipeline runs on "
        "GitHub Actions. Code and documentation: "
        "[github.com/fedepetr/football-data-platform]"
        "(https://github.com/fedepetr/football-data-platform)."
    )
    st.caption(f"Data source: {data_source()}")
