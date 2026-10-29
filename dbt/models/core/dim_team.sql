-- One row per team of the project, from the reviewed team map seed.

select distinct
    team_key,
    team_name,
    country
from {{ ref('team_map') }}
