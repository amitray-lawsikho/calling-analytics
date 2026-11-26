"""One-time (resumable) load of the last 60 days into the Suspicious Calls tables, straight from the MCube and Acefone APIs
(the Calling tables keep no disconnect side, ring time or recording). Day by day, oldest first; each dialler's day is
reset and reloaded whole, so a rerun never double counts. Stops before the time budget and carries on next run.

    python -m etl.sus_backfill --hours 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta

from etl import config as C
from etl import suspicious as SUS
from etl.rules import IST


def log(m: str) -> None:
    print(m, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--restart", default="false")
    a = ap.parse_args(argv)
    from etl import sources as S
    from etl.store import Store
    store, t0 = Store(), time.time()
    today = datetime.now(IST).date()
    first = today - timedelta(days=C.CALL_KEEP_DAYS - 1)
    st = (store.select("sus_state", "select=value&key=eq.backfill") or [{}])[0].get("value") or {}
    done = date.fromisoformat(st["done_through"]) if st.get("done_through") and str(a.restart).lower() != "true" else first - timedelta(days=1)
    d = max(done + timedelta(days=1), first)
    while d <= today:
        if time.time() - t0 > a.hours * 3600:
            log(f"Time budget reached; next run continues from {d}")
            break
        day_start = IST.localize(datetime.combine(d, datetime.min.time()))
        day_end = day_start + timedelta(days=1) - timedelta(seconds=1)
        if d == today:
            day_end = datetime.now(IST) - timedelta(minutes=C.LAG_MIN)
        stats = {}
        for src, fetch in (("M", S.fetch_mcube), ("A", S.fetch_acefone)):
            SUS.reset()
            fetch(day_start, "", log, until=day_end)
            store.rpc("sus_reset_day", {"p_day": d.isoformat(), "p_src": src})
            SUS.ROWS[:] = [r for r in SUS.ROWS if r["d"] == d.isoformat()]
            stats[src] = SUS.ingest(store, first)["new"]
        store.rpc("sus_set_state", {"p_key": "backfill", "p_value": {"done_through": d.isoformat(), "from": first.isoformat(),
                                                                       "at": datetime.now(IST).isoformat(timespec="seconds")}})
        log(f"{d}: MCube {stats['M']}, Acefone {stats['A']} calls ({(time.time() - t0) / 60:.0f} min)")
        d += timedelta(days=1)
    log(json.dumps({"done_through": (d - timedelta(days=1)).isoformat(), "minutes": round((time.time() - t0) / 60)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
