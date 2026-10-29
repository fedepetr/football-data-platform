-- One row per player, competition and season with goals and assists from
-- football-data.org /scorers (players with at least one goal). It is the only
-- source of scoring for 2026/27, which Transfermarkt does not have.
--
-- assists and penalties: NULL in the source is read as 0. Checked against
-- Transfermarkt on 2024/25-2025/26: when football-data.org has no assists,
-- Transfermarkt counts 0 in 88% of cases. assists_reported keeps the difference
-- between a 0 given by the source and a 0 inferred here.

select
    map_player.player_key,
    competition.competition_key,
    scorers.season as season_key,
    map_team.team_key,
    scorers.played_matches,
    scorers.goals,
    coalesce(scorers.assists, 0) as assists,
    scorers.assists is not null as assists_reported,
    coalesce(scorers.penalties, 0) as penalty_goals
from {{ ref('stg_fdorg__scorers') }} as scorers
join {{ ref('map_player') }} as map_player using (fdorg_person_id)
join {{ ref('dim_competition') }} as competition
    on competition.fdorg_code = scorers.competition_code
left join {{ ref('map_team') }} as map_team using (fdorg_team_id)
