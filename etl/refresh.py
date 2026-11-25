"""One refresh: team sheet, new calls from Acefone / MCube / Ozonetel, manual calls and LSQ stage changes -> Supabase,
then the per-caller, per-day summary the dashboard reads.

    python -m etl.refresh                 # normal run (incremental)
    python -m etl.refresh --full true     # also rebuild every summary day still covered by stored calls
    python -m etl.refresh --backfill true # one time: copy the call history from BigQuery first
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

from etl import config as C
from etl.rules import IST, UTC, call_times, day_metrics, merge_key


def log(msg: str) -> None:
    print(msg, flush=True)


def iso_dt(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else UTC.localize(v)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def day_list(a: date, b: date) -> list[date]:
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def build_days(store, days: set[date], tmap: dict[str, dict], today: date) -> int:
    """Recompute agent_day for these dates from the stored calls (one day per request)."""
    n = 0
    for d in sorted(days):
        data = store.rpc("ca_day_rows", {"p_from": d.isoformat(), "p_to": d.isoformat()})
        groups: dict[str, list] = defaultdict(list)
        for c in data["calls"]:
            dt = iso_dt(c["call_datetime"])
            start, end = call_times(c["src"], dt, int(c["call_duration"] or 0))
            groups[c["call_owner"]].append({"status": c["status"], "call_duration": int(c["call_duration"] or 0), "start": start, "end": end,
                                            "src": c["src"], "client_number": c["client_number"], "reason": None})
        for m in data["manual"]:
            groups[m["call_owner"]].append({"status": "answered", "call_duration": int(m["call_duration"] or 0), "start": None, "end": None,
                                            "src": "N", "client_number": m["client_number"], "reason": m["approved_by"]})
        # map dialler names to the team sheet's Caller Name (Streamlit: merge on lower-cased name, fall back to the raw name)
        by_caller: dict[str, list] = defaultdict(list)
        for owner, calls in groups.items():
            t = tmap.get(merge_key(owner))
            name = t["caller_name"] if t else owner
            if name is None or name == "":
                continue
            by_caller[name] += calls
        rows = []
        for name, calls in by_caller.items():
            t = tmap.get(merge_key(name)) or {}
            m = day_metrics(calls, d, today)
            rows.append({"call_date": d.isoformat(), "caller": name, "team": t.get("team_name"), "vertical": t.get("vertical"),
                         "analyst": t.get("analyst"), **m})
        store.delete("agent_day", f"call_date=eq.{d.isoformat()}")
        if rows:
            store.insert("agent_day", rows)
        n += len(rows)
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", default="false")
    ap.add_argument("--backfill", default="false")
    a = ap.parse_args(argv)
    full = str(a.full).lower() in ("1", "true", "yes")
    backfill = str(a.backfill).lower() in ("1", "true", "yes")

    from etl import sources as S
    from etl.store import Store

    store = Store()
    now = datetime.now(IST)
    today = now.date()
    keep_from = today - timedelta(days=C.KEEP_DAYS - 1)          # manual calls, stage changes
    calls_from = today - timedelta(days=C.CALL_KEEP_DAYS - 1)    # dialler calls, and the days whose summaries are rebuilt
    ampm = now.strftime("%d/%m/%Y %I:%M %p")
    detail: dict = {"updated_at_ampm": ampm, "full": full, "backfill": backfill}
    errors: list[str] = []
    try:
        # 1. team sheet
        team = S.team_map()
        if not team:
            raise RuntimeError("Team sheet is empty")
        old_hash = (store.select("refresh_log", "select=detail&status=eq.ok&order=run_at.desc&limit=1") or [{}])[0].get("detail", {}) or {}
        team_hash = hashlib.sha1(json.dumps(sorted(team, key=lambda r: r["merge_key"]), sort_keys=True).encode()).hexdigest()
        store.upsert("team_map", team, "merge_key")
        keys = {r["merge_key"] for r in team}
        stale = [r["merge_key"] for r in store.select("team_map", "select=merge_key") if r["merge_key"] not in keys]
        for i in range(0, len(stale), 100):
            store.delete("team_map", "merge_key=in.(" + ",".join(json.dumps(k) for k in stale[i:i + 100]) + ")")
        tmap = {r["merge_key"]: r for r in team}
        detail.update({"team_rows": len(team), "team_hash": team_hash})
        team_changed = old_hash.get("team_hash") != team_hash

        # 2. calls
        calls: list[dict] = []
        if backfill:
            calls += S.bigquery_history(calls_from, log)
        wm = store.rpc("ca_watermarks")
        fallback = now - timedelta(days=2)
        for src, fetch in (("A", S.fetch_acefone), ("M", S.fetch_mcube)):
            since = (iso_dt(wm.get(src)).astimezone(IST) - timedelta(minutes=C.REWIND_MIN)) if wm.get(src) else fallback
            if backfill and calls:
                last = max((c["call_datetime"] for c in calls if c["src"] == src and c["call_datetime"]), default=None)
                since = (iso_dt(last).astimezone(IST) - timedelta(minutes=C.REWIND_MIN)) if last else since
            try:
                got = fetch(since, ampm, log)
                detail[f"api_{src}"] = len(got)
                calls += got
            except Exception as e:  # one dialler down must not stop the others
                errors.append(f"{src}: {e}")
                log(f"::warning::{src} not refreshed: {e}")
        try:
            o_last = max((c["call_date"] for c in calls if c["src"] == "O"), default=None)
            o_since = o_last or (date.fromisoformat(wm["O"]) if wm.get("O") else today - timedelta(days=7))
            got = S.fetch_ozonetel(o_since if isinstance(o_since, date) else date.fromisoformat(str(o_since)), ampm, S.ozonetel_agents(), log)
            detail["api_O"] = len(got)
            calls += got
        except Exception as e:
            errors.append(f"O: {e}")
            log(f"::warning::Ozonetel not refreshed: {e}")

        calls = [c for c in calls if c["call_date"] and (c["call_date"] if isinstance(c["call_date"], date) else date.fromisoformat(str(c["call_date"]))) >= calls_from]
        batches = {}
        for c in calls:
            if c["ampm"] not in batches:
                batches[c["ampm"]] = store.rpc("ca_batch", {"p_ampm": c["ampm"] or ""})
        dedup = {}
        for c in calls:
            dedup[c["id"]] = c
        rows = [{"id": c["id"], "src": c["src"], "client_number": c["client_number"],
                 "call_datetime": c["call_datetime"].astimezone(UTC).isoformat() if c["call_datetime"] else None,
                 "call_duration": c["call_duration"], "status": c["status"], "direction": c["direction"], "service": c["service"],
                 "reason": c["reason"], "call_owner": c["call_owner"], "call_date": str(c["call_date"]), "batch": batches[c["ampm"]]}
                for c in sorted(dedup.values(), key=lambda c: (str(c["call_date"]), c["id"]))]
        store.upsert("calls", rows, "id", ignore_existing=True)
        touched = {date.fromisoformat(r["call_date"]) for r in rows}
        detail["calls_read"] = len(rows)

        # 3. manual calls + stage changes (whole sheets; table = sheet for the kept dates)
        man = S.manual_rows(S.read_csv(C.MANUAL_CSV_URL), keep_from, today)
        stored = lambda table: {int(i): d for i, d in store.rpc("ca_ids", {"p_table": table, "p_from": keep_from.isoformat()})}
        before = stored("manual_calls")
        now_ids = {r["id"] for r in man}
        touched |= {date.fromisoformat(d) for i, d in before.items() if i not in now_ids}
        touched |= {date.fromisoformat(r["call_date"]) for r in man if r["id"] not in before}
        store.rpc("ca_sync_ids", {"p_table": "manual_calls", "p_from": keep_from.isoformat(), "p_ids": list(now_ids)})
        store.upsert("manual_calls", [r for r in man if r["id"] not in before], "id", ignore_existing=True)
        stg = S.stage_rows(S.read_csv(C.STAGE_CSV_URL, dtype=str), keep_from)
        before = stored("stage_changes")
        store.rpc("ca_sync_ids", {"p_table": "stage_changes", "p_from": keep_from.isoformat(), "p_ids": [r["id"] for r in stg]})
        new_stage = [r for r in stg if r["id"] not in before]
        store.upsert("stage_changes", new_stage, "id", ignore_existing=True)
        detail.update({"manual_rows": len(man), "stage_rows": len(stg), "stage_new": len(new_stage)})

        # 4. summaries
        trimmed = store.rpc("ca_trim", {"p_keep_from": keep_from.isoformat(), "p_calls_from": calls_from.isoformat()})
        # a day's summary needs its calls, so only days still holding calls are rebuilt; older summaries stay as they are
        days = set(day_list(calls_from, today)) if (full or backfill or team_changed) else {d for d in touched if d >= calls_from}
        days |= {today, today - timedelta(days=1)}
        detail["summary_days"] = len(days)
        detail["agent_day_rows"] = build_days(store, days, tmap, today)
        detail["stage_day_rows"] = store.rpc("ca_rebuild_stage_day", {"p_from": keep_from.isoformat()})
        detail["day_unique_rows"] = store.rpc("ca_rebuild_day_unique", {"p_from": min(days).isoformat()})
        detail["trimmed"] = trimmed
        if errors:
            detail["warnings"] = errors
        store.insert("refresh_log", [{"status": "ok", "detail": detail}])
        log(json.dumps(detail, indent=2, default=str))
        return 0
    except Exception as exc:
        detail["error"] = f"{type(exc).__name__}: {exc}"[:1000]
        print("::error::" + detail["error"].replace("\n", " "))
        try:
            store.insert("refresh_log", [{"status": "failed", "detail": detail}])
        except Exception as e2:
            print("::error::Could not write refresh_log: " + str(e2)[:300])
        return 1


if __name__ == "__main__":
    sys.exit(main())
