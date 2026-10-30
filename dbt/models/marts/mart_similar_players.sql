-- The most similar players of each player and season, by statistics per 90 minutes.
--
-- 1. Players with at least `similar_players_min_minutes` in a completed season
--    (Transfermarkt appearances: the only source with minutes for everyone).
-- 2. Features per 90 minutes: goals, assists, yellow and red cards, plus the share
--    of the season's minutes played (how much of a starter the player is).
-- 3. Each feature becomes a z-score within the same season and position: how many
--    standard deviations above or below the average. Goals and cards then weigh
--    the same, and an attacker is compared with attackers only.
-- 4. Similarity = Euclidean distance between the z-score vectors; the
--    `similar_players_top_n` closest players are kept, in two scopes:
--    - all_competitions: Serie A and Premier League together (cross-league scouting);
--    - same_competition: only players of the player's own league.
--    z-scores are computed on both leagues together in both scopes, so distances
--    are on the same scale; the scope only changes who the candidates are.
-- With five simple features, "similar" means a similar output (goals, assists,
-- discipline, playing time), not a similar playing style: event data would be needed.

-- Main league of each player-season: the one with most minutes (a player who
-- moved from the Premier League to Serie A in January belongs to one of them).
with main_competition as (

    select player_key, season_key, arg_max(competition_key, minutes) as competition_key
    from (
        select player_key, season_key, competition_key, sum(minutes_played) as minutes
        from {{ ref('fct_player_appearance') }}
        group by all
    )
    group by all

),

player_seasons as (

    select
        appearances.player_key,
        appearances.season_key,
        players.position,
        sum(appearances.minutes_played) as minutes,
        sum(appearances.goals) as goals,
        sum(appearances.assists) as assists,
        sum(appearances.yellow_cards) as yellow_cards,
        sum(appearances.red_cards) as red_cards
    from {{ ref('fct_player_appearance') }} as appearances
    join {{ ref('dim_player') }} as players using (player_key)
    where players.position is not null
    group by all
    having sum(appearances.minutes_played) >= {{ var('similar_players_min_minutes') }}

),

per_90 as (

    select
        player_seasons.player_key,
        player_seasons.season_key,
        player_seasons.position,
        main_competition.competition_key,
        minutes,
        goals * 90.0 / minutes as goals_per_90,
        assists * 90.0 / minutes as assists_per_90,
        yellow_cards * 90.0 / minutes as yellow_cards_per_90,
        red_cards * 90.0 / minutes as red_cards_per_90,
        -- 38 matches of 90 minutes in a 20-team league season.
        minutes / (38 * 90.0) as minutes_share
    from player_seasons
    join main_competition using (player_key, season_key)

),

-- z-score = (value - average) / standard deviation, within season and position.
-- nullif avoids a division by zero when every player has the same value.
z_scores as (

    select
        player_key,
        season_key,
        position,
        competition_key,
        {% for feature in ['goals_per_90', 'assists_per_90', 'yellow_cards_per_90', 'red_cards_per_90', 'minutes_share'] %}
        coalesce(
            ({{ feature }} - avg({{ feature }}) over peers)
                / nullif(stddev_pop({{ feature }}) over peers, 0),
            0
        ) as z_{{ feature }}{{ "," if not loop.last }}
        {% endfor %}
    from per_90
    window peers as (partition by season_key, position)

),

-- Every pair of different players with the same season and position.
distances as (

    select
        player.player_key,
        player.season_key,
        player.position,
        player.competition_key,
        other.player_key as similar_player_key,
        other.competition_key as similar_competition_key,
        sqrt(
            {% for feature in ['goals_per_90', 'assists_per_90', 'yellow_cards_per_90', 'red_cards_per_90', 'minutes_share'] %}
            power(player.z_{{ feature }} - other.z_{{ feature }}, 2){{ " +" if not loop.last }}
            {% endfor %}
        ) as distance
    from z_scores as player
    join z_scores as other
        on other.season_key = player.season_key
        and other.position = player.position
        and other.player_key <> player.player_key

),

-- The same pairs in the two scopes: all of them, and only same-league ones.
scoped as (

    select *, 'all_competitions' as comparison_scope
    from distances

    union all

    select *, 'same_competition' as comparison_scope
    from distances
    where similar_competition_key = competition_key

),

ranked as (

    select
        *,
        row_number() over (
            partition by player_key, season_key, comparison_scope
            order by distance, similar_player_key
        ) as similarity_rank
    from scoped

)

select
    ranked.player_key,
    players.full_name as player_name,
    ranked.season_key,
    ranked.position,
    ranked.competition_key,
    ranked.comparison_scope,
    ranked.similarity_rank,
    ranked.similar_player_key,
    similar_player.full_name as similar_player_name,
    ranked.similar_competition_key,
    round(ranked.distance, 3) as distance,
    -- 100 for identical statistics, lower as the distance grows.
    round(100 / (1 + ranked.distance), 1) as similarity
from ranked
join {{ ref('dim_player') }} as players on players.player_key = ranked.player_key
join {{ ref('dim_player') }} as similar_player on similar_player.player_key = ranked.similar_player_key
where ranked.similarity_rank <= {{ var('similar_players_top_n') }}
