-- Example queries on data/fdp.duckdb, in the order they tell the project's story:
-- the problem, the result, the hard cases, the proof, the business value.
--
-- Run them in the DuckDB UI with the database attached as `fdp` (read-only):
--   uv run python -c "import duckdb, time; con = duckdb.connect();
--     con.execute(\"ATTACH 'data/fdp.duckdb' AS fdp (READ_ONLY)\");
--     con.execute('INSTALL ui; LOAD ui; CALL start_ui();'); [time.sleep(1) for _ in iter(int, 1)]"
-- Without the UI, remove the `fdp.` prefix and connect to data/fdp.duckdb directly.


-- 1. One person, three sources.
-- Each source has its own ids; map_player links them to one project key.
-- Anguissa: matched by hand (score 72.7, "André Zambo" vs "Frank" Anguissa).
-- Stulic: two football-data.org ids for one person, both on the same player_key.
select
    p.full_name,
    mp.player_key,
    mp.tm_player_id,
    mp.fdorg_person_id,
    mp.match_method,
    mp.match_score
from fdp.core.map_player as mp
join fdp.core.dim_player as p using (player_key)
where p.full_name in ('Lautaro Martínez', 'Frank Anguissa', 'Nikola Stulic');


-- 2. How well the player reconciliation worked.
-- Bands from the brief's thresholds (auto > 92, review 80-92, rejected < 80),
-- after the manual review. The window function sum(...) over () gives the total
-- of all rows, so the share is computed in the same query.
select
    band,
    count(*) as players,
    round(100.0 * count(*) / sum(count(*)) over (), 1) as pct
from fdp.matching.player_matches
group by band
order by players desc;


-- 3. The hard cases, fixed by the review.
-- Names that no string similarity would match: the sources use different parts
-- of the full name. The reverse check (same birth date AND same team) flagged
-- them; a person confirmed them, and every decision keeps its note.
select
    fdorg_player_name,
    tm_player_name,
    match_score,
    flags as note
from fdp.matching.player_matches
where band = 'manual'
order by match_score
limit 6;


-- 4. The proof that matches are right, not only that they exist.
-- Season goals of each matched player in two independent sources: football-data.org
-- (scorers) and Transfermarkt (sum of appearances), joined through player_key.
-- A wrong match would break the numbers: 1124 equal, 13 off by one, none more.
with transfermarkt as (
    select player_key, season_key, competition_key, sum(goals) as tm_goals
    from fdp.core.fct_player_appearance
    group by all
)
select
    abs(scoring.goals - transfermarkt.tm_goals) as goal_difference,
    count(*) as player_seasons
from fdp.core.fct_player_season_scoring as scoring
join transfermarkt using (player_key, season_key, competition_key)
group by 1
order by 1;


-- 5. Current Serie A table with expected goals.
-- fct_team_match_stats has one row per team and match, so a table is a group by.
-- goals_minus_xg > 0: the team scores more than its chances suggest (finishing
-- above expectations, usually hard to sustain).
select
    t.team_name,
    count(*) as played,
    sum(s.points) as pts,
    sum(s.goals_for) as gf,
    sum(s.goals_against) as ga,
    round(sum(s.xg_for), 1) as xg_for,
    round(sum(s.goals_for) - sum(s.xg_for), 1) as goals_minus_xg
from fdp.core.fct_team_match_stats as s
join fdp.core.dim_team as t using (team_key)
where s.season_key = '2627' and s.competition_key = 'serie_a'
group by t.team_name
order by pts desc, gf - ga desc;


-- 6. Biggest market value risers between the last two completed seasons.
-- Self-join: the same mart twice (a = 2024/25, b = 2025/26) puts both seasons of a
-- player on one row. Values are end-of-season Transfermarkt valuations.
with values_by_season as (
    select player_key, season_key, market_value_eur
    from fdp.marts.mart_player_profile
    where season_key in ('2425', '2526')
)
select
    p.full_name,
    a.market_value_eur / 1e6 as value_2425_m,
    b.market_value_eur / 1e6 as value_2526_m,
    (b.market_value_eur - a.market_value_eur) / 1e6 as delta_m
from values_by_season as a
join values_by_season as b
    on b.player_key = a.player_key and a.season_key = '2425' and b.season_key = '2526'
join fdp.core.dim_player as p on p.player_key = a.player_key
order by delta_m desc
limit 10;


-- 7. The five players most similar to Lautaro Martínez in 2025/26.
-- Exact k-nearest neighbours on per-90 statistics (goals, assists, cards, share of
-- minutes) standardised within season and position: similar output, not style.
-- comparison_scope: 'all_competitions' searches Serie A and Premier League
-- together (cross-league scouting); 'same_competition' only his own league.
select
    comparison_scope,
    similarity_rank,
    similar_player_name,
    similar_competition_key,
    distance,
    similarity
from fdp.marts.mart_similar_players
where player_name = 'Lautaro Martínez' and season_key = '2526'
order by comparison_scope, similarity_rank;


-- 8. The five most similar pairs of attackers in a league and season.
-- Every pair appears twice (A -> B and B -> A, same distance): least/greatest put
-- the two keys in a fixed order and qualify keeps one row per pair.
-- The closest pairs are average players: near the mean many profiles look alike,
-- while a star is far from everyone. Similar does not mean good.
select
    season_key,
    least(player_name, similar_player_name) as player_a,
    greatest(player_name, similar_player_name) as player_b,
    distance,
    similarity
from fdp.marts.mart_similar_players
where position = 'Attack'
    and season_key = '2526'
    and comparison_scope = 'same_competition'
    and competition_key = 'serie_a'
qualify row_number() over (
    partition by season_key,
        least(player_key, similar_player_key),
        greatest(player_key, similar_player_key)
    order by distance
) = 1
order by distance
limit 5;
