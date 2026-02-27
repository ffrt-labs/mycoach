#!/usr/bin/env python3
"""Manually fetch raw data from each Garmin API endpoint used by mycoach.

Usage:
    # Fetch all endpoints for today
    uv run python scripts/fetch_garmin.py

    # Fetch for a specific date
    uv run python scripts/fetch_garmin.py --date 2026-02-20

    # Fetch only specific endpoints
    uv run python scripts/fetch_garmin.py --only sleep hrv stats

    # Fetch activities for a date range
    uv run python scripts/fetch_garmin.py --date 2026-02-20 --end-date 2026-02-26

    # Save output to a file
    uv run python scripts/fetch_garmin.py --date 2026-02-20 --out garmin_raw.json
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

import garth
from garminconnect import Garmin

TOKEN_DIR = Path(__file__).parent.parent / ".garmin_tokens"

ENDPOINTS = [
    "stats",
    "heart_rates",
    "hrv",
    "sleep",
    "stress",
    "body_battery",
    "training_readiness",
    "training_status",
    "max_metrics",
    "respiration",
    "spo2",
    "activities",
]


def connect() -> Garmin:
    if not TOKEN_DIR.exists():
        print(f"[ERROR] Token directory not found: {TOKEN_DIR}", file=sys.stderr)
        print("Run the app first to authenticate and save tokens.", file=sys.stderr)
        sys.exit(1)

    try:
        garth.resume(str(TOKEN_DIR))
        _ = garth.client.username
    except Exception as e:
        print(f"[ERROR] Could not resume Garmin session: {e}", file=sys.stderr)
        sys.exit(1)

    api = Garmin()
    api.login(tokenstore=str(TOKEN_DIR))
    print(f"[OK] Connected to Garmin Connect")
    return api


def fetch_all(api: Garmin, day: date, end_day: date, only: list[str], split_dir: Path | None = None) -> dict:
    d = day.isoformat()
    e = end_day.isoformat()
    results = {}

    def run(name: str, fn, *args):
        if only and name not in only:
            return
        print(f"  Fetching {name}...", end=" ", flush=True)
        try:
            results[name] = fn(*args)
            print("OK")
        except Exception as ex:
            results[name] = {"error": str(ex)}
            print(f"ERROR: {ex}")
        if split_dir is not None and name in results:
            (split_dir / f"{name}.json").write_text(json.dumps(results[name], indent=2, default=str))

    run("stats",               api.get_stats,               d)
    run("heart_rates",         api.get_heart_rates,         d)
    run("hrv",                 api.get_hrv_data,            d)
    run("sleep",               api.get_sleep_data,          d)
    run("stress",              api.get_stress_data,         d)
    run("body_battery",        api.get_body_battery,        d, e)
    run("training_readiness",  api.get_training_readiness,  d)
    run("training_status",     api.get_training_status,     d)
    run("max_metrics",         api.get_max_metrics,         d)
    run("respiration",         api.get_respiration_data,    d)
    run("spo2",                api.get_spo2_data,           d)
    run("activities",          api.get_activities_by_date,  d, e)

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch raw Garmin API data")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Date to fetch (YYYY-MM-DD). Default: today",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="End date for range endpoints (body_battery, activities). Default: same as --date",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=ENDPOINTS,
        metavar="ENDPOINT",
        help=f"Fetch only these endpoints. Choices: {', '.join(ENDPOINTS)}",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Save JSON output to this file path",
    )
    parser.add_argument(
        "--split-dir",
        default="garmin-api-data",
        help="Directory to write one JSON file per endpoint (default: garmin-api-data)",
    )
    args = parser.parse_args()

    try:
        day = date.fromisoformat(args.date)
    except ValueError:
        print(f"[ERROR] Invalid date: {args.date}", file=sys.stderr)
        sys.exit(1)

    end_day = date.fromisoformat(args.end_date) if args.end_date else day
    only = args.only or []

    print(f"\nDate: {day}  End: {end_day}")
    print(f"Endpoints: {', '.join(only) if only else 'all'}\n")

    split_dir = Path(args.split_dir)
    split_dir.mkdir(parents=True, exist_ok=True)
    print(f"Per-endpoint files → {split_dir}/\n")

    api = connect()
    print()
    results = fetch_all(api, day, end_day, only, split_dir=split_dir)

    output = json.dumps(results, indent=2, default=str)

    if args.out:
        Path(args.out).write_text(output)
        print(f"\n[OK] Saved to {args.out}")
    else:
        print(f"\n{'='*60}\n")
        print(output)


if __name__ == "__main__":
    main()
