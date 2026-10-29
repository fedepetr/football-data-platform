-- One row per game from Transfermarkt, already filtered at load time to the
-- competitions (IT1, GB1) and seasons in scope.

select
    game_id::integer as tm_game_id,
    competition_id as tm_competition_id,
    -- Project season code ("2425"), set at load time from the start year below.
    _season as season,
    season::integer as season_start_year,
    round,
    date::date as match_date,
    home_club_id::integer as home_tm_club_id,
    home_club_name,
    away_club_id::integer as away_tm_club_id,
    away_club_name,
    home_club_goals::integer as home_goals,
    away_club_goals::integer as away_goals,
    home_club_manager_name,
    away_club_manager_name,
    stadium,
    attendance::integer as attendance,
    referee,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_games') }}
