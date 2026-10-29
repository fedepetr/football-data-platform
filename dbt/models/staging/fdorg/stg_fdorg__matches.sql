-- One row per match from football-data.org: the raw table holds one row per API
-- response (competition and season), with every match inside the JSON payload.
-- Includes future fixtures (status SCHEDULED/TIMED) with NULL scores.

with source as (

    select * from {{ source('raw', 'fdorg_matches') }}

),

-- Unnest the JSON list: one row per file becomes one row per match.
matches as (

    select
        _season,
        _source_file,
        _loaded_at,
        unnest(cast(payload->'$.matches' as json[])) as match
    from source

),

typed as (

    select
        (match->>'$.id')::integer as fdorg_match_id,
        match->>'$.competition.code' as competition_code,
        _season as season,
        (match->>'$.utcDate')::timestamptz as kickoff_utc,
        -- Date part of the UTC timestamp, read from the text: independent of the
        -- session time zone. Used to join with football-data.co.uk by date.
        left(match->>'$.utcDate', 10)::date as match_date_utc,
        match->>'$.status' as status,
        (match->>'$.matchday')::integer as matchday,
        match->>'$.stage' as stage,

        (match->>'$.homeTeam.id')::integer as home_team_id,
        match->>'$.homeTeam.name' as home_team_name,
        (match->>'$.awayTeam.id')::integer as away_team_id,
        match->>'$.awayTeam.name' as away_team_name,

        (match->>'$.score.fullTime.home')::integer as home_goals,
        (match->>'$.score.fullTime.away')::integer as away_goals,
        (match->>'$.score.halfTime.home')::integer as home_goals_ht,
        (match->>'$.score.halfTime.away')::integer as away_goals_ht,
        -- Same H/D/A convention as football-data.co.uk; NULL if not played yet.
        case match->>'$.score.winner'
            when 'HOME_TEAM' then 'H'
            when 'DRAW' then 'D'
            when 'AWAY_TEAM' then 'A'
        end as result,

        -- A few matches list more than one official: keep the main referee.
        list_filter(
            cast(match->'$.referees' as json[]),
            lambda referee: referee->>'$.type' = 'REFEREE'
        )[1]->>'$.name' as referee,

        (match->>'$.lastUpdated')::timestamptz as last_updated_utc,
        _source_file,
        _loaded_at

    from matches

)

select * from typed
