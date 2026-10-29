-- Every played match of couk and Transfermarkt must be linked to a football-data.org
-- fixture in map_match. Returns the unlinked matches: e.g. a date that differs
-- between sources (a postponed match recorded on the original date) would show up here.

select 'couk' as source, couk_match_ref as match_ref
from {{ ref('stg_couk__matches') }}
where couk_match_ref not in (
    select couk_match_ref from {{ ref('map_match') }} where couk_match_ref is not null
)

union all

select 'transfermarkt', tm_game_id::varchar
from {{ ref('stg_transfermarkt__games') }}
where tm_game_id not in (
    select tm_game_id from {{ ref('map_match') }} where tm_game_id is not null
)
