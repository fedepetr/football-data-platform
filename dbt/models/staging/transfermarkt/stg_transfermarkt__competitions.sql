-- One row per competition from Transfermarkt (all 65, not only those in scope).

select
    competition_id as tm_competition_id,
    name as competition_name,
    type as competition_type,
    sub_type as competition_sub_type,
    country_name,
    confederation,
    domestic_league_code,
    _source_file,
    _loaded_at
from {{ source('raw', 'transfermarkt_competitions') }}
