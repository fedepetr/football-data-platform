# Development Guide

Working document for building the Football Data Platform. It tracks the plan,
the conventions, what we learned about the data and the decisions we made.
Updated at the end of every phase.

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Project skeleton, tooling, conventions | in progress |
| 1 | Ingestion of the three sources into `raw` | todo |
| 2 | dbt staging and core, player reconciliation | todo |
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
    ingest/{common,couk,fdorg,transfermarkt}.py
    matching/players.py
    definitions.py                   # Dagster wiring only
  dbt/
    dbt_project.yml / profiles.yml
    seeds/ models/{staging,core,marts}/
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

## Phases

### Phase 0 – Skeleton
- [x] `uv init --package --name fdp --python 3.12`
- [x] Dev dependencies (pytest, ruff); runtime dependencies are added in the phase that needs them
- [x] `.env.example`, `data/raw/.gitkeep`, `docs/DEVELOPMENT.md`
- [x] ruff and pytest configured in `pyproject.toml`, one smoke test passing
- [ ] Credentials created: football-data.org token (done), Kaggle API key (pending), local `.env`

### Phase 1 – Ingestion (raw layer)
- [x] `config/pipeline.yaml` + `config.py`: seasons, competitions, source parameters
- [x] `ingest/common.py`: DuckDB connection, raw paths, save raw files,
      idempotent JSON load with technical columns
- [x] fdorg arrival checks (`check_payload`): required list, min items, truncation
- [x] `ingest/couk.py`: full season download, `read_csv(union_by_name=true)`,
      arrival checks; loaded for all seasons (2425/2526: 380 per league; 2627: I1 88, E0 80)
- [ ] Transfermarkt arrival checks (with its ingestion)
- [x] `ingest/fdorg.py`: matches/teams/scorers, 10 req/min, retry on 429/5xx,
      raw JSON saved, full season refresh (see decision log)
- [x] fdorg loaded for all seasons: 6 rows per raw table (SA, PL x 2425, 2526, 2627)
- [ ] `ingest/transfermarkt.py`: one-off full load of 7 tables, filtered to scope
- [ ] Dagster ingestion assets partitioned by season
- [ ] Each source: real sample inspected before coding, findings written below

### Phase 2 – Staging, core, reconciliation
- [ ] dbt project wired to DuckDB, sources declared on `raw`
- [ ] Team mapping seed, `map_team`, `map_match`
- [ ] `matching/players.py` + tests (normalisation, scoring, thresholds)
- [ ] Manual corrections table, `map_player`, match-rate asset check (>= 90%)
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
- _todo_

## Open questions

| Question | Status |
| --- | --- |
| Is birth date available on the football-data.org person resource? | yes: `dateOfBirth` in squads and scorers |
| Can matches be joined by date only (fdorg UTC vs couk local time)? | yes: couk time is UK time, same date as UTC for every kickoff |
| Manual team-name map for couk as dbt seed | needed: names differ (`Inter` vs `FC Internazionale Milano`) |
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
| 2026-10-29 | Dagster wired after all three sources are ingested | Set it up once, knowing every source; logic stays in tested functions |

## Known limitations

- Transfermarkt data stops on 2026-07-06: no appearances for 2026/27.
- xG (`HxG` / `AxG`) from football-data.co.uk only exists for 2026/27:
  past seasons have no xG. The columns are also undocumented in `notes.txt`.
- Serie A referees are not in football-data.co.uk: taken from football-data.org.
- fdorg `min_items: 380` assumes 20-team leagues: adding an 18-team league
  (e.g. Bundesliga, 306 matches) needs a per-competition minimum.
- fdorg client retries only 429 and 5xx (as in the brief): network errors and
  timeouts fail the run, which is simply re-run.
