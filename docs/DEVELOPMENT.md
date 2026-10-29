# Development Guide

Working document for building the Football Data Platform. It tracks the plan,
the conventions, what we learned about the data and the decisions we made.
Updated at the end of every phase.

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Project skeleton, tooling, conventions | done |
| 1 | Ingestion of the three sources into `raw` | done (PR pending) |
| 2 | dbt staging and core, player reconciliation | in progress (staging, teams, matches, players done; core facts left) |
| 3 | Marts, data quality tests, Dagster schedule, CI | todo |
| 4 | README, architecture diagram, demo | todo |

## Workflow

- `main` is always green: it installs with `uv sync` and tests pass.
- One short-lived branch per unit of work, named `<type>/<topic>`
  (e.g. `feat/couk-ingestion`, `chore/project-skeleton`).
- Each branch is merged into `main` through a Pull Request (self-reviewed).
- Commits follow Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`,
  `test:`, `refactor:`. Small and focused.
- Before every commit:
  - `uv run ruff check . && uv run ruff format --check .`
  - `uv run pytest`
  - `git diff --cached` reviewed for secrets (tokens live only in `.env`).
- A phase is done when: its checklist is ticked, checks pass, this document is
  updated (findings, decisions, limitations) and the branch is merged.

## Target layout

```
football-data-platform/
  pyproject.toml / uv.lock / .python-version
  .env.example
  README.md
  docs/DEVELOPMENT.md
  config/pipeline.yaml               # seasons, competitions, source parameters
  data/raw/<source>/<season>/        # git-ignored, recreated by the pipeline
  data/fdp.duckdb                    # git-ignored, single database for all layers
  src/fdp/
    config.py                        # reads config/pipeline.yaml
    ingest/{common,couk,fdorg,transfermarkt}.py  # common: shared helpers (paths, checks, loads)
    matching/teams.py                # team name matching (ordered letters, checks)
    matching/team_map.py             # team map draft: names + fixture votes
    matching/players.py              # player rules from the brief (normalise, score, bands)
    matching/player_map.py           # player reconciliation run (candidates, checks, corrections)
    definitions.py                   # Dagster wiring only
  dbt/
    dbt_project.yml / profiles.yml   # profile points to ../data/fdp.duckdb
    macros/generate_schema_name.sql  # schemas named staging, core, marts
    seeds/team_map.csv               # reviewed team map, one row per team and source
    seeds/ models/{staging,core,marts}/
    tests/                           # singular dbt tests (e.g. every team is mapped)
    models/staging/<source>/         # _<source>__sources.yml, _<source>__models.yml, stg_*.sql
  tests/
  .github/workflows/
