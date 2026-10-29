-- One row per match, combining the three sources through map_match.
-- football-data.org is the base (every fixture, status, UTC kick-off, score,
-- referee); couk adds xG (2026/27 only), Transfermarkt adds attendance and stadium.
-- Scores agree across the three sources on every played match (tested).

with fdorg as (

    select * from {{ ref('stg_fdorg__matches') }}

),

couk as (

    select * from {{ ref('stg_couk__matches') }}

),

transfermarkt as (

    select * from {{ ref('stg_transfermarkt__games') }}

)

select
    map_match.match_key,
    competition.competition_key,
    map_match.season as season_key,
    map_match.match_date,
    fdorg.kickoff_utc,
    fdorg.status,
    fdorg.matchday,
    map_match.home_team_key,
    map_match.away_team_key,

    -- Score only for finished matches: a match in play at load time has a
    -- partial score (and football-data.org already fills its winner).
    case when fdorg.status = 'FINISHED' then fdorg.home_goals end as home_goals,
    case when fdorg.status = 'FINISHED' then fdorg.away_goals end as away_goals,
    case when fdorg.status = 'FINISHED' then fdorg.result end as result,
    case when fdorg.status = 'FINISHED' then fdorg.home_goals_ht end as home_goals_ht,
    case when fdorg.status = 'FINISHED' then fdorg.away_goals_ht end as away_goals_ht,

    -- Expected goals: only football-data.co.uk, only from 2026/27.
    couk.home_xg,
    couk.away_xg,

    -- football-data.org has full names for both leagues; Transfermarkt fills the
    -- rare gaps (couk has initials, e.g. "R Jones", and no Serie A referees).
    coalesce(fdorg.referee, transfermarkt.referee, couk.referee) as referee,
    transfermarkt.attendance,
    transfermarkt.stadium,

    -- Which sources describe this match (useful to explain NULLs).
    couk.couk_match_ref is not null as in_couk,
    transfermarkt.tm_game_id is not null as in_transfermarkt

from {{ ref('map_match') }} as map_match
join fdorg using (fdorg_match_id)
join {{ ref('dim_competition') }} as competition
    on competition.fdorg_code = fdorg.competition_code
left join couk using (couk_match_ref)
left join transfermarkt using (tm_game_id)
