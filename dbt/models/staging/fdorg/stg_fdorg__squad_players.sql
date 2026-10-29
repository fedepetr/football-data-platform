-- One row per player and season from the squads in football-data.org /teams.
-- A player appears in one squad per season (checked on the data), so
-- (season, fdorg_person_id) is unique. Main input of the player reconciliation.

with source as (

    select * from {{ source('raw', 'fdorg_teams') }}

),

-- Two unnests: first one row per team, then one row per squad player.
teams as (

    select
        _season,
        payload->>'$.competition.code' as competition_code,
        _source_file,
        _loaded_at,
        unnest(cast(payload->'$.teams' as json[])) as team
    from source

),

players as (

    select
        _season,
        competition_code,
        (team->>'$.id')::integer as fdorg_team_id,
        _source_file,
        _loaded_at,
        unnest(cast(team->'$.squad' as json[])) as player
    from teams

),

typed as (

    select
        (player->>'$.id')::integer as fdorg_person_id,
        _season as season,
        competition_code,
        fdorg_team_id,
        player->>'$.name' as player_name,
        (player->>'$.dateOfBirth')::date as date_of_birth,
        player->>'$.nationality' as nationality,
        -- A few players carry the text "null" instead of a JSON null.
        nullif(player->>'$.position', 'null') as position,
        _source_file,
        _loaded_at
    from players

)

select
    fdorg_person_id,
    season,
    competition_code,
    fdorg_team_id,
    player_name,
    date_of_birth,
    nationality,
    position,
    -- Mostly generic roles (Defence, Offence...), some detailed (Centre-Back...):
    -- grouped into the Transfermarkt vocabulary to compare the sources.
    -- Unknown values are kept as they are, so the accepted_values test flags them.
    case
        when position = 'Goalkeeper' then 'Goalkeeper'
        when position in ('Defence', 'Centre-Back', 'Left-Back', 'Right-Back') then 'Defender'
        when position in (
            'Midfield', 'Central Midfield', 'Defensive Midfield', 'Attacking Midfield',
            'Left Midfield', 'Right Midfield'
        ) then 'Midfield'
        when position in ('Offence', 'Centre-Forward', 'Left Winger', 'Right Winger') then 'Attack'
        else position
    end as position_group,
    _source_file,
    _loaded_at
from typed
