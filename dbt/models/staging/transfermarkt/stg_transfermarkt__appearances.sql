-- One row per player appearance in a game in scope (filtered at load time
-- through the loaded games). Only up to 2025/26: the dataset stops in July 2026.

select
    appearance_id as tm_appearance_id,
    game_id::integer as tm_game_id,
    player_id::integer as tm_player_id,
    -- Club the player played for in this game (not the current club).
    player_club_id::integer as tm_club_id,
    _season as season,
    date::date as match_date,
    player_name,
    competition_id as tm_competition_id,
    minutes_played::integer as minutes_played,
    goals::integer as goals,
    assists::integer as assists,
    yellow_cards::integer as yellow_cards,
    red_cards::integer as red_cards,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_appearances') }}
