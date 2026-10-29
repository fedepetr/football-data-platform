-- One row per player and season: the player card of the demo (personal data,
-- market value, appearances, goals and assists per season).
--
-- One source per season, never mixed within a row (stats_source says which):
-- - Transfermarkt where it has the season (2024/25, 2025/26): every player who
--   played, with minutes and cards;
-- - football-data.org for the seasons Transfermarkt lacks (2026/27): scorers only,
--   because the free plan has no line-ups.
-- Goals agree between the two sources (98.9% of player-seasons); assists are
-- counted differently (69% agreement), so compare assists across sources with care.

with team_minutes as (

    -- Minutes per player, season and team, to pick the main team of a season.
    select player_key, season_key, team_key, competition_key, sum(minutes_played) as minutes
    from {{ ref('fct_player_appearance') }}
    group by all

),

from_transfermarkt as (

    select
        appearances.player_key,
        appearances.season_key,
        count(*) as appearances,
        sum(appearances.minutes_played) as minutes_played,
        sum(appearances.goals) as goals,
        sum(appearances.assists) as assists,
        sum(appearances.yellow_cards) as yellow_cards,
        sum(appearances.red_cards) as red_cards,
        'transfermarkt' as stats_source
    from {{ ref('fct_player_appearance') }} as appearances
    group by all

),

main_team_transfermarkt as (

    -- A player who moved in January gets the team where he played most.
    select
        player_key,
        season_key,
        arg_max(team_key, minutes) as team_key,
        arg_max(competition_key, minutes) as competition_key,
        count(distinct team_key) as teams
    from team_minutes
    group by all

),

from_fdorg as (

    -- Only the seasons Transfermarkt does not cover.
    select
        player_key,
        season_key,
        sum(played_matches) as appearances,
        null::bigint as minutes_played,
        sum(goals) as goals,
        sum(assists) as assists,
        null::bigint as yellow_cards,
        null::bigint as red_cards,
        'fdorg' as stats_source
    from {{ ref('fct_player_season_scoring') }}
    where season_key not in (select distinct season_key from {{ ref('fct_player_appearance') }})
    group by all

),

main_team_fdorg as (

    select
        player_key,
        season_key,
        arg_max(team_key, played_matches) as team_key,
        arg_max(competition_key, played_matches) as competition_key,
        count(distinct team_key) as teams
    from {{ ref('fct_player_season_scoring') }}
    group by all

),

player_seasons as (

    select stats.*, team.team_key, team.competition_key, team.teams
    from from_transfermarkt as stats
    join main_team_transfermarkt as team using (player_key, season_key)

    union all

    select stats.*, team.team_key, team.competition_key, team.teams
    from from_fdorg as stats
    join main_team_fdorg as team using (player_key, season_key)

),

-- Market value at the end of each season: the last valuation on or before the
-- season's last fixture (for the current season, the latest available).
season_value as (

    select
        player_seasons.player_key,
        player_seasons.season_key,
        arg_max(valuations.market_value_eur, valuations.valuation_date) as market_value_eur,
        max(valuations.valuation_date) as market_value_date
    from player_seasons
    join {{ ref('dim_season') }} as seasons using (season_key)
    join {{ ref('fct_player_valuation') }} as valuations
        on valuations.player_key = player_seasons.player_key
        and valuations.valuation_date <= seasons.last_match_date
    group by all

)

select
    player_seasons.player_key,
    player_seasons.season_key,
    seasons.season_label,
    players.full_name,
    players.birth_date,
    -- Age at the start of the season.
    date_diff('year', players.birth_date, seasons.first_match_date)
        - (strftime(seasons.first_match_date, '%m%d') < strftime(players.birth_date, '%m%d'))::integer
        as age,
    players.nationality,
    players.position,
    player_seasons.team_key,
    teams.team_name,
    player_seasons.teams,
    player_seasons.competition_key,
    player_seasons.appearances,
    player_seasons.minutes_played,
    player_seasons.goals,
    player_seasons.assists,
    player_seasons.yellow_cards,
    player_seasons.red_cards,
    season_value.market_value_eur,
    season_value.market_value_date,
    player_seasons.stats_source
from player_seasons
join {{ ref('dim_player') }} as players using (player_key)
join {{ ref('dim_season') }} as seasons using (season_key)
left join {{ ref('dim_team') }} as teams using (team_key)
left join season_value using (player_key, season_key)
