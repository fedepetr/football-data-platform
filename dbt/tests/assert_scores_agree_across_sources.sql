-- The final score of a match must be the same in every source that has it.
-- Returns the matches where a source disagrees with football-data.org.

select
    map_match.match_key,
    fdorg.home_goals as fdorg_home_goals,
    fdorg.away_goals as fdorg_away_goals,
    couk.home_goals as couk_home_goals,
    couk.away_goals as couk_away_goals,
    transfermarkt.home_goals as tm_home_goals,
    transfermarkt.away_goals as tm_away_goals
from {{ ref('map_match') }} as map_match
join {{ ref('stg_fdorg__matches') }} as fdorg using (fdorg_match_id)
left join {{ ref('stg_couk__matches') }} as couk using (couk_match_ref)
left join {{ ref('stg_transfermarkt__games') }} as transfermarkt using (tm_game_id)
where
    (couk.couk_match_ref is not null
        and (fdorg.home_goals is distinct from couk.home_goals
            or fdorg.away_goals is distinct from couk.away_goals))
    or (transfermarkt.tm_game_id is not null
        and (fdorg.home_goals is distinct from transfermarkt.home_goals
            or fdorg.away_goals is distinct from transfermarkt.away_goals))
