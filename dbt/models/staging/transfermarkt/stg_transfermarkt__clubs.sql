-- One row per club from Transfermarkt.
-- Formatted text (e.g. net_transfer_record '+€5.90m') and always-empty columns
-- (total_market_value) are left in raw.

select
    club_id::integer as tm_club_id,
    name as club_name,
    domestic_competition_id as tm_competition_id,
    squad_size::integer as squad_size,
    average_age::double as average_age,
    stadium_name,
    stadium_seats::integer as stadium_seats,
    coach_name,
    last_season::integer as last_season,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_clubs') }}
