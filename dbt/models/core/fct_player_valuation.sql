-- One row per player and valuation date: market value history from Transfermarkt.
-- Only players in scope (dim_player); their full history is kept, also from
-- seasons before 2024/25, so a profile can show how the value evolved.

select
    players.player_key,
    valuations.valuation_date,
    valuations.market_value_eur,
    valuations.tm_club_id,
    valuations.club_name,
    -- Project team when the club is one of ours, NULL for every other club.
    map_team.team_key
from {{ ref('stg_transfermarkt__player_valuations') }} as valuations
join {{ ref('dim_player') }} as players
    on players.player_key = 'tm_' || valuations.tm_player_id
left join {{ ref('map_team') }} as map_team using (tm_club_id)