```

Folders are created when the phase that needs them starts, not before.

## Configuration

`config/pipeline.yaml` is the single place for pipeline parameters:

- `seasons` in scope and the `current_season` refreshed weekly;
- `competitions`, each with its code in every source
  (Serie A: `SA` in fdorg, `I1` in couk; Premier League: `PL`, `E0`);
- per-source settings, e.g. fdorg base URL, rate limit, timeout, retry policy
  and endpoints with their extra parameters.

Rules:

- Secrets never go in the YAML (it is committed): tokens stay in `.env`.
- Only values live in the YAML; logic (how to retry, how to load) stays in code.
- Season codes are quoted (`"2627"`): unquoted, YAML reads them as numbers.
  `tests/test_config.py` fails if that happens.
- Read with `yaml.safe_load`, once, when an ingestion module is imported.
  The path is relative, so commands run from the project root.

## Raw layer: load and refresh logic

Seasons are identified everywhere by a 4-digit code (`2425`, `2526`, `2627`).
Only `fdorg.py` converts it to the start year the API expects (`2627` -> `2026`).

Each pipeline run, for a source and a season:

1. **Extract** the full season from the source (one API call or CSV per
   competition and endpoint).
2. **Save** the response untouched to `data/raw/<source>/<season>/<file>`,
   overwriting the previous file with the same name.
3. **Load** the file into `raw.<source>_<entity>` in `data/fdp.duckdb`.

How a JSON file is loaded (`load_raw_json`):

- **One row per file.** The whole response goes into a `payload` JSON column,
  untouched (schema-on-read). Unnesting and typing happen in dbt staging, so a
  change in the source structure cannot break the raw load.
- **Technical columns:** `_source_file` (path of the file), `_season`,
  `_loaded_at` (load timestamp).
- **Replace by file:** before inserting, rows with the same `_source_file` are
  deleted. Re-running never duplicates data, and reloading Serie A does not
  touch Premier League rows of the same season.
- **One transaction:** delete and insert succeed or fail together; a broken file
  raises an error and leaves the previous load in place.

Example: `raw.fdorg_matches` holds one row per file, 6 rows in total
(SA and PL x 3 seasons). Each row contains all the matches of that file.

**Weekly refresh:** the current season (`2627`) is re-extracted in full and its
rows are replaced; past seasons are not touched. The API returns the whole
season in one call, so a full refresh costs the same as an incremental one
and also picks up postponed matches and late score corrections.

football-data.org client (`get_json`):

- Fixed pause after every request, computed as `60 / requests_per_minute`
  (6 seconds on the free tier: 10 requests/minute).
- Retry on 429 and 5xx: up to 4 attempts, waiting 10s, 20s, 40s.
- Other errors (e.g. 403 bad token, 404 wrong path) fail immediately.
- All these values come from `config/pipeline.yaml`.
- Run one season by hand: `uv run python -m fdp.ingest.fdorg 2627`
  (6 calls, ~40 seconds).

Arrival checks (`check_payload`), run after each API call and before saving:

| Rule | Brief requirement | Config | Catches |
| --- | --- | --- | --- |
| The list `items_key` exists | required columns | `items_key` | format change, error message instead of data |
| At least `min_items` items | minimum rows | `min_items` (matches 380, teams 20, scorers 1) | empty or partial response |
| Fewer items than the request `limit` | - | `params.limit` | truncated data (as happened with scorers) |

A failed check raises `ValueError` naming the file (e.g. `SA scorers 2425`) and
stops before saving, so the previous good file and rows stay in place.

How CSV files are loaded (`rebuild_raw_csv_table`, used by football-data.co.uk):

- **One row per match**, with the file's own columns: a CSV is already a table.
- **Rebuilt from all files on disk:** `raw.couk_matches` is recreated with
  `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_csv('data/raw/couk/*/*.csv', ...)`.
  Inserting file by file would fail as soon as a season brings new columns.
- `union_by_name = true` aligns columns by name across seasons (113 to 132
  columns per file, 167 in the union); missing columns are NULL.
- `all_varchar = true`: every value is text, no type guessing in raw.
- Technical columns: `_source_file` (from `filename = true`), `_season` (parent
  folder of the file), `_loaded_at`.
- **Idempotent by construction:** the raw files are the source of truth; the
  weekly refresh replaces the current season file and the table is rebuilt,
  so it cannot hold duplicates. One statement: if it fails, the old table stays.

football-data.co.uk ingestion (`couk.py`):

- Download `<base_url>/<season>/<code>.csv` following redirects; no retry
  (no documented rate limit, a failed run is re-run). Season codes match the
  project ones, no conversion.
- File saved byte for byte, BOM included.
- Arrival checks (`check_csv`), before saving: required columns
  (`Div`, `Date`, `HomeTeam`, `AwayTeam`, `FTHG`, `FTAG`, `FTR`) and at least
  1 match. Completeness (380 per finished season) is a dbt test in phase 3.
- Run one season by hand: `uv run python -m fdp.ingest.couk 2627` (no token needed).

Transfermarkt ingestion (`transfermarkt.py`):

- Public files of `dcaribou/transfermarkt-datasets`, one `.csv.gz` per table at
  `<base_url>/<table>.csv.gz`: no credentials (Kaggle is the alternative).
- One-off full load of 7 tables, in config order: `competitions`, `clubs`,
  `players`, `games`, `appearances`, `player_valuations`, `transfers`.
- Files are not split by season (each covers all seasons): saved complete and
  untouched to `data/raw/transfermarkt/<table>.csv.gz`.
- Loaded into `raw.transfermarkt_<table>` (one row per CSV line, all text) with
  `CREATE OR REPLACE TABLE`, so a reload replaces the table.
- Scope filter applied only when loading (the file stays complete):
  - `games`: competitions in scope (`IT1`, `GB1`) and seasons in scope;
  - `appearances`: only appearances of the loaded games;
  - every other table is loaded in full (as the brief asks for players).
- `_season` per row, in the project format, to join with the other sources:
  `games` from `season` (start year 2024 -> `2425`), `appearances` from their
  game. Empty for tables without a season (players, clubs...); valuations and
  transfers are dated events, their season is derived in staging.
- Arrival checks with the shared `check_csv` (reads gzip as a stream and stops
  at `min_rows`): required key columns per table, at least 1 row.
- Run by hand: `uv run python -m fdp.ingest.transfermarkt` (~67 MB, no arguments).

Logging: every ingestion step logs a line while it runs (`requesting`,
`downloading`, `loaded ... (N rows)`), and fdorg retries log a `WARNING`.
`setup_logging()` is called by the command line entry points and at the start
of each Dagster asset (without it Python shows only warnings); pytest captures
logs itself.

## Orchestration (Dagster)

`src/fdp/definitions.py` only wires the ingestion functions as assets; all logic
stays in `ingest/`. Start the UI with `uv run dagster dev` (http://127.0.0.1:3000):
`[tool.dagster] module_name` in `pyproject.toml` points to the definitions, and
`dagster dev` reads `.env` for the football-data.org token.

| Asset (multi-asset) | Produces | Partitions | Metadata in the UI |
| --- | --- | --- | --- |
| `fdorg_raw` | `raw/fdorg_matches`, `raw/fdorg_teams`, `raw/fdorg_scorers` | seasons | items loaded |
| `couk_raw` | `raw/couk_matches` | seasons | matches |
| `transfermarkt_raw` | `raw/transfermarkt_<table>` (7 tables) | none (snapshot) | rows |

- One asset per raw table, keyed `["raw", "<table>"]` like the DuckDB table:
  dagster-dbt will link dbt sources to them by name in phase 2.
- Season partitions (`2425`, `2526`, `2627`) come from `config/pipeline.yaml`.
- DuckDB allows a single writer, so:
  - `in_process_executor`: the steps of a run execute one after the other;
  - `BackfillPolicy.single_run()`: a backfill of several seasons is one run
    looping over `context.partition_keys`, not parallel runs.
  - Do not run other scripts on `data/fdp.duckdb` while Dagster writes to it.
- Run one partition from the command line:
  `uv run dagster asset materialize -m fdp.definitions --select raw/couk_matches --partition 2627`
- dbt models are assets too (`@dbt_assets`, keys `staging/<model>`), and every
  dbt test is an asset check (48 on staging). Each dbt source `raw.<table>` is
  linked to the raw asset with the same key, so the lineage is raw -> staging.
- `DBT_PROJECT.prepare_if_dev()` runs `dbt parse` when `dagster dev` starts, so
  Dagster reads an up-to-date manifest. On a fresh clone, run
  `uv run --directory dbt dbt parse` before `pytest` (the tests load the manifest).

## Transformation (dbt)

dbt (`dbt-core` 1.12 + `dbt-duckdb`) turns raw tables into typed, renamed models.
Run it from the `dbt/` folder: `uv run --directory dbt dbt build` (models and
tests in dependency order); `--select <model>` or `--select staging.fdorg` to
run a part. Python ingestion writes `raw.*`; dbt only reads it through
`source('raw', ...)`: the tables are the contract between the two.

Conventions:

- One DuckDB schema per layer: `raw` (Python), `staging` (views), `core` and
  `marts` (tables), `seeds`. The `generate_schema_name` macro keeps the names
  plain instead of dbt's default `main_staging`.
- Staging models are named `stg_<source>__<entity>`, one folder per source with
  `_<source>__sources.yml` (raw tables) and `_<source>__models.yml` (docs, tests).
- Source ids keep a source prefix: `fdorg_match_id`, `tm_player_id`,
  `couk_match_ref`, so the origin of an id is clear once sources are combined.
- `profiles.yml` uses `../data/fdp.duckdb` (relative to `dbt/`, where both the
  CLI and Dagster run dbt) and `threads: 1` (single writer).
- Only built-in generic tests (`unique`, `not_null`, `accepted_values`,
  `relationships`); multi-column uniqueness is tested on an expression,
  e.g. `season || '_' || fdorg_person_id`. No dbt packages so far.

Staging models (12):

| Source | Model | One row per | Notes |
| --- | --- | --- | --- |
| couk | `stg_couk__matches` | match | `couk_match_ref` built as `I1_2026-08-22_Udinese_Como`; date via `strptime` (fails on a new format); odds left in raw |
| fdorg | `stg_fdorg__matches` | match | JSON unnested; result as H/D/A; main referee (`type = REFEREE`); `match_date_utc` read from the text, not the session time zone |
| fdorg | `stg_fdorg__teams` | team and season | |
| fdorg | `stg_fdorg__squad_players` | player and season | two unnests (teams, then squads); `position_group` in the Transfermarkt vocabulary, unknown roles kept so the test flags them |
| fdorg | `stg_fdorg__scorers` | scorer, competition, season | `assists`/`penalties` NULL kept, decided in core |
| transfermarkt | `stg_transfermarkt__competitions`, `__clubs`, `__players` | entity | `Missing` position -> NULL; every player kept |
| transfermarkt | `stg_transfermarkt__games`, `__appearances` | game, appearance | already in scope; `relationships` tests to games and players |
| transfermarkt | `stg_transfermarkt__player_valuations`, `__transfers` | valuation, transfer | future transfers (475) excluded; fee 0 = free or unknown |

## Team and match reconciliation

### Teams: how the map was built

The brief asks for a manual team map as a dbt seed. Writing ~150 references by
hand is slow and error-prone (`Man United` / `Man City`, `Verona` / `Hellas
Verona FC`), so the seed is drafted by code and then reviewed by hand.

The name method was designed step by step on the real data, each step measured
against the fixture-based truth (49 couk names -> football-data.org teams):

| Step | Idea | Correct | What was learned |
| --- | --- | --- | --- |
| 1 | Count the letters of each word, share of letters in common / longer word | 45/49 | Fails on abbreviations: `man` vs `manchester` = 3/10, so `Man City` -> Hull **City** |
| 2 | Divide by the **shorter** word: letters of the short word found in the other | 47/49 | Abbreviations fixed; fails on anagrams: `inter` letters are all in `fiorentina` |
| 3 | Keep the **order** of the letters (longest common subsequence, LCS) | 48/49 | `inter` in `fiorentina` = 3/5; only `Milan` left: a tie with `Internazionale Milano` |
| 4 | On a tie, prefer the candidate with **fewer extra letters** | 49/49 | Rule found by looking at the error: risk of overfitting |
| 5 | **Checks**: collisions (two names on one team) and mutual match (the team picks the name back) | - | Detect errors instead of choosing: they flag every wrong match |

Because step 4 was tuned on the data, names alone are not trusted. A second,
independent method uses fixtures: the same match has the same date and score in
both sources, so the teams of matching rows "vote" for each other; the most voted
pair wins (other matches with the same date and score are low-share noise).

`uv run python -m fdp.matching.team_map` writes `data/team_map_review.csv`
(git-ignored): one row per team and source, football-data.org as hub. A row is
`sure` only if names and fixtures agree, with no tie, collision or non-mutual
match; otherwise `review` with the reasons (`disagree`, `tie`, `collision`,
`not_mutual`, `no_fixtures`).

Review on 2026-10-29: 147 rows, 136 sure, 11 to review.

- 10 confirmed. Fixtures corrected the names where they disagreed: `Verona`
  -> Hellas Verona (names tied with Chievo Verona), `AC Pisa` -> Pisa Sporting
  Club (names chose Spezia). Hull City and Frosinone (promoted, no Transfermarkt
  games) confirmed on names only.
- 1 removed: Coventry City / transfermarkt. Coventry is not in the Transfermarkt
  dataset; the draft matched it to Leicester City (flagged with 4 reasons).

The reviewed rows are saved as `dbt/seeds/team_map.csv` (146 rows, the brief's
columns only). To refresh it (e.g. promoted teams next season): rerun the script,
review the new `review` rows, update the seed.

### dbt models and tests

- `dim_team`: 49 teams (`team_key` is a readable slug, e.g. `man_united`).
- `map_team`: one row per team, one column per source (`tm_club_id`,
  `fdorg_team_id`, `couk_team_name`), pivoted from the seed; NULL when a source
  has no reference (Coventry City has no Transfermarkt club).
- `map_match`: one row per football-data.org fixture (the hub, including future
  matches), joined to couk and Transfermarkt on date + home team + away team
  after team reconciliation. `match_key` is `season_home_away`, e.g.
  `2526_inter_milan`. Result: every played match is linked; 2425 and 2526 have
  760/760 matches in all three sources, 2627 has 168 couk matches and no
  Transfermarkt games. Matching on season + teams without the date gives the same
  result with zero date differences, so the date join is safe.
- Tests that protect the maps over time:
  - `unique` on every source reference in `map_team` and `map_match`: a wrong
    match (e.g. keeping Coventry on Leicester's club) fails;
  - `assert_every_source_team_is_mapped` (singular): every team in staging must
    be in the seed, so a newly promoted team is reported, not silently dropped;
  - `assert_every_played_match_is_mapped` (singular): every played couk and
    Transfermarkt match must reach a fixture.

## Player reconciliation

football-data.org players (2220 people from squads and scorers) are matched to
Transfermarkt players (50149). Rules from the brief, in `matching/players.py`:

1. **Normalisation**: unidecode, lowercase, punctuation removed, words in
   alphabetical order (`Martínez Lautaro` = `Lautaro Martinez`). Apostrophes are
   deleted rather than splitting words, because sources disagree on them
   (`N'Diaye` / `Ndiaye`): 5 matches moved from review to auto with this.
