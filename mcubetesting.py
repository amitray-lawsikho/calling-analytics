"""
mcube_cap_probe.py -- diagnostic only. Does NOT touch BigQuery.

Finds the API's real response cap by:
  1. Cross-checking a window's total against the sum of its two halves.
     If total == sum -> not truncated. If total < sum -> truncated, and
     `total` is at or near the cap.
  2. Widening a known-busy window step by step. If the count climbs then
     freezes at a fixed number, that frozen number is the cap.
"""
import os
import time
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta

API_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
MCUBE_TOKEN = os.getenv("MCUBE_TOKEN")
SLEEP = 21
india = pytz.timezone("Asia/Kolkata")

# >>> SET THIS: a window you KNOW is busy. From your earlier run, the evening
# of 15 May 2026 had 220+ calls in roughly 17:50-19:00. Use that.
BUSY_START = india.localize(datetime(2026, 5, 18, 10, 0, 0))


def api_count(start_ist, end_ist):
    """Returns (record_count, unique_callid_count) for a window."""
    payload = {
        "start_date": start_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date":   end_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "Authorization": MCUBE_TOKEN,
    }
    try:
        res = requests.post(API_URL, headers={"Content-Type": "application/json"},
                            json=payload, timeout=60)
        if res.status_code == 429:
            print("  rate limited, sleeping 65s")
            time.sleep(65)
            return api_count(start_ist, end_ist)
        if res.status_code != 200:
            print(f"  HTTP {res.status_code}: {res.text[:150]}")
            return None, None
        data = res.json()
        if data.get("status") != "succ":
            print(f"  status != succ: {data.get('status')}")
            return None, None
        msg = data.get("msg") or []
        if not isinstance(msg, list):
            return 0, 0
        ids = {str(r.get("callid")) for r in msg}
        return len(msg), len(ids)
    except Exception as e:
        print(f"  error: {e}")
        return None, None


def test_split_consistency():
    """
    TEST 1 -- the decisive one.
    Query a busy window, then its two halves. If the API is truncating,
    total < (half1 + half2). If it returns everything, total == half1 + half2
    (minus a tiny boundary overlap).
    """
    print("=" * 60)
    print("TEST 1 -- whole vs sum-of-halves")
    print("=" * 60)
    start = BUSY_START
    end   = BUSY_START + timedelta(hours=2)
    mid   = start + (end - start) / 2

    total, total_u = api_count(start, end)
    print(f"  WHOLE   {start.time()}-{end.time()} : {total} records ({total_u} unique)")
    time.sleep(SLEEP)
    h1, h1_u = api_count(start, mid)
    print(f"  HALF 1  {start.time()}-{mid.time()} : {h1} records ({h1_u} unique)")
    time.sleep(SLEEP)
    h2, h2_u = api_count(mid, end)
    print(f"  HALF 2  {mid.time()}-{end.time()} : {h2} records ({h2_u} unique)")

    if None in (total, h1, h2):
        print("\n  A call failed -- rerun.")
        return
    halves = h1 + h2
    print(f"\n  whole = {total}    half1 + half2 = {halves}")
    if total >= halves - 5:          # small tolerance for boundary-second overlap
        print(f"  >>> NOT TRUNCATED. The API returned the full window ({total}).")
        print("      No cap was hit at this volume.")
    else:
        print(f"  >>> TRUNCATED. Whole window lost {halves - total} records.")
        print(f"      The cap is at or near {total}.")


def test_widening():
    """
    TEST 2 -- widen a busy window until the count stops growing.
    A frozen count = the cap. A still-growing count = cap not yet reached.
    """
    print()
    print("=" * 60)
    print("TEST 2 -- widening sweep")
    print("=" * 60)
    counts = []
    for hours in [1, 2, 4, 8, 24, 72]:
        end = BUSY_START + timedelta(hours=hours)
        c, u = api_count(BUSY_START, end)
        print(f"  +{hours:>3}h window : {c} records ({u} unique)")
        counts.append(c)
        time.sleep(SLEEP)

    clean = [c for c in counts if c is not None]
    if len(clean) >= 2 and clean[-1] == clean[-2] and clean[-1] > 100:
        print(f"\n  >>> Count froze at {clean[-1]} despite a wider window.")
        print(f"      That strongly suggests the cap is {clean[-1]}.")
    else:
        print("\n  >>> Count kept changing -- no cap hit in this range.")
        print("      The API appears to return everything.")


if __name__ == "__main__":
    if not MCUBE_TOKEN:
        raise SystemExit("MCUBE_TOKEN not set.")
    test_split_consistency()
    test_widening()
