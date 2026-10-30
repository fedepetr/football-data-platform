-- One row per football-data.org player with its Transfermarkt match.
-- Accepted matches: band "auto" (score above 92, no failed check) or "manual"
-- (reviewed correction). Every other player is kept without a Transfermarkt id,
-- so football-data.org facts (e.g. season scoring) always find a player_key.

with matches as (

    select * from {{ source('matching', 'player_matches') }}

)

select
    -- Project player key: the Transfermarkt id when matched (several football-data.org
    -- ids of one person share it), otherwise the football-data.org id.
    case
        when band in ('auto', 'manual') then 'tm_' || tm_player_id
        else 'fdorg_' || fdorg_person_id
    end as player_key,
    case when band in ('auto', 'manual') then tm_player_id end as tm_player_id,
    fdorg_person_id,
    case when band in ('auto', 'manual') then match_score end as match_score,
    case when band in ('auto', 'manual') then match_method else 'unmatched' end as match_method
from matches