2. **Candidates**: same birth date, or same team and season (team through
   `map_team`; Transfermarkt appearances for 2024/25 and 2025/26, the club in the
   July 2026 snapshot for 2026/27). Birth date alone is not enough: 1527 players
   share it with more than 5 Transfermarkt players (up to 25).
3. **Score 0-100**: normalised Damerau-Levenshtein and `token_set_ratio`, the
   higher wins (`match_method` records which). On equal scores, fewer edits win:
   `token_set_ratio` gives 100 to any contained name (`Federico` vs `Federico
   Dimarco`), the same problem as `Milan` for teams, solved the same way.
4. **Bands**: above 92 `auto`, 80-92 `review`, below 80 `rejected`
   (thresholds in `config/pipeline.yaml`).
5. **Checks** turn `auto` into `review`: a real tie (same score and edits), a
   collision (football-data.org players with *different* birth dates on the same
   Transfermarkt player), or the team method (LCS) rating another candidate
   higher. football-data.org sometimes has two ids for one person (same name and
   birth date, e.g. Nikola Štulić 221315 / 145690): both may share a match.
6. **Manual corrections**: `dbt/seeds/player_corrections.csv`
   (`fdorg_person_id, tm_player_id, note`), re-read at every run; an empty
   `tm_player_id` means "no match".

