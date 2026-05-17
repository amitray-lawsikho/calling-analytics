"""
mcube_cursor_check.py — diagnostic only. Does NOT touch BigQuery.
Verifies whether MCUBE returns saturated windows sorted ascending by starttime,
and dry-runs the cursor-walk so you can see how many calls / how many records.
"""
import os
import time
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta

# ---------------- CONFIG ----------------
API_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
MCUBE_TOKEN = os.getenv("MCUBE_TOKEN")

SLEEP_BETWEEN_CALLS = 21
MAX_RECORDS_PER_RESPONSE = 100
OVERLAP = timedelta(minutes=1)
MAX_ITERATIONS = 500

india = pytz.timezone("Asia/Kolkata")

# >>> SET THIS: a window you believe has 100+ Mcube calls <
# Pick a busy stretch — e.g. a couple of weekdays.
CHECK_START = india.localize(datetime(2026, 5, 12, 0, 0, 0))
CHECK_END   = india.localize(datetime(2026, 5, 14, 0, 0, 0))


def _api_call(start_ist, end_ist):
    payload = {
        "start_date": start_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date":   end_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "Authorization": MCUBE_TOKEN,
    }
    headers = {"Content-Type": "application/json"}
    try:
        res = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        if res.status_code == 429:
            print("  Rate limited. Sleeping 65s...")
            time.sleep(65)
            return _api_call(start_ist, end_ist)
        if res.status_code != 200:
            print(f"  HTTP {res.status_code}: {res.text[:200]}")
            return []
        data = res.json()
        if data.get("status") != "succ":
            print(f"  status != succ: {data.get('status')}")
            return []
        msg = data.get("msg") or []
        return msg if isinstance(msg, list) else []
    except Exception as e:
        print(f"  Error {start_ist} -> {end_ist}: {e}")
        return []


def check_sort_order():
    """TEST 1: does a saturated window come back ascending by starttime?"""
    print("=" * 60)
    print("TEST 1 — sort order of a saturated window")
    print(f"Window: {CHECK_START} -> {CHECK_END}")
    print("=" * 60)

    msg = _api_call(CHECK_START, CHECK_END)
    print(f"Records returned: {len(msg)}")

    if len(msg) < MAX_RECORDS_PER_RESPONSE:
        print(f"\n!! This window returned < 100 records.")
        print("   The sort-order test only means something on a SATURATED")
        print("   (100-record) window. Pick a busier CHECK_START/CHECK_END.")
        return None

    st = pd.to_datetime([r.get("starttime") for r in msg], errors="coerce")
    valid = st[pd.notna(st)]
    print(f"Parseable starttimes: {len(valid)} / {len(msg)}")
    if len(valid) == 0:
        print("!! Could not parse any starttime — check the field name.")
        return None

    ordered = list(valid)
    ascending  = ordered == sorted(ordered)
    descending = ordered == sorted(ordered, reverse=True)

    print(f"\n  earliest in batch: {valid.min()}")
    print(f"  latest in batch:   {valid.max()}")
    print(f"  sorted ASCENDING?  {ascending}")
    print(f"  sorted DESCENDING? {descending}")
    print()
    if ascending:
        print(">>> SAFE: cursor-walk works. It returns the earliest 100;")
        print("    jumping the cursor to the latest seen misses nothing.")
    elif descending:
        print(">>> NOT SAFE for cursor-walk: returns the LATEST 100, so")
        print("    walking forward would skip earlier records.")
        print("    Use the recursive binary-split sweep instead.")
    else:
        print(">>> UNSORTED: order is not guaranteed. Do NOT cursor-walk.")
        print("    Use the recursive binary-split sweep instead.")
    return ascending


def dry_run_cursor_walk():
    """TEST 2: simulate the full cursor-walk. Counts only — pushes nothing."""
    print()
    print("=" * 60)
    print("TEST 2 — cursor-walk dry run (no BQ, no write)")
    print("=" * 60)

    all_ids = []
    seen_ids = set()
    cursor = CHECK_START
    first_call = True
    api_calls = 0

    for i in range(MAX_ITERATIONS):
        if cursor >= CHECK_END:
            break
        if not first_call:
            time.sleep(SLEEP_BETWEEN_CALLS)
        first_call = False

        msg = _api_call(cursor, CHECK_END)
        api_calls += 1
        new = [str(r.get("callid")) for r in msg if str(r.get("callid")) not in seen_ids]
        seen_ids.update(str(r.get("callid")) for r in msg)
        all_ids.extend(str(r.get("callid")) for r in msg)
        print(f"  call {api_calls}: {cursor} -> {CHECK_END} | "
              f"{len(msg)} records ({len(new)} new)")

        if len(msg) < MAX_RECORDS_PER_RESPONSE:
            print("  -> tail reached (< 100). Done.")
            break

        times = pd.to_datetime([r.get("starttime") for r in msg], errors="coerce")
        times = times[pd.notna(times)]
        if len(times) == 0:
            print("  !! no parseable timestamps in a full batch — stopping.")
            break
        latest = india.localize(times.max().to_pydatetime())
        next_cursor = latest - OVERLAP
        if next_cursor <= cursor:
            next_cursor = latest + timedelta(seconds=1)
            print(f"  WARNING: 100+ records near {latest}; stepping past it.")
        cursor = next_cursor
    else:
        print(f"  WARNING: hit MAX_ITERATIONS ({MAX_ITERATIONS}).")

    print()
    print(f"  total API calls:     {api_calls}")
    print(f"  total records seen:  {len(all_ids)}")
    print(f"  unique callids:      {len(seen_ids)}")
    print(f"  duplicate re-pulls:  {len(all_ids) - len(seen_ids)} (expected, from overlap)")


if __name__ == "__main__":
    if not MCUBE_TOKEN:
        raise SystemExit("MCUBE_TOKEN env var is not set.")
    check_sort_order()
    dry_run_cursor_walk()
