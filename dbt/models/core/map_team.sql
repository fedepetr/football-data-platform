-- One row per team with its reference in each source (the brief's map_team):
-- the seed has one row per team and source, pivoted here into one column per source.
-- A missing reference stays NULL (e.g. Coventry City has no Transfermarkt club).

select
    team_key,
    max(case when source = 'transfermarkt' then source_team_ref::integer end) as tm_club_id,
    max(case when source = 'fdorg' then source_team_ref::integer end) as fdorg_team_id,
    max(case when source = 'couk' then source_team_ref end) as couk_team_name
from {{ ref('team_map') }}
group by team_key
