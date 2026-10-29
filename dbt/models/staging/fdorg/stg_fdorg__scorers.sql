-- One row per scorer, competition and season from football-data.org /scorers.
-- Only players with at least one goal. assists/penalties are often NULL: whether
-- NULL means 0 is decided in core, where season scoring is built.

with source as (

    select * from {{ source('raw', 'fdorg_scorers') }}

),

scorers as (

    select
        _season,
        payload->>'$.competition.code' as competition_code,
        _source_file,
        _loaded_at,
        unnest(cast(payload->'$.scorers' as json[])) as scorer
    from source

)

select
    (scorer->>'$.player.id')::integer as fdorg_person_id,
    _season as season,
    competition_code,
    -- Use `name`: `firstName` is empty and `lastName` holds the full name.
    scorer->>'$.player.name' as player_name,
    (scorer->>'$.player.dateOfBirth')::date as date_of_birth,
    scorer->>'$.player.nationality' as nationality,
    -- `position` is often NULL here; `section` (e.g. Offence) is filled instead.
    scorer->>'$.player.section' as section,
    (scorer->>'$.team.id')::integer as fdorg_team_id,
    scorer->>'$.team.name' as team_name,
    (scorer->>'$.playedMatches')::integer as played_matches,
    (scorer->>'$.goals')::integer as goals,
    (scorer->>'$.assists')::integer as assists,
    (scorer->>'$.penalties')::integer as penalties,
    _source_file,
    _loaded_at
from scorers
