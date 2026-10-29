-- Cross-check of the player reconciliation: for a matched player, the goals of a
-- season in football-data.org (scorers) and Transfermarkt (sum of appearances)
-- must agree within one goal (sources sometimes credit a goal differently).
-- A larger gap points to a wrong player match. Returns those player-seasons.
-- On 2026-10-29: 1124 of 1137 equal, 13 off by one, none off by more.

with transfermarkt as (

    select player_key, season_key, competition_key, sum(goals) as goals
    from {{ ref('fct_player_appearance') }}
    group by all

)

select
    scoring.player_key,
    scoring.season_key,
    scoring.competition_key,
    scoring.goals as fdorg_goals,
    transfermarkt.goals as tm_goals
from {{ ref('fct_player_season_scoring') }} as scoring
join transfermarkt using (player_key, season_key, competition_key)
where abs(scoring.goals - transfermarkt.goals) > 1