The team method (ordered letters, LCS) was also tried as the main rule: 92.7%
auto on birth-date candidates with no disagreement with exact-name matches. It
is kept as an independent second opinion; the brief's measures are the main rule.

Run: Dagster asset `matching/player_matches` (Python, between staging and core)
or `uv run python -m fdp.matching.player_map`. It writes
`matching.player_matches` (one row per football-data.org player with score,
method, band, flags, candidate source). The blocking asset check
`player_match_rate` requires at least 90% matched (`auto` + `manual`), so a drop
stops `map_player` from being rebuilt. dbt `map_player` keeps every
football-data.org player: `player_key` is `tm_<id>` when matched (shared by
duplicate football-data.org ids), `fdorg_<id>` otherwise (`match_method`
`unmatched`).

Review on 2026-10-29: 32 rows in `review`, all decided by hand.

- 27 nicknames or name variants with the same surname and birth date
  (`William` / `Will Smallbone`, `Valentín` / `Taty Castellanos`, `Sávio` /
  `Savinho`) and `Russ` / `Rhys Oakley`: confirmed.
- 2 collisions with different birth dates on one Transfermarkt player: only the
  football-data.org id whose birth date matches Transfermarkt was confirmed
  (Éderson 81440, Ismaël Koné 177734); the other id is marked "no match" (Éderson
  1689 born 1989, Koné 172183 born 06 instead of 16 June). Prudent choice:
  accept a match only when name, team and birth date all agree.

