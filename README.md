# aashaan

A personal fitness coach for Mac, synced with Google Health (Fitbit). It pulls your data into local Parquet files, and Claude Code turns it into daily check-ins, weekly and monthly reviews, trend notes, and tracked advice.

> **Privacy:** all health data stays on your machine. `data/`, `profile.md`, `journal/`, `reports/`, `trends/`, `plans/` and `advice/` are gitignored. Only the tooling is in this repo.

## Requirements

- macOS with [uv](https://docs.astral.sh/uv/)
- The `ghealth` CLI, authenticated against your Google Health account (`ghealth auth status` to check)
- [Claude Code](https://claude.com/claude-code)

## Quick start

```bash
git clone https://github.com/jassimabdullatheef/aashaan.git
cd aashaan
uv run scripts/sync.py           # pull the last 30 days into monthly Parquet files
```

Sync options: `--days N`, `--from YYYY-MM-DD --to YYYY-MM-DD`, `--force` (refetch), `--rebuild-only` (skip fetching, rebuild `data/daily/`).

Then create a `profile.md` with your baseline facts, goals, conditions and medications, and ask Claude things like:

- "Sync and write today's daily check-in"
- "Write the weekly review"
- "Write the monthly review for October"
- "How is my sleep trending?" / "Why was my resting HR high this week?"
- "Log in my journal: headache, slept badly, coffee at 6pm"

## Layout

| Path | Purpose | In git |
|------|---------|--------|
| `scripts/sync.py` | Fetches data via `ghealth` into Parquet and rebuilds the daily table | yes |
| `templates/` | Daily, weekly and monthly report templates | yes |
| `CLAUDE.md` | Conventions Claude follows (workflow, data notes, advice rules) | yes |
| `.agents/skills/` | Agent skills (e.g. `fitness-coach`) | yes |
| `profile.md` | Your goals, conditions and context. Create this first. | no |
| `data/daily/YYYY-MM.parquet` | One row per day, all key metrics | no |
| `data/raw/<type>/YYYY-MM.parquet` | Raw records per data type | no |
| `reports/` | Daily, weekly and monthly reviews | no |
| `trends/` | Notes on one metric over time | no |
| `plans/` | Training and cardio plan | no |
| `advice/log.md` | Recommendations and whether they worked | no |
| `journal/` | Your own notes (illness, travel, diet), which help explain the data | no |

See `CLAUDE.md` for the full conventions.

## Disclaimer

This is a self-tracking aid, not medical advice. See a clinician for anything that worries you.
