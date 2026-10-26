"""Calling rules, ported line by line from the Streamlit calling dashboard (final_dashboard.py, run_calling_dashboard)
and the old BigQuery pipelines (legacy/). Pure functions, no I/O. See docs/definitions.md."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta

import pytz

IST = pytz.timezone("Asia/Kolkata")
UTC = pytz.utc

OFFICE_START = time(10, 0)
OFFICE_END = time(20, 0)
LATE_AFTER = time(10, 15)
DAY_SEC = 36000            # 10 office hours
MIN_GAP = 300              # a break is a gap of 5 minutes or more
QUALIFY = 180              # "calls > 3 mins" = duration >= 180 s

SOURCE_NAME = {"A": "Acefone", "O": "Ozonetel", "M": "MCube", "N": "Manual"}


def hid(*parts) -> int:
    """Stable signed 64-bit id from the parts (Postgres bigint)."""
    h = hashlib.sha1("|".join("" if p is None else str(p) for p in parts).encode()).digest()
    return int.from_bytes(h[:8], "big", signed=True)


def as_str(v) -> str:
    """What pandas .astype(str) wrote into BigQuery for object columns: None -> 'None'."""
    return str(v)


def merge_key(name) -> str:
    return str(name or "").strip().lower()


# ---------------------------------------------------------------- source rows -> stored call rows
# Stored row: id, src, client_number, call_datetime (aware UTC), call_duration, status, direction, service, reason,
# call_owner (raw dialler name), call_date, ampm (updated_at_ampm of the load)

def safe_duration(x) -> int:
    try:
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h * 3600 + m * 60 + s
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def hms_seconds(t) -> int:
    try:
        h, m, s = map(int, str(t).split(":"))
        return h * 3600 + m * 60 + s
    except (TypeError, ValueError):
        return 0


def parse_naive(s):
    """'2026-10-25 10:00:12' -> naive datetime, or None."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(s)[:19], fmt)
        except ValueError:
            continue
    return None


def acefone_row(call: dict, ampm: str) -> dict | None:
    """legacy/acefone_pipeline.py fetch + filter (outbound, or inbound answered; no answered 0-second calls)."""
    end = parse_naive(call.get("end_stamp"))
    if end is None:
        return None
    end_ist = IST.localize(end)
    agent = None
    if call.get("agent_name"):
        agent = call["agent_name"]
    elif call.get("missed_agents"):
        missed = call.get("missed_agents")
        agent = missed[0].get("name") if missed and isinstance(missed, list) else None
    else:
        for flow in call.get("call_flow", []) or []:
            if flow.get("type") == "Agent":
                agent = flow.get("name")
                break
    status = as_str(call.get("status"))
    direction = as_str(call.get("direction"))
    duration = safe_duration(call.get("call_duration"))
    if not (direction == "outbound" or (direction == "inbound" and status.lower() == "answered")):
        return None
    if status.lower() == "answered" and duration == 0:
        return None
    cid = str(call.get("call_id"))
    return {"id": hid("ACE", cid), "src": "A", "client_number": str(call.get("client_number", ""))[-10:],
            "call_datetime": end_ist.astimezone(UTC), "call_duration": duration, "status": status, "direction": direction,
            "service": as_str(call.get("service")), "reason": as_str(call.get("reason")),
            "call_owner": str(agent or "").replace("-Extension", "").strip(), "call_date": end_ist.date(), "ampm": ampm}


def mcube_row(r: dict, ampm: str) -> dict | None:
    """legacy/mcube_pipeline.py process_records; drops CANCEL (pipeline) and EXECUTIVE BUSY (dashboard query)."""
    dialstatus = str(r.get("dialstatus")).upper()
    if "CANCEL" in dialstatus.strip() or dialstatus.strip() == "EXECUTIVE BUSY":
        return None
    start, end = parse_naive(r.get("starttime")), parse_naive(r.get("endtime"))
    if start is None:
        return None
    duration = max(0, int((end - start).total_seconds())) if end else 0
    start_ist = IST.localize(start)
    return {"id": hid("MCB", r.get("callid")), "src": "M", "client_number": str(r.get("callto") or "")[-10:],
            "call_datetime": start_ist.astimezone(UTC), "call_duration": duration,
            "status": "answered" if dialstatus == "ANSWER" else "missed",
            "direction": str(r.get("direction")).strip().lower(), "service": None,
            "reason": str(r.get("dialstatus") if r.get("dialstatus") is not None else ""),
            "call_owner": str(r.get("agentname") or "").strip(), "call_date": start_ist.date(), "ampm": ampm}


