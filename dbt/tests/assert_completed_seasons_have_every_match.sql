-- A completed season must have every match of a double round-robin, all finished:
-- teams x (teams - 1) matches (380 for the 20-team Serie A and Premier League,
-- as the brief asks). The expected number comes from the teams in the data, so
-- the test also works for leagues of another size (e.g. 18 teams -> 306).
-- Returns the competition-seasons that do not add up.

with teams_per_season as (

    select competition_key, season_key, count(distinct home_team_key) as teams
    from {{ ref('fct_match') }}
    group by all

),

matches_per_season as (

    select
        competition_key,
        season_key,
        count(*) as matches,
        count(*) filter (where status = 'FINISHED') as finished_matches
    from {{ ref('fct_match') }}
    group by all

)

select
    matches.competition_key,
    matches.season_key,
    teams.teams,
    teams.teams * (teams.teams - 1) as expected_matches,
    matches.matches,
    matches.finished_matches
from matches_per_season as matches
join teams_per_season as teams using (competition_key, season_key)
join {{ ref('dim_season') }} as seasons using (season_key)
-- Only completed seasons: the current one is still being played.
where not seasons.is_current
    and (
        matches.matches <> teams.teams * (teams.teams - 1)
        or matches.finished_matches <> teams.teams * (teams.teams - 1)
    )
