-- One row per transfer that already happened (future ones are excluded in staging).
-- Only players in scope (dim_player), with their full transfer history.

select
    players.player_key,
    transfers.transfer_date,
    -- Transfermarkt's own season label, e.g. "25/26".
    transfers.transfer_season,
    transfers.from_tm_club_id,
    transfers.from_club_name,
    from_team.team_key as from_team_key,
    transfers.to_tm_club_id,
    transfers.to_club_name,
    to_team.team_key as to_team_key,
    -- 0 means either a free transfer or an unknown fee: the source does not tell.
    transfers.transfer_fee_eur,
    transfers.market_value_eur
from {{ ref('stg_transfermarkt__transfers') }} as transfers
join {{ ref('dim_player') }} as players
    on players.player_key = 'tm_' || transfers.tm_player_id
-- Project teams when the clubs are ours, NULL for every other club.
left join {{ ref('map_team') }} as from_team on from_team.tm_club_id = transfers.from_tm_club_id
left join {{ ref('map_team') }} as to_team on to_team.tm_club_id = transfers.to_tm_club_id
