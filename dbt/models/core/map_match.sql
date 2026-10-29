-- One row per match with its reference in each source (the brief's map_match).
-- Matches are joined on date + home team + away team, after the team
-- reconciliation (map_team). football-data.org is the hub: it lists every
-- fixture, including future ones, which have no couk / Transfermarkt match yet.
-- Dates are safe to compare: couk is UK local time, fdorg is UTC (date taken from
-- the UTC text), and no kickoff is late enough to change the date (checked).

with fdorg as (

    select
        matches.fdorg_match_id,
        matches.season,
        matches.match_date_utc as match_date,
        home.team_key as home_team_key,
        away.team_key as away_team_key
    from {{ ref('stg_fdorg__matches') }} as matches
    join {{ ref('map_team') }} as home on home.fdorg_team_id = matches.home_team_id
    join {{ ref('map_team') }} as away on away.fdorg_team_id = matches.away_team_id

),

couk as (

    select
        matches.couk_match_ref,
        matches.match_date,
        home.team_key as home_team_key,
        away.team_key as away_team_key
    from {{ ref('stg_couk__matches') }} as matches
    join {{ ref('map_team') }} as home on home.couk_team_name = matches.home_team_name
    join {{ ref('map_team') }} as away on away.couk_team_name = matches.away_team_name

),

transfermarkt as (

    select
        games.tm_game_id,
        games.match_date,
        home.team_key as home_team_key,
        away.team_key as away_team_key
    from {{ ref('stg_transfermarkt__games') }} as games
    join {{ ref('map_team') }} as home on home.tm_club_id = games.home_tm_club_id
    join {{ ref('map_team') }} as away on away.tm_club_id = games.away_tm_club_id

)

select
    -- Readable project key: a fixture (home vs away) is played once per season,
    -- e.g. 2627_genoa_juventus.
    concat_ws('_', fdorg.season, fdorg.home_team_key, fdorg.away_team_key) as match_key,
    transfermarkt.tm_game_id,
    fdorg.fdorg_match_id,
    couk.couk_match_ref,
    fdorg.season,
    fdorg.match_date,
    fdorg.home_team_key,
    fdorg.away_team_key
from fdorg
left join couk
    on couk.match_date = fdorg.match_date
    and couk.home_team_key = fdorg.home_team_key
    and couk.away_team_key = fdorg.away_team_key
left join transfermarkt
    on transfermarkt.match_date = fdorg.match_date
    and transfermarkt.home_team_key = fdorg.home_team_key
    and transfermarkt.away_team_key = fdorg.away_team_key
