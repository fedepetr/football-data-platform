-- One row per player and valuation date (market value history), every player.

select
    player_id::integer as tm_player_id,
    date::date as valuation_date,
    market_value_in_eur::double as market_value_eur,
    current_club_id::integer as tm_club_id,
    current_club_name as club_name,
    player_club_domestic_competition_id as tm_competition_id,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_player_valuations') }}
