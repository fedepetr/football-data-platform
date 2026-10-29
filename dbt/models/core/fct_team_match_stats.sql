-- One row per match and team: the same match seen from each side, with the
-- statistics of football-data.co.uk (shots, corners, fouls, cards, xG).
-- Only played matches (couk lists played matches only): two rows per match.

with matches as (

    select
        map_match.match_key,
        map_match.season as season_key,
        map_match.match_date,
        map_match.home_team_key,
        map_match.away_team_key,
        competition.competition_key,
        couk.*
    from {{ ref('map_match') }} as map_match
    join {{ ref('stg_couk__matches') }} as couk using (couk_match_ref)
    join {{ ref('dim_competition') }} as competition
        on competition.couk_code = couk.competition_code

),

-- The home side: "for" columns are the home team's, "against" the away team's.
home as (

    select
        match_key,
        home_team_key as team_key,
        away_team_key as opponent_team_key,
        true as is_home,
        competition_key,
        season_key,
        match_date,
        home_goals as goals_for,
        away_goals as goals_against,
        home_shots as shots,
        home_shots_on_target as shots_on_target,
        home_corners as corners,
        home_fouls as fouls,
        home_yellow_cards as yellow_cards,
        home_red_cards as red_cards,
        home_xg as xg_for,
        away_xg as xg_against
    from matches

),

-- The away side: the same columns with home and away swapped.
away as (

    select
        match_key,
        away_team_key as team_key,
        home_team_key as opponent_team_key,
        false as is_home,
        competition_key,
        season_key,
        match_date,
        away_goals as goals_for,
        home_goals as goals_against,
        away_shots as shots,
        away_shots_on_target as shots_on_target,
        away_corners as corners,
        away_fouls as fouls,
        away_yellow_cards as yellow_cards,
        away_red_cards as red_cards,
        away_xg as xg_for,
        home_xg as xg_against
    from matches

),

both_sides as (

    select * from home
    union all
    select * from away

)

select
    *,
    -- Result and league points from the team's point of view.
    case
        when goals_for > goals_against then 'W'
        when goals_for = goals_against then 'D'
        else 'L'
    end as result,
    case
        when goals_for > goals_against then 3
        when goals_for = goals_against then 1
        else 0
    end as points
from both_sides
