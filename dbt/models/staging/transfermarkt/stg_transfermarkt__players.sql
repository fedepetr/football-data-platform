-- One row per player from Transfermarkt: every player, not only those in scope,
-- so players who changed league can still be reconciled. Main input of the
-- player reconciliation together with stg_fdorg__squad_players.

select
    player_id::integer as tm_player_id,
    name as player_name,
    first_name,
    last_name,
    -- Stored as "1978-06-09 00:00:00": keep the date only. Missing for ~0.1% of players.
    date_of_birth::timestamp::date as date_of_birth,
    country_of_citizenship as nationality,
    country_of_birth,
    -- "Missing" is an empty value written as text.
    nullif(position, 'Missing') as position,
    sub_position,
    foot,
    height_in_cm::integer as height_cm,
    current_club_id::integer as current_tm_club_id,
    current_club_name,
    last_season::integer as last_season,
    market_value_in_eur::double as market_value_eur,
    highest_market_value_in_eur::double as highest_market_value_eur,
    contract_expiration_date::timestamp::date as contract_expiration_date,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_players') }}