def ozonetel_status(s) -> str:
    s = str(s).lower()
    return "missed" if s == "unanswered" else s


def ozonetel_direction(s) -> str:
    s = str(s).lower()
    return "outbound" if s == "manual" else s


def ozonetel_row(d: dict, day: date, agents_lower: set[str], ampm: str) -> dict | None:
    """legacy/ozonetel_pipeline.py: our agents only, phone required, no missed inbound; dashboard status/direction mapping."""
    agent = str(d.get("AgentName")).strip()
    if agent.lower() not in agents_lower:
        return None
    phone = str(d.get("E164"))[-10:]
    if not phone.strip() or phone.strip() == "nan" or d.get("E164") is None:
        return None
    raw_status = d.get("Status", d.get("DialStatus"))
    if str(d.get("Type")).lower() == "inbound" and str(raw_status).lower() == "unanswered":
        return None
    start = parse_naive(f"{day.isoformat()} {d.get('StartTime')}")
    end = parse_naive(f"{day.isoformat()} {d.get('EndTime')}")
    start_utc = IST.localize(start).astimezone(UTC) if start else None
    end_utc = IST.localize(end).astimezone(UTC) if end else None
    return {"id": hid("OZO", d.get("CallID"), start_utc.isoformat() if start_utc else "", end_utc.isoformat() if end_utc else ""),
            "src": "O", "client_number": phone, "call_datetime": start_utc, "call_duration": hms_seconds(d.get("Duration")),
            "status": ozonetel_status(raw_status), "direction": ozonetel_direction(d.get("Type")), "service": None,
            "reason": d.get("Disposition"), "call_owner": agent, "call_date": day, "ampm": ampm}


# ---------------------------------------------------------------- per caller, per day metrics (process_metrics_logic)

def fmt_hm(sec) -> str:
    """format_dur_hm: Python round() (half to even) on minutes."""
    if sec is None or sec <= 0:
        return "0h 0m"
    tm = int(round(sec / 60))
    return f"{tm // 60}h {tm % 60}m"


def gap(s: datetime, e: datetime) -> float:
    """get_display_gap_seconds: both ends floored to the minute."""
    return (e.replace(second=0, microsecond=0) - s.replace(second=0, microsecond=0)).total_seconds()


def call_times(src: str, call_datetime_utc: datetime | None, duration: int):
    """Start and end in IST. Acefone stores the call end; Ozonetel and MCube store the call start; Manual has no time."""
    if call_datetime_utc is None:
        return None, None
    t = call_datetime_utc.astimezone(IST)
    d = timedelta(seconds=duration)
    if src in ("O", "M"):
        return t, t + d
    return t - d, t


