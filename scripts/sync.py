#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas>=2.2", "pyarrow>=17"]
# ///
"""Pull health data via the ghealth CLI into monthly Parquet files.

Usage:
    uv run scripts/sync.py                  # last 30 days
    uv run scripts/sync.py --days 90
    uv run scripts/sync.py --from 2026-10-14 --to 2026-10-28
    uv run scripts/sync.py --rebuild-only   # rebuild data/daily/ from raw
    uv run scripts/sync.py --force          # refetch the range even if already synced

Syncs are incremental: data/sync_state.json records which dates each type has
already fetched, and only the missing dates are requested. The most recent
REFRESH_DAYS are never marked as fetched, because the API is still filling them in
(today's steps, a sleep that ends tomorrow, late device uploads).

Storage (one file per month):
    data/raw/<type>/YYYY-MM.parquet   records per data type, merged by record key,
                                      so repeated syncs accumulate history
    data/daily/YYYY-MM.parquet        one row per day, rebuilt from raw each run

Raw files hold flattened columns for querying plus `_json`, the full original
record, so nothing from the API is lost.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DAILY = ROOT / "data" / "daily"
STATE = ROOT / "data" / "sync_state.json"
REFRESH_DAYS = 2
GHEALTH = os.environ.get("GHEALTH_BIN", "/Users/work/Code/google-health-cli/ghealth")

# Types fetched with `list` (record-level data).
LIST_TYPES = [
    "daily-resting-heart-rate",
    "daily-heart-rate-variability",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "daily-sleep-temperature-derivations",
    "daily-vo2-max",
    "sleep",
    "exercise",
    "weight",
    "body-fat",
    "height",
    "hydration-log",
    "nutrition-log",
]
# Types fetched with `daily-rollup` (API caps rollups at 14 days per request).
ROLLUP_TYPES = [
    "steps",
    "distance",
    "floors",
    "active-minutes",
    "total-calories",
    "active-zone-minutes",
    "time-in-heart-rate-zone",
]
ROLLUP_CHUNK_DAYS = 14


def run(args):
    proc = subprocess.run([GHEALTH, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"exit {proc.returncode}")
    return json.loads(proc.stdout)["dataPoints"]


def fetch_list(dtype, start, end):
    return run(["data", dtype, "list", "--from", start.isoformat(),
                "--to", end.isoformat(), "--limit", "5000"])


def fetch_rollup(dtype, start, end):
    rows, cur = [], start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=ROLLUP_CHUNK_DAYS - 1), end)
        rows += run(["data", dtype, "daily-rollup", "--from", cur.isoformat(),
                     "--to", chunk_end.isoformat()])
        cur = chunk_end + dt.timedelta(days=1)
    return rows


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def merge_intervals(intervals):
    """Merge [from, to] ISO-date intervals that overlap or touch."""
    out = []
    for a, b in sorted(intervals):
        if out and dt.date.fromisoformat(a) <= dt.date.fromisoformat(out[-1][1]) + dt.timedelta(days=1):
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def missing_ranges(covered, start, end):
    """Sub-ranges of [start, end] not in the covered intervals."""
    gaps, cur = [], start
    for a, b in merge_intervals(covered):
        a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        if b < cur:
            continue
        if a > end:
            break
        if a > cur:
            gaps.append((cur, min(end, a - dt.timedelta(days=1))))
        cur = max(cur, b + dt.timedelta(days=1))
    if cur <= end:
        gaps.append((cur, end))
    return gaps


def record_key(rec):
    return rec.get("id") or rec.get("date") or rec.get("time") or json.dumps(rec, sort_keys=True)


def record_time(rec):
    return rec.get("date") or rec.get("start") or rec.get("time") or rec.get("end") or ""


def flatten(rec, prefix=""):
    """Nested dicts become dotted columns; lists become JSON strings."""
    out = {}
    for k, v in rec.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        elif isinstance(v, list):
            out[key] = json.dumps(v)
        else:
            out[key] = v
    return out


def write_month(path, records):
    records = sorted(records, key=record_time)
    df = pd.DataFrame([{**flatten(r), "_json": json.dumps(r, sort_keys=True)} for r in records])
    # The API mixes str/number for some fields; Parquet needs one type per column.
    for c in df.columns[df.dtypes == object]:
        if df[c].dropna().map(type).nunique() > 1:
            df[c] = df[c].map(lambda v: None if v is None or v != v else str(v))
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def read_month(path):
    if not path.exists():
        return []
    return [json.loads(j) for j in pd.read_parquet(path, columns=["_json"])["_json"]]


def merge_raw(dtype, new_rows):
    """Merge new records into data/raw/<type>/YYYY-MM.parquet. Returns stored total."""
    by_month = defaultdict(list)
    for r in new_rows:
        by_month[record_time(r)[:7]].append(r)
    for month, rows in by_month.items():
        if not month:
            continue
        path = RAW / dtype / f"{month}.parquet"
        # A stored record is replaced by an incoming one with the same key, or with
        # the same start time (a sleep/exercise re-processed under a new id).
        incoming = {record_key(r): r for r in rows}
        starts = {r["start"] for r in rows if r.get("start")}
        kept = [r for r in read_month(path)
                if record_key(r) not in incoming and r.get("start") not in starts]
        write_month(path, kept + list(incoming.values()))
    return len(load_raw(dtype))


def load_raw(dtype):
    rows = []
    for path in sorted((RAW / dtype).glob("*.parquet")):
        rows += read_month(path)
    return rows


def migrate_json():
    """One-time move from the old data/raw/<type>.json layout."""
    for path in RAW.glob("*.json"):
        merge_raw(path.stem, json.loads(path.read_text()))
        path.unlink()
    old_csv = ROOT / "data" / "daily.csv"
    if old_csv.exists():
        old_csv.unlink()


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def seconds(s):
    return num(s[:-1]) if isinstance(s, str) and s.endswith("s") else None


def build_daily():
    days = defaultdict(dict)

    def put(date, col, val):
        if val is not None:
            if isinstance(val, float):
                val = int(val) if val.is_integer() else round(val, 2)
            days[date][col] = val

    for r in load_raw("steps"):
        put(r["date"], "steps", num(r.get("countSum")))
    for r in load_raw("distance"):
        mm = num(r.get("millimetersSum"))
        put(r["date"], "distance_km", mm / 1e6 if mm else None)
    for r in load_raw("floors"):
        put(r["date"], "floors", num(r.get("countSum")))
    for r in load_raw("total-calories"):
        put(r["date"], "calories_kcal", num(r.get("kcalSum")))
    for r in load_raw("active-minutes"):
        for lvl in r.get("activeMinutesRollupByActivityLevel", []):
            put(r["date"], f"active_min_{lvl['activityLevel'].lower()}", num(lvl.get("activeMinutesSum")))
    for r in load_raw("active-zone-minutes"):
        fat = num(r.get("sumInFatBurnHeartZone")) or 0
        cardio = num(r.get("sumInCardioHeartZone")) or 0
        peak = num(r.get("sumInPeakHeartZone")) or 0
        # Fitbit convention: cardio/peak minutes count double.
        put(r["date"], "azm", fat + 2 * (cardio + peak))
    for r in load_raw("daily-resting-heart-rate"):
        put(r["date"], "resting_hr", num(r.get("beatsPerMinute")))
    for r in load_raw("daily-heart-rate-variability"):
        put(r["date"], "hrv_ms", num(r.get("averageHeartRateVariabilityMilliseconds")))
    for r in load_raw("daily-oxygen-saturation"):
        put(r["date"], "spo2_pct", num(r.get("averagePercentage")))
    for r in load_raw("daily-respiratory-rate"):
        put(r["date"], "resp_rate", num(r.get("breathsPerMinute")))
    for r in load_raw("daily-sleep-temperature-derivations"):
        n, b = num(r.get("nightlyTemperatureCelsius")), num(r.get("baselineTemperatureCelsius"))
        put(r["date"], "skin_temp_delta_c", n - b if n is not None and b is not None else None)
    for r in load_raw("daily-vo2-max"):
        put(r["date"], "vo2max", num(r.get("vo2Max") or r.get("estimatedVo2Max")))

    # Sleep is attributed to the date it ended (wake-up day); naps tracked separately.
    for r in load_raw("sleep"):
        date = r["end"][:10]
        d = days[date]
        if r.get("isNap"):
            d["nap_min"] = d.get("nap_min", 0) + (r.get("minutesAsleep") or 0)
            continue
        stages = r.get("stageMinutes", {})
        d["sleep_min"] = d.get("sleep_min", 0) + (r.get("minutesAsleep") or 0)
        d["sleep_awake_min"] = d.get("sleep_awake_min", 0) + (r.get("minutesAwake") or 0)
        for st in ("DEEP", "REM", "LIGHT"):
            d[f"sleep_{st.lower()}_min"] = d.get(f"sleep_{st.lower()}_min", 0) + stages.get(st, 0)
        # Bedtime/wake of the longest main sleep on that date.
        if r.get("minutesAsleep", 0) >= d.get("_main_len", 0):
            d["_main_len"] = r.get("minutesAsleep", 0)
            d["bedtime"] = r["start"][11:16]
            d["wake_time"] = r["end"][11:16]

    for r in load_raw("exercise"):
        date = (r.get("start") or r.get("end") or "")[:10]
        if not date:
            continue
        d = days[date]
        d["exercise_count"] = d.get("exercise_count", 0) + 1
        mins = (seconds(r.get("activeDuration")) or 0) / 60
        d["exercise_min"] = round(d.get("exercise_min", 0) + mins, 1)
        names = set(filter(None, d.get("exercise_types", "").split("|")))
        names.add((r.get("displayName") or r.get("exerciseType", "?")).capitalize())
        d["exercise_types"] = "|".join(sorted(names))

    # Weight: last reading of the day.
    for r in sorted(load_raw("weight"), key=lambda r: r["time"]):
        put(r["time"][:10], "weight_kg", num(r.get("weightGrams")) / 1000)
    for r in load_raw("body-fat"):
        put((r.get("time") or "")[:10], "body_fat_pct", num(r.get("percentage")))

    cols = ["date", "steps", "distance_km", "floors", "calories_kcal",
            "active_min_light", "active_min_moderate", "active_min_vigorous", "azm",
            "exercise_count", "exercise_min", "exercise_types",
            "resting_hr", "hrv_ms", "spo2_pct", "resp_rate", "skin_temp_delta_c", "vo2max",
            "sleep_min", "sleep_deep_min", "sleep_rem_min", "sleep_light_min", "sleep_awake_min",
            "bedtime", "wake_time", "nap_min", "weight_kg", "body_fat_pct"]
    df = pd.DataFrame([{"date": d, **days[d]} for d in sorted(k for k in days if k)])
    df = df.reindex(columns=cols)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ("exercise_types", "bedtime", "wake_time"):
        df[c] = df[c].astype("string")
    DAILY.mkdir(parents=True, exist_ok=True)
    for old in DAILY.glob("*.parquet"):
        old.unlink()
    months = pd.to_datetime(df["date"]).dt.strftime("%Y-%m")
    for month, part in df.groupby(months):
        part.to_parquet(DAILY / f"{month}.parquet", index=False)
    return DAILY, len(df)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--rebuild-only", action="store_true", help="skip fetching; rebuild data/daily/")
    p.add_argument("--force", action="store_true", help="refetch even dates already synced")
    a = p.parse_args()

    end = dt.date.fromisoformat(a.end) if a.end else dt.date.today()
    start = dt.date.fromisoformat(a.start) if a.start else end - dt.timedelta(days=a.days - 1)
    RAW.mkdir(parents=True, exist_ok=True)
    migrate_json()

    failures = []
    if not a.rebuild_only:
        print(f"Syncing {start} → {end}")
        state = load_state()
        settled = dt.date.today() - dt.timedelta(days=REFRESH_DAYS)
        for dtype in LIST_TYPES + ROLLUP_TYPES:
            covered = [] if a.force else state.get(dtype, [])
            gaps = missing_ranges(covered, start, end)
            if not gaps:
                print(f"  {dtype:38} up to date")
                continue
            fetch = fetch_rollup if dtype in ROLLUP_TYPES else fetch_list
            try:
                rows = []
                for g_start, g_end in gaps:
                    rows += fetch(dtype, g_start, g_end)
                total = merge_raw(dtype, rows)
                # Mark as fetched only after the data is safely written.
                done = [[x.isoformat(), min(y, settled).isoformat()] for x, y in gaps if x <= settled]
                state[dtype] = merge_intervals(state.get(dtype, []) + done)
                save_state(state)
                spans = ", ".join(f"{x}…{y}" if x != y else f"{x}" for x, y in gaps)
                print(f"  {dtype:38} +{len(rows):4}  (stored {total})  fetched {spans}")
            except Exception as e:
                failures.append(dtype)
                print(f"  {dtype:38} FAILED: {str(e)[:120]}", file=sys.stderr)

    out, n = build_daily()
    (ROOT / "data" / "last_sync.txt").write_text(dt.datetime.now().isoformat(timespec="seconds") + "\n")
    print(f"Wrote {out.relative_to(ROOT)}/ ({n} days)")
    if failures:
        print(f"Failed types: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
