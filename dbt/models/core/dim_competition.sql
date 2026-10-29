-- One row per competition in scope, with its code in each source (from the seed).

select
    competition_key,
    competition_name,
    country,
    fdorg_code,
    couk_code,
    tm_competition_id
from {{ ref('competition_map') }}
