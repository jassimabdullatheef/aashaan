# Personal Health Workspace

This directory is where the user reviews their own health data from Google Health (Fitbit) via the `ghealth` CLI, and where summaries, trends, and advice are stored. Use the `ghealth` skill for ad-hoc queries.

## Layout

| Path | Purpose |
|------|---------|
| `profile.md` | Who the user is: baseline facts, goals, conditions, meds. Read first, every session. |
| `data/raw/<type>/YYYY-MM.parquet` | Raw records per data type per month, merged across syncs. Flattened columns plus `_json` (the full original record). Don't edit by hand. |
| `data/daily/YYYY-MM.parquet` | One row per day, all key metrics, one file per month. Primary source for analysis. Rebuilt on every sync. |
| `data/last_sync.txt` | Timestamp of the last sync. |
| `data/sync_state.json` | Date ranges already fetched per type. Sync skips these and always refreshes the last 2 days. Delete a type's entry, or pass `--force`, to refetch. |
| `scripts/sync.py` | Pulls data into Parquet and rebuilds `data/daily/`. Run with `uv run` (dependencies are declared inline). |
| `reports/daily/YYYY-MM-DD.md` | Daily check-ins. |
| `reports/weekly/YYYY-Www.md` | Weekly reviews (ISO week, e.g. `2026-W44`). |
| `reports/monthly/YYYY-MM.md` | Monthly reviews. |
| `trends/<metric>.md` | Long-running notes on one metric (baselines, changes, causes). |
| `plans/training-plan.md` | Current training and cardio plan (phases, exercises, tracking targets). Check adherence against it in weekly reviews. |
| `advice/log.md` | Every recommendation given, with date, rationale, and follow-up status. |
| `journal/YYYY-MM-DD.md` | User's own notes: symptoms, illness, travel, labs, doctor visits, diet. |
| `templates/` | Report templates. |

## Workflow

1. **Sync first.** Run `uv run scripts/sync.py` (default: last 30 days; `--days N` or `--from/--to` for more; `--rebuild-only` skips fetching). If auth fails, run `ghealth auth status` and relay its `next_steps`.
2. **Read context.** Read `profile.md`, the latest report of the same kind, open items in `advice/log.md`, and recent `journal/` entries (illness, travel and stress explain a lot of anomalies).
3. **Analyze `data/daily/`.** Compare against the user's own baseline (rolling 7-day vs prior 28-day), not just population norms. Say how many days of data back each claim.
4. **Write the report** from the matching template. Update `trends/` when a baseline shifts, and append new advice to `advice/log.md`.
5. **Follow up.** In each weekly review, check whether last week's advice was followed and whether it helped.

## Reading the data

Point pandas at a folder to read every month at once:

```bash
uv run --with pandas --with pyarrow python -c "
import pandas as pd
d = pd.read_parquet('data/daily')                        # all months
oct = pd.read_parquet('data/daily/2026-10.parquet')       # one month
sleep = pd.read_parquet('data/raw/sleep')                 # raw records
"
```

## Data notes

- The device is a Fitbit Air. Weight is logged through Apple Shortcuts. Account data starts 2026-10-14; earlier rows are partial.
- Sleep is assigned to the **wake-up date**. Naps go in `nap_min`, not `sleep_min`.
- `azm` = active zone minutes: fat-burn minutes count once, cardio and peak count twice (Fitbit convention). WHO guideline ≈ 150 AZM per week.
- `skin_temp_delta_c` = nightly skin temperature minus personal baseline. Above +1°C for several nights can mean illness.
- `exercise_count` includes auto-detected walks.
- Today's row is always partial until the day ends.
- Empty types right now: vo2-max, body-fat, nutrition. Hydration is sparse.

## Advice rules

- Ground every recommendation in the user's data, and cite the numbers.
- Give one to three specific, actionable items. Don't list everything that could be improved.
- Flag anything that warrants a clinician clearly and calmly: sustained resting HR rise above 10 bpm, SpO2 averages below 92%, irregular-rhythm notifications, abnormal ECG, a temperature spike with symptoms, or unexplained rapid weight change. This is a self-tracking aid, not a diagnosis.
- Use the user's goals in `profile.md` to set priorities.
