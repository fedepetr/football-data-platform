-- One row per team and season from football-data.org /teams.
-- Squads are unnested separately in stg_fdorg__squad_players.

with source as (

    select * from {{ source('raw', 'fdorg_teams') }}

),

teams as (

    select
        _season,
        payload->>'$.competition.code' as competition_code,
        _source_file,
        _loaded_at,
        unnest(cast(payload->'$.teams' as json[])) as team
    from source

)

select
    (team->>'$.id')::integer as fdorg_team_id,
    _season as season,
    competition_code,
    team->>'$.name' as team_name,
    team->>'$.shortName' as short_name,
    team->>'$.tla' as tla,
    team->>'$.area.name' as country,
    team->>'$.venue' as venue,
    (team->>'$.founded')::integer as founded,
    _source_file,
    _loaded_at
from teams
