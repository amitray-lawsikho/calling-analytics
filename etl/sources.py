"""Reading the sources: dialler APIs (incremental), Google Sheets (manual calls, stage changes, team sheet) and the
one-time BigQuery copy. API logic follows legacy/*_pipeline.py; transforms are in etl/rules.py."""
from __future__ import annotations

import io
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from etl import config as C
from etl import suspicious as SUS
from etl.rules import IST, UTC, STAGE_DEDUP, acefone_row, clean_phone, hid, mcube_row, ozonetel_direction, ozonetel_row, ozonetel_status


def read_csv(url: str, **kw) -> pd.DataFrame:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.content.decode("utf-8")), **kw)


# ---------------------------------------------------------------- team sheet

def team_map() -> list[dict]:
    """Caller Name -> Team Name, Vertical, Analyst; key = lower-cased, trimmed Caller Name (first row wins)."""
    df = read_csv(C.TEAM_CSV_URL)
    df.columns = df.columns.str.strip()
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        name = r.get("Caller Name")
        if pd.isna(name) or not str(name).strip():
            continue
        k = str(name).strip().lower()
        if k in out:
            continue
        val = lambda c: None if c not in df.columns or pd.isna(r.get(c)) else str(r.get(c))
        out[k] = {"merge_key": k, "caller_name": str(name), "team_name": val("Team Name"), "vertical": val("Vertical"), "analyst": val("Analyst")}
    return list(out.values())


def ozonetel_agents() -> set[str]:
    df = read_csv(C.OZONETEL_TEAM_URL)
    return {str(x).strip().lower() for x in df.iloc[:, 2].dropna().unique()}


# ---------------------------------------------------------------- Acefone (2-hour chunks, 500 per page)

def fetch_acefone(since_ist: datetime, ampm: str, log, until: datetime | None = None) -> list[dict]:
    headers = {"Authorization": f"Bearer {os.environ['ACEFONE_API_KEY']}", "accountid": os.environ["ACEFONE_ACCOUNT_ID"]}
    until = until or datetime.now(IST) - timedelta(minutes=C.LAG_MIN)
    rows, seen = [], set()
    start = since_ist
    while start < until:
        end = min(start + timedelta(hours=2), until)
        page, prev = 1, set()
        while True:
            params = {"from_date": start.strftime("%Y-%m-%d %H:%M:%S"), "to_date": end.strftime("%Y-%m-%d %H:%M:%S"), "page": page, "limit": 500}
            data = None
            for attempt in range(1, 4):
                try:
                    res = requests.get(C.ACEFONE_URL, headers=headers, params=params, timeout=120)
                    if res.status_code == 200:
                        data = res.json().get("results", [])
                        break
                    log(f"Acefone {start:%d %b %H:%M} page {page}: HTTP {res.status_code} (try {attempt})")
                except requests.RequestException as e:
                    log(f"Acefone {start:%d %b %H:%M} page {page}: {type(e).__name__} (try {attempt})")
                time.sleep(5 * attempt)
            if data is None:
                raise RuntimeError("Acefone API failed after 3 tries")
            if not data:
                break
            ids = {str(c.get("call_id")) for c in data}
            if ids == prev:
                break
            prev = ids
            for call in data:
                cid = str(call.get("call_id"))
                if cid in seen:
                    continue
                seen.add(cid)
                SUS.keep("A", call)
                r = acefone_row(call, ampm)
                if r:
                    rows.append(r)
            if len(data) < 500 or page >= 50:
                break
            page += 1
        start = end
    return rows


# ---------------------------------------------------------------- MCube (21 s between pages: API limit)

