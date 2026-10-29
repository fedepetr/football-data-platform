-- One row per season loaded, with its dates taken from the football-data.org fixtures
-- (the declared season start of the API is not reliable, see DEVELOPMENT.md).

with seasons as (

    select
        season as season_key,
        min(match_date_utc) as first_match_date,
        max(match_date_utc) as last_match_date
    from {{ ref('stg_fdorg__matches') }}
    group by season

),

-- The season in progress: the latest one that has already started.
current_season as (

    select season_key
    from seasons
    where first_match_date <= current_date
    order by first_match_date desc
    limit 1

)

select
    seasons.season_key,
    -- "2425" -> "2024/25"
    '20' || left(seasons.season_key, 2) || '/' || right(seasons.season_key, 2) as season_label,
    ('20' || left(seasons.season_key, 2))::integer as start_year,
    seasons.first_match_date,
    seasons.last_match_date,
    seasons.season_key = current_season.season_key as is_current
from seasons
cross join current_season