### Metrics (2026-10-29)

Share of football-data.org players per band, after the review:

| Band | Players | Share |
| --- | --- | --- |
| auto (score > 92, no failed check) | 2064 | 93.0% |
| manual (reviewed and confirmed) | 30 | 1.4% |
| **matched (auto + manual)** | **2094** | **94.3%** |
| manual_no_match (reviewed, refused) | 2 | 0.1% |
| rejected (score < 80) | 124 | 5.6% |

Distribution of the best candidate's score (2220 players):

| Score | Players |
| --- | --- |
| 100 | 2051 |
| 95-99.9 | 9 |
| 92.1-94.9 | 8 |
| 80-92 (review band) | 28 |
| 60-79.9 | 51 |
| below 60 | 73 |

- Matched players by candidate rule: both rules 1438, birth date only 618,
  team and season only 38 (birth date differs or is missing in one source).
- Automatic matches by method: Damerau-Levenshtein 1971, `token_set_ratio` 93.
- Rejected players are mostly young squad players missing from Transfermarkt:
  their best candidate is an unrelated person (`Simone Ascione` -> `Weliton`).

## Phases

### Phase 0 – Skeleton
- [x] `uv init --package --name fdp --python 3.12`
- [x] Dev dependencies (pytest, ruff); runtime dependencies are added in the phase that needs them
- [x] `.env.example`, `data/raw/.gitkeep`, `docs/DEVELOPMENT.md`
- [x] ruff and pytest configured in `pyproject.toml`, one smoke test passing
- [x] Credentials: football-data.org token in `.env`. Kaggle key not needed (public
      Transfermarkt files); Kaggle may be added at the end as an alternative source

### Phase 1 – Ingestion (raw layer)
- [x] `config/pipeline.yaml` + `config.py`: seasons, competitions, source parameters
- [x] `ingest/common.py`: DuckDB connection, raw paths, save raw files,
      idempotent JSON load with technical columns
- [x] fdorg arrival checks (`check_payload`): required list, min items, truncation
- [x] `ingest/couk.py`: full season download, `read_csv(union_by_name=true)`,
      arrival checks; loaded for all seasons (2425/2526: 380 per league; 2627: I1 88, E0 80)
- [x] `ingest/fdorg.py`: matches/teams/scorers, 10 req/min, retry on 429/5xx,
      raw JSON saved, full season refresh (see decision log)
- [x] fdorg loaded for all seasons: 6 rows per raw table (SA, PL x 2425, 2526, 2627)
- [x] `ingest/transfermarkt.py`: one-off full load of 7 tables, games and appearances
      filtered to scope, arrival checks (games 1520, appearances 46873)
- [x] Progress logging in all ingestion modules
- [x] Dagster ingestion assets partitioned by season (`definitions.py`), checked with a real run
- [x] Each source: real sample inspected before coding, findings written below

