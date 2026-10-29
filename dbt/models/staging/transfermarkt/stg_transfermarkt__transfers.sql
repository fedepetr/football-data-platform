-- One row per transfer that has already happened, every player.
-- Future transfers (announced, ~475 rows) are excluded: they did not happen
-- yet and would distort "where a player plays"; they remain in raw.

select
    player_id::integer as tm_player_id,
    transfer_date::date as transfer_date,
    transfer_season,
    from_club_id::integer as from_tm_club_id,
    from_club_name,
    to_club_id::integer as to_tm_club_id,
    to_club_name,
    -- 0 means either a free transfer or an unknown fee: the source does not tell.
    transfer_fee::double as transfer_fee_eur,
    market_value_in_eur::double as market_value_eur,
    player_name,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_transfers') }}
where transfer_date::date <= current_date
