"""
MCUBE Pagination Probe
======================
Verifies MCUBE's Historical Data API pagination behaviour empirically.

DOES NOT TOUCH BIGQUERY. Read-only API hits.

Pick a window known to be BUSY (>3500 calls) so pagination actually triggers.
Run during low traffic — this script only reads from MCUBE, no writes anywhere.
"""

import os
import time
import requests

# ---- CONFIG ----
API_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
MCUBE_TOKEN = os.getenv("MCUBE_TOKEN")
SLEEP = 21  # 3 hits/min => 21s between calls

# Pick a window you believe had >3500 calls so we can actually exercise paging.
# Multi-day windows from busy weekdays are safest. Adjust to your data.
START = "2026-05-14 00:00:00"
END   = "2026-05-25 23:59:59"

# Stop probing after this many pages even if pages keep coming back full.
MAX_PAGES_TO_PROBE = 6


def call_api(start, end, page=None, label=""):
    payload = {
        "start_date": start,
        "end_date": end,
        "Authorization": MCUBE_TOKEN,
    }
    if page is not None:
        payload["page"] = page

    try:
        res = requests.post(
            API_URL, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
    except Exception as e:
        print(f"  [{label}] REQUEST ERROR: {e}")
        return None

    if res.status_code != 200:
        print(f"  [{label}] HTTP {res.status_code}: {res.text[:200]}")
        return None

    try:
        data = res.json()
    except Exception:
        print(f"  [{label}] Non-JSON response: {res.text[:200]}")
        return None

    if data.get("status") != "succ":
        print(f"  [{label}] Non-success: {str(data)[:200]}")
        return []  # treat as empty, not failure

    msg = data.get("msg") or []
    return msg if isinstance(msg, list) else []


def ids_of(rows):
    return {str(r.get("callid")) for r in rows if r.get("callid")}


def probe():
    print(f"Window: {START} -> {END}")
    print("=" * 70)

    # ---- Test A: no page param (current production behaviour) ----
    print("\n[A] No page param (mirrors current pipeline)")
    baseline = call_api(START, END, page=None, label="A")
    if baseline is None:
        print("  Baseline call failed. Aborting.")
        return
    baseline_ids = ids_of(baseline)
    print(f"  Records returned : {len(baseline)}")
    print(f"  Unique callids   : {len(baseline_ids)}")

    # ---- Test B: page=1, 2, 3, ... ----
    all_paged_ids = set()
    per_page = []
    for page in range(1, MAX_PAGES_TO_PROBE + 1):
        time.sleep(SLEEP)
        print(f"\n[B{page}] page={page}")
        rows = call_api(START, END, page=page, label=f"B{page}")
        if rows is None:
            print("  Failed; stopping page loop.")
            break

        page_ids = ids_of(rows)
        new_ids  = page_ids - all_paged_ids
        dupes    = page_ids & all_paged_ids

        print(f"  Records returned         : {len(rows)}")
        print(f"  Unique callids on page   : {len(page_ids)}")
        print(f"  NEW (not on prior pages) : {len(new_ids)}")
        print(f"  DUPLICATES of prior pages: {len(dupes)}")

        all_paged_ids |= page_ids
        per_page.append((page, len(rows), len(new_ids), len(dupes)))

        if len(rows) == 0:
            print("  Empty page — pagination end reached.")
            break
        if len(rows) < 3500:
            print("  Partial page — likely last page.")
            break

    # ---- Test C: past-the-end page (sanity check on stop condition) ----
    time.sleep(SLEEP)
    print(f"\n[C] page=999 (well past the end)")
    far = call_api(START, END, page=999, label="C")
    if far is not None:
        far_ids = ids_of(far)
        wraps_to_page_1 = bool(far_ids) and far_ids.issubset(all_paged_ids) and len(far_ids) > 0
        print(f"  Records returned : {len(far)}")
        if len(far) == 0:
            print("  Returns []. Safe: empty-page stop condition works.")
        elif wraps_to_page_1:
            print("  WARNING: out-of-range page returned data that overlaps existing pages.")
            print("  API may be wrapping or ignoring the page param. Investigate.")
        else:
            print("  WARNING: out-of-range page returned unexpected data.")

    # ---- Summary ----
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Baseline (no page param) unique callids : {len(baseline_ids)}")
    print(f"Paginated total unique callids          : {len(all_paged_ids)}")
    print(f"Per-page counts (page, total, new, dup) : {per_page}")

    only_baseline = baseline_ids - all_paged_ids
    only_paged    = all_paged_ids - baseline_ids
    print(f"In baseline only  : {len(only_baseline)}")
    print(f"In paginated only : {len(only_paged)}")

    if not per_page:
        print("\nNo paginated data fetched — can't conclude.")
        return

    max_page_size = max(c for _, c, _, _ in per_page)
    full_pages    = sum(1 for _, c, _, _ in per_page if c >= 3500)

    print(f"\nLargest single page size : {max_page_size}")
    print(f"Pages at the 3500 cap    : {full_pages}")

    print("\nInterpretation:")
    if len(all_paged_ids) > len(baseline_ids):
        print("  PAGINATION IS NEEDED. The no-page-param call is silently")
        print("  dropping records. Cumulative paged fetch returned more")
        print(f"  unique callids ({len(all_paged_ids)}) than baseline ({len(baseline_ids)}).")
    elif len(all_paged_ids) == len(baseline_ids) and only_baseline == set() and only_paged == set():
        print("  Baseline and paginated results MATCH for this window.")
        print("  Either (a) window had <=3500 records, so paging didn't trigger,")
        print("  or (b) no-page-param already returns everything.")
        if max_page_size < 3500:
            print(f"  Window only had {max_page_size} records on its biggest page —")
            print("  rerun with a busier multi-day window to actually exercise paging.")
    else:
        print("  ANOMALY — baseline and paginated sets differ but neither is a")
        print("  strict superset. Inspect the per-page output above.")


if __name__ == "__main__":
    if not MCUBE_TOKEN:
        raise SystemExit("MCUBE_TOKEN env var not set.")
    probe()