### Phase 2 – Staging, core, reconciliation
- [x] dbt project wired to DuckDB, sources declared on `raw`
- [x] Staging models for the three sources (12 models, 48 tests)
- [x] dbt models as Dagster assets (`dagster-dbt`), lineage raw -> staging
- [x] Team mapping seed (drafted by name + fixture matching, reviewed), `dim_team`, `map_team`, `map_match`
- [x] `matching/players.py` + tests (normalisation, scoring, thresholds)
- [x] Manual corrections table, `map_player`, match-rate asset check (>= 90%): 94.3% matched
- [ ] Dimensions and facts in `core`

### Phase 3 – Marts, quality, CI
- [ ] `mart_player_profile`, `mart_similar_players`
- [ ] dbt tests: unique/not_null, relationships, accepted_values, match counts
- [ ] Weekly schedule on current season partition
- [ ] GitHub Actions: lint + tests on PR; weekly pipeline run

### Phase 4 – Delivery
- [ ] README: architecture, data model, reconciliation metrics, limitations
- [ ] dbt docs generated
- [ ] Demo: player profile and similar players
- [ ] Repository made public

## Data findings

### football-data.co.uk
- URL `https://www.football-data.co.uk/mmz4281/<season>/<league>.csv`
  redirects: the client must follow redirects.
- All six files checked on 2026-10-29 (I1 and E0 x 2425, 2526, 2627):
  HTTP 200, UTF-8 with BOM, `Date` as `dd/mm/yyyy`.
- Rows: 380 for completed seasons; the current season has only played
  matches (I1 88, E0 80), unlike fdorg which also lists future fixtures.
- Columns vary by season: I1 119 / 131 / 113, E0 120 / 132 / 114. The
  difference is mostly bookmaker odds columns appearing and disappearing.
  An earlier check recorded 120 columns for I1 2627: the column set can also
  change during a season. Hence `read_csv(..., union_by_name=true)`.
- Core columns present in every file: `Div`, `Date`, `Time`, `HomeTeam`,
  `AwayTeam`, `FTHG`, `FTAG`, `FTR`, `HTHG`, `HTAG`, `HTR`, `HS`, `AS`, `HST`,
  `AST`, `HF`, `AF`, `HC`, `AC`, `HY`, `AY`, `HR`, `AR`.
- `HxG` / `AxG` only exist in 2026/27 (both leagues), and are not documented
  in `notes.txt`. Past seasons have no xG from this source.
- `Referee` exists in every E0 season and in no I1 season: for Serie A the
  referee comes from fdorg `referees`.
- `Time` is UK local time: Udinese-Como 2026-08-22 is `17:30` in couk and
  `16:30Z` in fdorg (18:30 in Italy). Kickoffs end around 21:00 UK, so the
  match date is the same in both sources.
- Team names are short (`Inter`, `Como`) while fdorg uses official names
  (`FC Internazionale Milano`, `Como 1907`): a manual mapping is needed.

### football-data.org
- Free tier (checked 2026-10-29 on the pricing, coverage and policies pages):
  - 12 competitions, Serie A (`SA`) and Premier League (`PL`) included.
  - 10 requests per minute.
  - Scores and schedules are delayed (no live scores).
  - Not included: line-ups, substitutions, in-match goal scorers, bookings, squads.
- Token verified: `GET /v4/competitions/SA` returns 200.
- Real responses checked on 2026-10-29 (samples in `data/samples/fdorg/`, git-ignored).
  The free tier gives more than the pricing page says.
- `/competitions/SA/matches?season=<start year>` (`season=2026` means 2026/27):
  - Past seasons are available: 2024 returns 380 matches, all `FINISHED`.
  - The current season returns the full fixture list: 380 matches
    (88 `FINISHED`, 102 `TIMED`, 190 `SCHEDULED`). The 88 finished matches
    match the 88 rows of the couk I1 CSV.
  - Kickoff in `utcDate` (UTC, e.g. `2026-08-22T16:30:00Z`).
  - `referees` is filled: covers the missing `Referee` column in couk I1.
  - `odds` only holds a message: not in the free tier.
  - `season.startDate` (2026-08-23) is after the first match (2026-08-22):
    use match dates, not the declared season start.
- `/competitions/SA/teams?season=<year>`:
  - Squads are included, despite the pricing page. Players have `id`, `name`,
    `position`, `dateOfBirth`, `nationality`.
  - Past seasons return the teams of that season (2024 includes Empoli, Monza,
    Venezia) and different, larger squads (all players who were in the roster).
  - `coach` is the current one even for past seasons: do not use it historically.
