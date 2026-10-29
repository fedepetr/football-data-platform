-- One row per match from football-data.co.uk, with proper types and readable names.
-- Keeps results and match statistics; bookmaker odds columns stay in raw only.

with source as (

    select * from {{ source('raw', 'couk_matches') }}
    -- Padding lines made only of commas would have no competition code.
    where "Div" is not null

),

typed as (

    select
        "Div" as competition_code,
        _season as season,
        -- Fails loudly if the site changes the date format (fail fast, no silent NULLs).
        strptime("Date", '%d/%m/%Y')::date as match_date,
        -- Kick-off is in UK local time (verified against football-data.org UTC times).
        "Time"::time as kickoff_time_uk,
        "HomeTeam" as home_team_name,
        "AwayTeam" as away_team_name,

        -- Full time and half time
        "FTHG"::integer as home_goals,
        "FTAG"::integer as away_goals,
        "FTR" as result,
        "HTHG"::integer as home_goals_ht,
        "HTAG"::integer as away_goals_ht,
        "HTR" as result_ht,

        -- Match statistics ("AS" is quoted: it is also an SQL keyword)
        "HS"::integer as home_shots,
        "AS"::integer as away_shots,
        "HST"::integer as home_shots_on_target,
        "AST"::integer as away_shots_on_target,
        "HF"::integer as home_fouls,
        "AF"::integer as away_fouls,
        "HC"::integer as home_corners,
        "AC"::integer as away_corners,
        "HY"::integer as home_yellow_cards,
        "AY"::integer as away_yellow_cards,
        "HR"::integer as home_red_cards,
        "AR"::integer as away_red_cards,

        -- Only in 2026/27 (NULL before) and undocumented in notes.txt.
        "HxG"::double as home_xg,
        "AxG"::double as away_xg,
        -- Only in Premier League files (NULL for Serie A).
        "Referee" as referee,

        _source_file,
        _loaded_at

    from source

)

select
    -- The CSV has no match id: build a readable, unique one,
    -- e.g. I1_2026-08-22_Udinese_Como (a fixture is played once per date).
    concat_ws('_', competition_code, strftime(match_date, '%Y-%m-%d'), home_team_name, away_team_name)
        as couk_match_ref,
    *
from typed
