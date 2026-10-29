-- One row per player and match, from Transfermarkt appearances (only Transfermarkt
-- has them; the dataset stops in July 2026, so 2024/25 and 2025/26 only).
-- Linked to the project keys: match through map_match, team through map_team,
-- player through his Transfermarkt id (every appearing player is in dim_player).

select
    appearances.tm_appearance_id as appearance_key,
    'tm_' || appearances.tm_player_id as player_key,
    map_match.match_key,
    -- The club the player played for in this match.
    map_team.team_key,
    map_match.season as season_key,
    competition.competition_key,
    appearances.match_date,
    appearances.minutes_played,
    appearances.goals,
    appearances.assists,
    appearances.yellow_cards,
    appearances.red_cards
from {{ ref('stg_transfermarkt__appearances') }} as appearances
join {{ ref('map_match') }} as map_match using (tm_game_id)
join {{ ref('map_team') }} as map_team on map_team.tm_club_id = appearances.tm_club_id
join {{ ref('dim_competition') }} as competition
    on competition.tm_competition_id = appearances.tm_competition_id