- `/competitions/SA/scorers?season=<year>`:
  - Default `limit` is 10. With `limit=200` the 2026 response returns 144 players,
    i.e. every player with at least one goal.
  - `limit=200` truncated completed seasons: 2425 and 2526 returned exactly 200.
    Raised to `limit=1000` (accepted by the API): 2425 PL 271, SA 291;
    2526 PL 279, SA 297. Lesson: a count equal to the limit means truncated data.
  - `assists` is null for 81/144 players and `penalties` for 134/144:
    most likely null means 0, to confirm in staging.
  - `player.firstName` is empty and `lastName` holds the full name: use `name`.
  - `player.position` can be null; `section` (e.g. `Offence`) is filled instead.

### Transfermarkt
- Checked on 2026-10-29 from the public files (last modified 2026-09-05).
  The README confirms updates are paused: `games` up to 2026-07-06,
  `appearances` up to 2026-06-28, `player_valuations` up to 2026-06-12,
  no 2026/27 squads.
- Tables (rows, columns): competitions 65/11, clubs 796/17, players 50149/26,
  games 88958/23, appearances 1894350/13, player_valuations 656301/6,
  transfers 175165/10.
- Competition codes: Serie A `IT1`, Premier League `GB1`.
- `games.season` is the start year (2024 = 2024/25), like fdorg.
- In scope: 380 games per league in 2024/25 and 2025/26 (1520), 46873
  appearances (~11500 per league and season). No 2026/27 games.
- `players.date_of_birth` is missing for only 0.1% of players: good for matching.
- `transfers.transfer_date` goes up to 2030: future (announced) transfers,
  to handle in staging.
- Files are gzip, every value quoted, no BOM.

## Open questions

| Question | Status |
| --- | --- |
| Is birth date available on the football-data.org person resource? | yes: `dateOfBirth` in squads and scorers |
| Can matches be joined by date only (fdorg UTC vs couk local time)? | yes: every played match linked, zero date differences across sources |
| Manual team-name map for couk as dbt seed | done: drafted by code, reviewed, `dbt/seeds/team_map.csv` |
| Meaning of `HxG` / `AxG` | undocumented, loaded as-is; only in 2026/27 |
| `Referee` missing in I1: only Serie A or all leagues? | Serie A only, every season; use fdorg `referees` |
| Does the fdorg free tier give access to past seasons (2024, 2025)? | yes for 2024; 2025 to confirm in the first run |
| Does fdorg `/teams` return squads on the free tier? | yes |
| Is fdorg `/scorers` available on the free tier? | yes, with `limit` raised above the default 10 |
| Does fdorg `assists: null` mean 0? | open |

## Decision log