def fetch_mcube(since_ist: datetime, ampm: str, log, until: datetime | None = None) -> list[dict]:
    until = until or datetime.now(IST) - timedelta(minutes=C.LAG_MIN)
    if since_ist >= until:
        return []
    rows, seen, page = [], set(), 1
    while True:
        payload = {"start_date": since_ist.strftime("%Y-%m-%d %H:%M:%S"), "end_date": until.strftime("%Y-%m-%d %H:%M:%S"),
                   "Authorization": os.environ["MCUBE_TOKEN"], "page": page}
        msg = None
        for attempt in range(1, 6):
            try:
                res = requests.post(C.MCUBE_URL, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
                if res.status_code == 429:
                    time.sleep(65)
                    continue
                if res.status_code != 200:
                    log(f"MCube page {page}: HTTP {res.status_code} (try {attempt})")
                    time.sleep(10 * attempt)
                    continue
                data = res.json()
                msg = data.get("msg") if data.get("status") == "succ" and isinstance(data.get("msg"), list) else []
                break
            except (requests.RequestException, ValueError) as e:
                log(f"MCube page {page}: {type(e).__name__} (try {attempt})")
                time.sleep(10 * attempt)
        if msg is None:
            raise RuntimeError("MCube API failed after 5 tries")
        new = [r for r in msg if str(r.get("callid")) and str(r.get("callid")) not in seen]
        if not new:
            break
        for r in new:
            seen.add(str(r.get("callid")))
            SUS.keep("M", r)
            x = mcube_row(r, ampm)
            if x:
                rows.append(x)
        if page >= 200:
            log("MCube: hit 200 pages, window may be incomplete")
            break
        page += 1
        time.sleep(21)
    return rows


# ---------------------------------------------------------------- Ozonetel (whole days, 2 requests a minute)

def fetch_ozonetel(since_day: date, ampm: str, agents_lower: set[str], log) -> list[dict]:
    headers = {"Content-Type": "application/json", "apiKey": os.environ["OZONETEL_API_KEY"]}
    today = datetime.now(IST).date()
    rows, day = [], since_day
    while day <= today:
        details, page = [], 1
        while True:
            payload = {"fromDate": f"{day} 00:00:00", "toDate": f"{day} 23:59:59", "userName": os.environ["OZONETEL_USERNAME"]}
            try:
                res = requests.get(C.OZONETEL_URL, headers=headers, json=payload, params={"pageNo": page, "pageSize": 500}, timeout=60)
                if res.status_code == 429:
                    time.sleep(65)
                    continue
                data = res.json()
            except (requests.RequestException, ValueError) as e:
                raise RuntimeError(f"Ozonetel {day} page {page}: {type(e).__name__}") from e
            got = data.get("details", []) or []
            total = int(data.get("totalCount", 0) or 0)
            if not got:
                break
            details.extend(got)
            if len(details) >= total or len(got) < 500:
                break
            page += 1
            time.sleep(32)
        rows += [r for d in details if (r := ozonetel_row(d, day, agents_lower, ampm))]
        day += timedelta(days=1)
        if day <= today:
            time.sleep(32)
    return rows


# ---------------------------------------------------------------- manual calls sheet (legacy/manual_calls_pipeline.py)

MANUAL_COLS = {
    "Timestamp": "Timestamp", "Date": "Manual_Date", "Counselor Name (Refer your LSQ name)": "call_owner",
    "Duration of the call in minutes(for a single call only)": "call_duration",
    "Phone Number of Submitted Manual (Numbers will be audited kindly provide accurate details)": "client_number",
    "Approved By": "Approved_By",
}


def manual_rows(df: pd.DataFrame, keep_from: date, today: date) -> list[dict]:
    df = df.copy()
    df.columns = df.columns.str.replace(r"\s+", " ", regex=True).str.strip()
    df = df[list(MANUAL_COLS)].rename(columns=MANUAL_COLS)
    df["call_owner"] = df["call_owner"].astype(str).str.title().replace(["Nan", "None", ""], "N/A").str.strip()
    ts = pd.to_datetime(df["Timestamp"], dayfirst=True, errors="coerce")
    md = pd.to_datetime(df["Manual_Date"], dayfirst=True, errors="coerce")
    df["Call_Date"] = md.fillna(ts).dt.date
    df = df[df["Call_Date"].notna()]
    df = df[(df["Call_Date"] >= keep_from) & (df["Call_Date"] <= today)].copy()
    df["call_duration"] = (pd.to_numeric(df["call_duration"], errors="coerce").fillna(0) * 60).astype(int)
    df["client_number"] = df["client_number"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    out = []
    for i, r in df.iterrows():
        ap = None if pd.isna(r["Approved_By"]) else str(r["Approved_By"])
        out.append({"id": hid("MAN", i, r["Timestamp"], r["Call_Date"], r["call_owner"], r["call_duration"], r["client_number"], ap),
                    "call_date": r["Call_Date"].isoformat(), "call_owner": r["call_owner"], "call_duration": int(r["call_duration"]),
                    "client_number": r["client_number"], "approved_by": ap})
    return out


# ---------------------------------------------------------------- LSQ stage changes sheet (legacy/stage_changed_truncate.py)

def stage_rows(df: pd.DataFrame, keep_from: date) -> list[dict]:
    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    df["Timestamp"] = df["Timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="NaT")
    df = df.dropna(subset=["Timestamp"])
    keys = [k for k in STAGE_DEDUP if k in df.columns]
    for k in keys:
        df[k] = df[k].astype(str).str.strip()
    df = df.drop_duplicates(subset=keys, keep="last")
    df["_id"] = [hid("STG", *vals) for vals in df[keys].itertuples(index=False)]
    df["Date"] = df["Timestamp"].dt.tz_convert(IST).dt.date
    for c in ("Phone", "Alternate_Phone_Number"):
        if c in df.columns:
            df[c] = df[c].astype(str).map(clean_phone)
    df["LastName"] = df["Phone"]
    df = df.rename(columns={"Stage Changed By": "Stage_Changed_By", "BotComment": "StageChangeComment"})
    df = df[df["Date"] >= keep_from]
    g = lambda r, c: None if c not in r or pd.isna(r[c]) else str(r[c])
    out = []
    for _, r in df.iterrows():
        out.append({"id": int(r["_id"]), "date": r["Date"].isoformat(), "first_name": g(r, "FirstName"),
                    "last_name": g(r, "LastName"), "email": g(r, "EmailAddress"), "phone": g(r, "Phone"),
                    "alt_phone": g(r, "Alternate_Phone_Number"), "new_stage": g(r, "New_Contact_Stage"),
                    "changed_by": None if g(r, "Stage_Changed_By") is None else g(r, "Stage_Changed_By").strip(),
                    "comment": g(r, "StageChangeComment")})
    return out


# ---------------------------------------------------------------- one-time copy of the call history from BigQuery

def bigquery_history(keep_from: date, log) -> list[dict]:
    import json
    from google.cloud import bigquery
    from google.oauth2 import service_account

    info = json.loads(os.environ["BQ_CREDENTIALS"])
    bq = bigquery.Client(credentials=service_account.Credentials.from_service_account_info(info), project=C.BQ_PROJECT)
    t = f"`{C.BQ_PROJECT}.{C.BQ_DATASET}"
    out: list[dict] = []
    q = lambda sql: bq.query(sql).result(page_size=50000)
    s = lambda v: None if v is None else str(v)

    for r in q(f"SELECT call_id, client_number, call_datetime, call_owner, call_duration, status, direction, service, reason, "
               f"updated_at_ampm, `Call Date` d FROM {t}.acefone_calls` WHERE `Call Date` >= '{keep_from}'"):
        out.append({"id": hid("ACE", str(r["call_id"]).removeprefix("ACE_")), "src": "A", "client_number": s(r["client_number"]),
                    "call_datetime": r["call_datetime"], "call_duration": int(r["call_duration"] or 0), "status": s(r["status"]),
                    "direction": s(r["direction"]), "service": s(r["service"]), "reason": s(r["reason"]), "call_owner": s(r["call_owner"]),
                    "call_date": r["d"], "ampm": s(r["updated_at_ampm"])})
    log(f"BigQuery Acefone: {len(out)}")
    n0 = len(out)
    for r in q(f"SELECT call_id, client_number, call_datetime, call_owner, call_duration, status, direction, reason, updated_at_ampm, "
               f"`Call Date` d FROM {t}.mcube_calls` WHERE `Call Date` >= '{keep_from}' "
               f"AND (dialstatus IS NULL OR UPPER(TRIM(dialstatus)) != 'EXECUTIVE BUSY')"):
        out.append({"id": hid("MCB", str(r["call_id"]).removeprefix("MCB_")), "src": "M", "client_number": s(r["client_number"]),
                    "call_datetime": r["call_datetime"], "call_duration": int(r["call_duration"] or 0), "status": s(r["status"]),
                    "direction": s(r["direction"]), "service": None, "reason": s(r["reason"]), "call_owner": s(r["call_owner"]),
                    "call_date": r["d"], "ampm": s(r["updated_at_ampm"])})
    log(f"BigQuery MCube: {len(out) - n0}")
    n0 = len(out)
    for r in q(f"SELECT CAST(CallID AS STRING) cid, AgentName, phone_number, StartTime, EndTime, CallDate, duration_sec, Status, Type, "
               f"Disposition, updated_at_ampm FROM {t}.ozonetel_calls` WHERE CallDate >= '{keep_from}'"):
        st, en = r["StartTime"], r["EndTime"]
        out.append({"id": hid("OZO", r["cid"], st.astimezone(UTC).isoformat() if st else "", en.astimezone(UTC).isoformat() if en else ""),
                    "src": "O", "client_number": s(r["phone_number"]), "call_datetime": st, "call_duration": int(r["duration_sec"] or 0),
                    "status": ozonetel_status(r["Status"]), "direction": ozonetel_direction(r["Type"]), "service": None,
                    "reason": r["Disposition"], "call_owner": s(r["AgentName"]), "call_date": r["CallDate"], "ampm": s(r["updated_at_ampm"])})
    log(f"BigQuery Ozonetel: {len(out) - n0}")
    return out
