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
  data/raw/<source>/<season>/        # git-ignored, recreated by the pipeline
  src/fdp/
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

## Phases

### Phase 0 – Skeleton
- [x] `uv init --package --name fdp --python 3.12`
- [x] Dev dependencies (pytest, ruff); runtime dependencies are added in the phase that needs them
- [x] `.env.example`, `data/raw/.gitkeep`, `docs/DEVELOPMENT.md`
- [x] ruff and pytest configured in `pyproject.toml`, one smoke test passing
- [ ] Credentials created: football-data.org token, Kaggle API key, local `.env`

### Phase 1 – Ingestion (raw layer)
- [ ] `ingest/common.py`: DuckDB connection, raw paths, technical columns,
      idempotent replace of a season, arrival checks (min rows, required columns)
- [ ] `ingest/couk.py`: full season download, `read_csv(union_by_name=true)`
- [ ] `ingest/fdorg.py`: matches/teams/scorers, 10 req/min, retry on 429/5xx,
      raw JSON saved, incremental window `last loaded date - 7 days`
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
- I1 2026/27 (2026-10-29): 88 matches, UTF-8 with BOM, 120 columns,
  `Date` as `dd/mm/yyyy`.
- `HxG` / `AxG` present but not documented in `notes.txt`.
- No `Referee` column in I1 2026/27. To check: E0 and past I1 seasons.

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
| Can matches be joined by date only (fdorg UTC vs couk local time)? | open |
| Manual team-name map for couk as dbt seed | open |
| Meaning of `HxG` / `AxG` | undocumented, loaded as-is |
| `Referee` missing in I1: only Serie A or all leagues? | open (fdorg `referees` can cover it) |
| Does the fdorg free tier give access to past seasons (2024, 2025)? | yes for 2024; 2025 to confirm in the first run |
| Does fdorg `/teams` return squads on the free tier? | yes |
| Is fdorg `/scorers` available on the free tier? | yes, with `limit` raised above the default 10 |
| Does fdorg `assists: null` mean 0? | open |

## Decision log

| Date | Decision | Why |
| --- | --- | --- |
| 2026-10-29 | Repo private during development, public in phase 4 | Publish only when README and demo are ready |
| 2026-10-29 | Feature branches + PR into `main`, Conventional Commits | Readable history, CI on PRs from phase 3 |

## Known limitations

- Transfermarkt data stops on 2026-07-06: no appearances for 2026/27.