| Date | Decision | Why |
| --- | --- | --- |
| 2026-10-29 | Repo private during development, public in phase 4 | Publish only when README and demo are ready |
| 2026-10-29 | Feature branches + PR into `main`, Conventional Commits | Readable history, CI on PRs from phase 3 |
| 2026-10-29 | Season code `2627` across the project, converted to `2026` only for the fdorg API | One key to compare the three sources |
| 2026-10-29 | fdorg: full season refresh instead of the brief's incremental window (`last date - 7 days`) | One call returns the whole season: same cost, simpler replace, no missed postponed matches |
| 2026-10-29 | Raw JSON stored as one row per file in a `payload` column (schema-on-read) | Raw load cannot break on source changes; typing done in dbt staging |
| 2026-10-29 | Idempotent load by `_source_file`, not by season | Reloading one competition must not delete the other |
| 2026-10-29 | Rate limit as a fixed 6s pause, not by reading API headers | Simple and enough: ~2 minutes for a full load |
| 2026-10-29 | Pipeline parameters in `config/pipeline.yaml` instead of code constants | One place to see and change what is loaded; code only holds logic |
| 2026-10-29 | Arrival checks added after the extract-save-load chain worked | The truncated scorers showed they are needed |
| 2026-10-29 | Arrival checks run before saving, thresholds in the YAML | Bad data never overwrites a good load; limits in one place |
| 2026-10-29 | xG kept as in the source (2026/27 only), no model change | Brief lists xG for couk, but past seasons lack it: documented as a limitation |
| 2026-10-29 | couk CSVs: table rebuilt from all raw files, not inserted file by file | Columns change by season; `union_by_name` handles it only when reading all files together |
| 2026-10-29 | couk arrival check: at least 1 row, not 380 | Current season holds played matches only; completeness is a dbt test |
| 2026-10-29 | Transfermarkt from the public dataset files, not Kaggle | No credentials: a recruiter can clone and run; same download code as couk. Kaggle kept as a possible alternative |
| 2026-10-29 | Transfermarkt files not split by season; `_season` as a column on games and appearances | Each file covers all seasons: splitting would alter the raw copy. The column gives the same `2425` key as the other sources |
| 2026-10-29 | Scope filter applied at load time, raw files kept complete | Adding a competition later only needs a reload, no new download |
| 2026-10-29 | `check_csv` and `season_start_year` moved to `common.py` | Used by more than one source |
| 2026-10-29 | Logging with the standard `logging` module, one logger per module | Long downloads looked stuck without progress messages |
| 2026-10-29 | Dagster wired after all three sources are ingested | Set it up once, knowing every source; logic stays in tested functions |
| 2026-10-29 | One Dagster asset per raw table (multi-assets), keys `["raw", "<table>"]` | dagster-dbt links dbt sources to assets with the same name |
| 2026-10-29 | Sequential execution: in-process executor + single-run backfills | DuckDB allows one writer; the bottleneck is the API rate limit, not compute |
| 2026-10-29 | dbt Core v1 (1.12), not dbt v2 | dbt-duckdb and dagster-dbt are built on Core v1 |
| 2026-10-29 | Staging as views, core and marts as tables | Staging is light and always reads the latest raw; core/marts are queried often |
| 2026-10-29 | Result as H/D/A and role groups in the Transfermarkt vocabulary for every source | One convention across sources; the same accepted_values tests apply |
| 2026-10-29 | Placeholder values (`"null"`, `Missing`) turned into NULL in staging | Empty values look the same in every source |
| 2026-10-29 | Future Transfermarkt transfers excluded in staging | Announced moves did not happen yet; they stay in raw |
| 2026-10-29 | No dbt packages yet (built-in tests only) | Fewer dependencies; reconsider `dbt_utils` for surrogate keys in core |
| 2026-10-29 | Team seed drafted by code, then reviewed by hand | Faster and safer than 150 references typed by hand; the seed stays the reviewed source of truth |
| 2026-10-29 | Two independent methods for teams: names (ordered letters) and fixture votes | The name rule was tuned on the data (overfitting risk); fixtures confirm or correct it |
| 2026-10-29 | Wrong match removed rather than kept (Coventry City / Transfermarkt) | A missing, declared reference is better than a wrong, silent one (it would duplicate Leicester's data) |
| 2026-10-29 | football-data.org as hub for teams and matches | It has every team and fixture of every season, including 2026/27 and future matches |
| 2026-10-29 | Readable keys (`man_united`, `2526_inter_milan`) instead of hashes | Easy to read in reviews and debugging; unique by construction (tested) |
| 2026-10-29 | Brief's player score as main rule, team LCS method as second opinion | The brief fixes the method; the LCS method was benchmarked (92.7% auto) and kept as a cross-check |
| 2026-10-29 | Ties on player score broken by fewer edits (Damerau-Levenshtein) | token_set_ratio scores contained names at 100; same idea as the team tie-break |
| 2026-10-29 | Apostrophes deleted in player names, not used as separators | Sources disagree (`N'Diaye` / `Ndiaye`); "no punctuation" applied to person names |
| 2026-10-29 | Collision only when birth dates differ | football-data.org has duplicate ids for one person; they may share a match |
| 2026-10-29 | `review` is not a match until confirmed in the corrections file | Only reviewed decisions count; the asset check counts auto + manual |
| 2026-10-29 | Doubtful duplicates not confirmed (Éderson 1689, Koné 172183) | Accept a match only when name, team and birth date agree |
| 2026-10-29 | `map_player` keeps unmatched football-data.org players with `fdorg_<id>` keys | Facts from football-data.org (season scoring) always find a player |
| 2026-10-29 | Blocking asset check on the match rate | A quality drop stops downstream models instead of spreading bad data |

## Known limitations

- 5.6% of football-data.org players are not matched to Transfermarkt (mostly
  young squad players not in the Transfermarkt dataset): they have no market
  value or Transfermarkt appearances.
- Player candidates for 2026/27 by team use the Transfermarkt club of July 2026:
  players who moved after the snapshot are found by birth date only.
- Coventry City (2026/27) has no Transfermarkt club: the dataset only covers
  clubs of the leagues it follows. No data is lost (Transfermarkt has no 2026/27).
- Sequential runs only (DuckDB single writer). Parallel loads would need a
  multi-writer database or one DuckDB file per source.
- Transfermarkt data stops on 2026-07-06: no appearances for 2026/27.
- Transfermarkt files come from the dataset author's public bucket: the URL may
  change; the Kaggle dataset `davidcariboo/player-scores` is the fallback.
- xG (`HxG` / `AxG`) from football-data.co.uk only exists for 2026/27:
  past seasons have no xG. The columns are also undocumented in `notes.txt`.
- Serie A referees are not in football-data.co.uk: taken from football-data.org.
- fdorg `min_items: 380` assumes 20-team leagues: adding an 18-team league
  (e.g. Bundesliga, 306 matches) needs a per-competition minimum.
- fdorg client retries only 429 and 5xx (as in the brief): network errors and
  timeouts fail the run, which is simply re-run.
