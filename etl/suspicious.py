"""Suspicious Call Analysis (Amit, 26 Nov 2026): who ended each call, talk time, customer ring time and the recording link,
captured from the raw MCube / Acefone records the refresh already reads. Separate tables (sus_*); a failure here never
touches the Calling tables (the refresh calls ingest() inside its own try/except).

Rules (field check, 26 Nov 2026):
  MCube   outbound only. disconnectedBy = Agent | Customer. talk = answeredtime (hh:mm:ss). customer ring = cust_ring_endtime
          - cust_ring_sttime. dialstatus ANSWER = answered, Executive Busy = dialer call not taken, BUSY / NOANSWER = reached
          the customer, not answered. Recording = filename.
  Acefone outbound only. reason disconnected_by_caller = the agent ended it (outbound: the caller is our agent). talk =
          answered_seconds. No customer ring time in the API: for calls not answered the call duration stands in for it.
          Recording = recording_url. Same caller and id as the Calling table (acefone_row).
  Pick-up cut  = answered, agent ended it, talk <= the chosen limit (5 / 10 / 20 / 60 / 180 s).
  Low-ring cut = not answered, agent ended it, customer ring <= 10 s.
"""
from __future__ import annotations

from datetime import date

from etl.rules import IST, UTC, acefone_row, hid, hms_seconds, parse_naive

LOW_RING = 10
KEEP_TALK = 180
ROWS: list[dict] = []          # filled while the refresh reads the diallers


def reset() -> None:
    ROWS.clear()


def _secs(a, b) -> int | None:
    s, e = parse_naive(a), parse_naive(b)
    if s is None or e is None or s.year < 2000 or e.year < 2000:
        return None
    return max(0, int((e - s).total_seconds()))


def mcube(r: dict) -> dict | None:
    if str(r.get("direction") or "").strip().lower() != "outbound":
        return None
    start = parse_naive(r.get("starttime"))
    if start is None:
        return None
    st = str(r.get("dialstatus") or "").strip().upper()
    k = {"ANSWER": "A", "EXECUTIVE BUSY": "X", "BUSY": "R", "NOANSWER": "R"}.get(st, "O")
    agent = str(r.get("disconnectedBy") or "").strip().lower() in ("agent", "executive")
    talk = hms_seconds(r.get("answeredtime")) if k == "A" else 0
    cring = _secs(r.get("cust_ring_sttime"), r.get("cust_ring_endtime"))
    s_ist = IST.localize(start)
    return {"id": hid("MCB", r.get("callid")), "d": s_ist.date().isoformat(), "caller": str(r.get("agentname") or "").strip(), "src": "M",
            "k": k, "agent": agent, "talk": talk, "cring": cring, "low": bool(k == "R" and agent and cring is not None and cring <= LOW_RING),
            "num": str(r.get("callto") or "")[-10:], "dt": s_ist.astimezone(UTC).isoformat(), "rec": str(r.get("filename") or ""),
            "ref": str(r.get("callid") or "")}


def acefone(call: dict) -> dict | None:
    if str(call.get("direction") or "").strip().lower() != "outbound":
        return None
    x = acefone_row(call, "")
    if not x:
        return None
    k = "A" if str(call.get("status") or "").lower() == "answered" else "R"
    agent = str(call.get("reason") or "").strip().lower() == "disconnected_by_caller"
    try:
        talk = int(call.get("answered_seconds") or 0) if k == "A" else 0
    except (TypeError, ValueError):
        talk = 0
    cring = None if k == "A" else int(x["call_duration"] or 0)
    return {"id": x["id"], "d": x["call_date"].isoformat(), "caller": x["call_owner"], "src": "A", "k": k, "agent": agent, "talk": talk,
            "cring": cring, "low": bool(k == "R" and agent and cring is not None and cring <= LOW_RING), "num": x["client_number"],
            "dt": x["call_datetime"].isoformat(), "rec": str(call.get("recording_url") or ""), "ref": str(call.get("call_id") or "")}


def keep(src: str, raw: dict) -> None:
    """Called for every raw record the refresh reads; never raises."""
    try:
        row = mcube(raw) if src == "M" else acefone(raw)
        if row and row["caller"]:
            if not (row["k"] == "A" and row["agent"] and row["talk"] <= KEEP_TALK):
                row = {k: v for k, v in row.items() if k not in ("num", "dt", "rec", "ref")}   # counts only
            ROWS.append(row)
    except Exception:
        pass


def ingest(store, keep_from: date, batch: int = 2000) -> dict:
    rows = [r for r in ROWS if r["d"] >= keep_from.isoformat()]
    new = 0
    for i in range(0, len(rows), batch):
        new += int(store.rpc("sus_ingest", {"p_rows": rows[i:i + batch]}) or 0)
    trim = store.rpc("sus_trim", {"p_from": keep_from.isoformat()})
    return {"read": len(rows), "new": new, "trim": trim}
