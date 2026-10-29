-- One row per player in scope: football-data.org players (matched or not) and
-- Transfermarkt players with appearances in the games in scope.
-- Attributes come from Transfermarkt when the player is matched (one consistent
-- source for name, birth date, nationality and position), otherwise from the
-- only source that has the player.

with matched as (

    -- Several football-data.org ids of one person share the same player_key.
    select distinct player_key, tm_player_id
    from {{ ref('map_player') }}
    where tm_player_id is not null

),

fdorg_only as (

    select player_key, fdorg_person_id
    from {{ ref('map_player') }}
    where tm_player_id is null

),

-- Latest football-data.org record of each person (squads and scorers).
fdorg_players as (

    select
        fdorg_person_id,
        arg_max(player_name, season) as player_name,
        arg_max(date_of_birth, season) as date_of_birth,
        arg_max(nationality, season) as nationality,
        arg_max(position_group, season) as position_group
    from {{ ref('stg_fdorg__squad_players') }}
    group by fdorg_person_id

),

fdorg_scorers as (

    -- 19 scorers are in no squad: their position comes from `section` (e.g. Offence).
    select
        fdorg_person_id,
        arg_max(player_name, season) as player_name,
        arg_max(date_of_birth, season) as date_of_birth,
        arg_max(nationality, season) as nationality,
        arg_max(
            case section
                when 'Goalkeeper' then 'Goalkeeper'
                when 'Defence' then 'Defender'
                when 'Midfield' then 'Midfield'
                when 'Offence' then 'Attack'
            end,
            season
        ) as position_group
    from {{ ref('stg_fdorg__scorers') }}
    group by fdorg_person_id

),

-- Transfermarkt players who appeared in scope but are in no football-data.org squad.
transfermarkt_only as (

    select distinct 'tm_' || tm_player_id as player_key, tm_player_id
    from {{ ref('stg_transfermarkt__appearances') }}
    where tm_player_id not in (select tm_player_id from matched)

),

from_transfermarkt as (

    select
        keys.player_key,
        players.player_name as full_name,
        players.date_of_birth as birth_date,
        players.nationality,
        players.position,
        'transfermarkt' as attributes_source
    from (
        select player_key, tm_player_id from matched
        union all
        select player_key, tm_player_id from transfermarkt_only
    ) as keys
    join {{ ref('stg_transfermarkt__players') }} as players using (tm_player_id)

),

from_fdorg as (

    select
        fdorg_only.player_key,
        coalesce(squad.player_name, scorer.player_name) as full_name,
        coalesce(squad.date_of_birth, scorer.date_of_birth) as birth_date,
        coalesce(squad.nationality, scorer.nationality) as nationality,
        coalesce(squad.position_group, scorer.position_group) as position,
        'fdorg' as attributes_source
    from fdorg_only
    left join fdorg_players as squad using (fdorg_person_id)
    left join fdorg_scorers as scorer using (fdorg_person_id)

)

select * from from_transfermarkt
union all
select * from from_fdorg
