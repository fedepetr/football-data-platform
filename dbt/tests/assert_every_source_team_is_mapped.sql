-- Every team found in the staging data must be in the team map.
-- Returns the unmapped teams: the test fails (and lists them) when, for example,
-- a newly promoted team appears next season and the seed was not updated.
-- Transfermarkt is checked on its games only: it has no 2026/27 data, so a
-- promoted team without a club (Coventry City) is expected and not an error.

with source_teams as (

    select 'couk' as source, home_team_name as team_ref from {{ ref('stg_couk__matches') }}
    union
    select 'couk', away_team_name from {{ ref('stg_couk__matches') }}
    union
    select 'fdorg', home_team_id::varchar from {{ ref('stg_fdorg__matches') }}
    union
    select 'fdorg', away_team_id::varchar from {{ ref('stg_fdorg__matches') }}
    union
    select 'transfermarkt', home_tm_club_id::varchar from {{ ref('stg_transfermarkt__games') }}
    union
    select 'transfermarkt', away_tm_club_id::varchar from {{ ref('stg_transfermarkt__games') }}

)

select source_teams.*
from source_teams
left join {{ ref('team_map') }} as team_map
    on team_map.source = source_teams.source
    and team_map.source_team_ref = source_teams.team_ref
where team_map.team_key is null