def day_metrics(calls: list[dict], c_date: date, today: date) -> dict:
    """One caller, one day. `calls`: dicts with status, call_duration, start, end (IST or None), src, client_number, reason.
    Ported from process_metrics_logic; numbers add up across days exactly as the Streamlit loop does."""
    ans = sum(1 for c in calls if str(c["status"]).lower() == "answered")
    miss = sum(1 for c in calls if str(c["status"]).lower() == "missed")
    q = [c for c in calls if c["call_duration"] >= QUALIFY]
    out = {
        "total_calls": len(calls), "answered": ans, "missed": miss, "calls_3m": len(q),
        "calls_15_20": sum(1 for c in calls if 900 <= c["call_duration"] < 1200),
        "calls_20p": sum(1 for c in calls if c["call_duration"] >= 1200),
        "dur_3m": int(sum(c["call_duration"] for c in q)),
        "io_line": None, "break_line": None, "break_timing": None, "issues": [], "break_sec": 0, "prod_sec": DAY_SEC,
        "n_acefone": sum(1 for c in calls if c["src"] == "A"), "n_ozonetel": sum(1 for c in calls if c["src"] == "O"),
        "n_mcube": sum(1 for c in calls if c["src"] == "M"), "n_manual": sum(1 for c in calls if c["src"] == "N"),
        "manual_dur": int(sum(c["call_duration"] for c in calls if c["src"] == "N")),
        "approvals": [], "unique_leads": len({c["client_number"] for c in calls if c.get("client_number") not in (None, "")}),
    }
    seen = {}
    for c in calls:
        if c["src"] == "N" and c.get("reason") is not None:
            v = str(c["reason"]).strip()
            if v and v.lower() not in seen:
                seen[v.lower()] = v
    out["approvals"] = list(seen.values())

    timed = sorted((c for c in calls if c["start"] is not None), key=lambda c: c["start"])
    if not timed:
        return out   # untimed day (manual calls only): full 10 hours, no checks (as in Streamlit)
    first = min(c["start"] for c in timed)
    last = max(c["end"] for c in timed)
    out["io_line"] = f"{c_date.strftime('%d/%m')}: In {first.strftime('%I:%M %p')} · Out {last.strftime('%I:%M %p')}"
    start_office = IST.localize(datetime.combine(c_date, OFFICE_START))
    end_office = IST.localize(datetime.combine(c_date, OFFICE_END))
    issues = []
    if first > IST.localize(datetime.combine(c_date, LATE_AFTER)):
        issues.append("Late Check-In")
    if last < end_office and c_date != today:
        issues.append("Early Check-Out")

    breaks, brk = [], 0.0
    if first > start_office:
        g = gap(start_office, first)
        if g >= MIN_GAP:
            breaks.append((start_office, first, g))
            brk += g
    for i in range(len(timed) - 1):
        s = max(timed[i]["end"], start_office)
        e = min(timed[i + 1]["start"], end_office)
        if e > s:
            g = gap(s, e)
            if g >= MIN_GAP:
                breaks.append((s, e, g))
                brk += g
    if c_date != today and last < end_office:
        g = gap(last, end_office)
        if g >= MIN_GAP:
            breaks.append((last, end_office, g))
            brk += g
    if c_date == today:
        brk += gap(last, end_office)   # time still to come today (Streamlit adds it, may be negative after 8 PM)
    if breaks:
        shown = sum(b[2] for b in breaks)
        out["break_line"] = f"{c_date.strftime('%d/%m')}: {len(breaks)} breaks : {fmt_hm(shown)}"
        t = f"{c_date.strftime('%d/%m')}:"
        for s, e, g in breaks:
            t += f"\n  {s.strftime('%I:%M %p')}→{e.strftime('%I:%M %p')} ({fmt_hm(g)})"
        out["break_timing"] = t
    prod = DAY_SEC - brk
    if len(q) < 40:
        issues.append("Low Calls")
    if out["dur_3m"] < 11700:
        issues.append("Low Duration")
    if len(breaks) > 2:
        issues.append("Excessive Breaks")
    if prod < 18000:
        issues.append("Less Productive")
    out.update({"issues": issues, "break_sec": int(round(brk)), "prod_sec": int(round(prod))})
    return out


# ---------------------------------------------------------------- stage changes (legacy/stage_changed_truncate.py)

STAGE_DEDUP = ["ProspectID", "Old_Contact_Stage", "New_Contact_Stage", "Stage Changed By", "Stage_Changed_On"]


def stage_kind(new_stage) -> str | None:
    """Counted the Streamlit way: contains Discovery / Roadmap / Follow Up (any case)."""
    s = str(new_stage or "").lower()
    return "discovery" if "discovery" in s else "roadmap" if "roadmap" in s else "followup" if "follow up" in s else None


def clean_phone(v) -> str:
    return str(v).replace("+91", "").replace("-", "").strip()[-10:]
