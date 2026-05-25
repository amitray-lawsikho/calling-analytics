import requests
import os
import pandas as pd
import pytz
import time
from datetime import datetime, timedelta
from google.cloud import bigquery

# ---------------- CONFIG ----------------
PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "mcube_calls"
TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"
LOG_TABLE = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"

API_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
MCUBE_TOKEN = os.getenv("MCUBE_TOKEN")

SLEEP_BETWEEN_CALLS = 21

MAX_PAGES = 200

india = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()

# ---------------- HELPERS ----------------

def time_to_seconds(t):
    try:
        if t is None or pd.isna(t):
            return 0
        h, m, s = map(int, str(t).split(":"))
        return h * 3600 + m * 60 + s
    except Exception:
        return 0

def _dedup(rows):
    """Drop duplicate records by callid, preserving order."""
    seen, out = set(), []
    for r in rows:
        cid = str(r.get("callid"))
        if cid and cid not in seen:
            seen.add(cid)
            out.append(r)
    return out

# ---------------- INCREMENTAL LOGIC ----------------

def get_last_call_time():
    """
    Resume point = latest call_datetime already in BQ, rewound 30 minutes.
    The 30-min rewind plus the deduplication steps guarantee that records
    near the previous run's boundary are re-pulled and then dropped as
    duplicates -- nothing at the edge is ever missed.
    """
    try:
        q = f"SELECT MAX(call_datetime) AS last_time FROM `{TABLE_ID}` WHERE source = 'Mcube'"
        df = client.query(q).to_dataframe()
        last_utc = df.iloc[0]["last_time"]

        if pd.isna(last_utc):
            return datetime.now(india) - timedelta(days=2)

        if last_utc.tzinfo is None:
            last_utc = pytz.utc.localize(last_utc)

        return last_utc.astimezone(india) - timedelta(minutes=30)

    except Exception as e:
        print(f"Error fetching last time: {e}")
        return datetime.now(india) - timedelta(days=2)

# ---------------- FETCH MCUBE ----------------

def _api_call(start_ist, end_ist, page=1):
    payload = {
        "start_date": start_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "end_date":   end_ist.strftime("%Y-%m-%d %H:%M:%S"),
        "Authorization": MCUBE_TOKEN,
        "page": page,
    }
    headers = {"Content-Type": "application/json"}

    try:
        res = requests.post(API_URL, headers=headers, json=payload, timeout=60)

        if res.status_code == 429:
            print("  Rate limited. Sleeping 65s...")
            time.sleep(65)
            return _api_call(start_ist, end_ist, page=page)

        if res.status_code != 200:
            print(f"  HTTP {res.status_code}: {res.text[:200]}")
            return []

        data = res.json()
        if data.get("status") != "succ":
            return []

        msg = data.get("msg") or []
        if not isinstance(msg, list):
            return []
        return msg

    except Exception as e:
        print(f"  Error {start_ist} -> {end_ist} (page {page}): {e}")
        return []

def fetch_window_paginated(start_ist, end_ist):
    all_rows = []
    seen_ids = set()
    page = 1

    while True:
        print(f"Fetching {start_ist} -> {end_ist} page={page}")
        msg = _api_call(start_ist, end_ist, page=page)

        if not msg:
            print(f"  Page {page}: empty response, pagination complete.")
            break

        # Count new vs dupe before extending
        new_rows = []
        dupes = 0
        for r in msg:
            cid = str(r.get("callid"))
            if not cid:
                continue
            if cid in seen_ids:
                dupes += 1
            else:
                seen_ids.add(cid)
                new_rows.append(r)

        print(f"  Page {page}: {len(msg)} records "
              f"({len(new_rows)} new, {dupes} dupes).")

        if not new_rows:
            print(f"  Page {page}: no new records, pagination complete.")
            break

        all_rows.extend(new_rows)

        if page >= MAX_PAGES:
            print(f"  WARNING: Hit MAX_PAGES cap of {MAX_PAGES} for window "
                  f"{start_ist} -> {end_ist}. Window may contain more data "
                  f"than fetched. Investigate immediately.")
            break

        page += 1
        time.sleep(SLEEP_BETWEEN_CALLS)

    return all_rows
# ---------------- TRANSFORM ----------------

def process_records(rows):
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    out = pd.DataFrame()
    out["call_id"] = "MCB_" + df["callid"].astype(str)

    out["direction"] = df["direction"].astype(str).str.strip().str.lower()
    out["dialstatus"] = df["dialstatus"].astype(str).str.upper()
    out["status"] = out["dialstatus"].apply(
        lambda x: "answered" if x == "ANSWER" else "missed"
    )

    out["call_owner"] = df["agentname"].fillna("").astype(str).str.strip()

    # Customer's number -- last 10 of `callto` for both directions in this API
    out["client_number"] = df["callto"].fillna("").astype(str).str[-10:]

    # Talk time (matches acefone's call_duration semantics)
    # Times -- API returns IST naive
    start_dt = pd.to_datetime(df["starttime"], errors="coerce")
    end_dt   = pd.to_datetime(df["endtime"],   errors="coerce")

    # Talk time = endtime - starttime (in seconds)
    duration_delta = (end_dt - start_dt).dt.total_seconds()
    out["call_duration"] = (
        pd.to_numeric(duration_delta, errors="coerce")
          .fillna(0)
          .clip(lower=0)
          .astype(int)
    )

    out["call_datetime"] = start_dt.apply(
        lambda x: india.localize(x).astimezone(pytz.utc) if pd.notna(x) else pd.NaT
    )
    out["Call Date"] = start_dt.dt.date

    out["source"] = "Mcube"
    out["reason"] = df["dialstatus"].fillna("").astype(str)
    out["groupname"] = df.get("groupname", "").fillna("").astype(str)
    out["mcube_source"] = df.get("source", "").fillna("").astype(str)  # Dialer / Api / Callback
    out["recording_url"] = df.get("filename", "").fillna("").astype(str)

    now_utc = datetime.now(pytz.utc)
    now_ist = datetime.now(india)
    out["updated_at"] = now_utc
    out["updated_at_ampm"] = now_ist.strftime("%d/%m/%Y %I:%M %p")

    out = out[out["call_datetime"].notna()].copy()
    return out

# ---------------- LOAD & LOG ----------------

def load_to_bigquery(df):
    if df.empty:
        return

    df = df.sort_values("call_datetime", ascending=False)
    df = df.drop_duplicates(subset=["call_id"], keep="first")

    df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True)
    df["updated_at"]    = pd.to_datetime(df["updated_at"],    utc=True)
    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)
    df["Call Date"]     = pd.to_datetime(df["Call Date"]).dt.date

    for col in df.columns:
        if df[col].dtype == "object" and col not in ["updated_at", "call_datetime", "Call Date"]:
            df[col] = df[col].astype(str)

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_APPEND",
        schema=[
            bigquery.SchemaField("call_datetime", "TIMESTAMP"),
            bigquery.SchemaField("Call Date",     "DATE"),
            bigquery.SchemaField("updated_at",    "TIMESTAMP"),
            bigquery.SchemaField("call_duration", "INT64"),
        ],
    )
    client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config).result()

def log_pipeline(rows):
    ist_now = datetime.now(india)
    log_data = [{
        "run_date": ist_now.date(),
        "pipeline_name": "MCUBE",
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows),
    }]
    client.load_table_from_dataframe(pd.DataFrame(log_data), LOG_TABLE).result()

# ---------------- MAIN ----------------

def run_pipeline():
    last_time_ist = get_last_call_time()
    end_time_ist  = datetime.now(india) - timedelta(minutes=5)   # 5-min lag

    if last_time_ist >= end_time_ist:
        print("Already up to date.")
        log_pipeline(0)
        return

    print(f"Fetching {last_time_ist} -> {end_time_ist} (5-min lag)")

    all_rows = fetch_window_paginated(last_time_ist, end_time_ist)

    if not all_rows:
        print("No records returned by Mcube.")
        log_pipeline(0)
        return

    all_rows = _dedup(all_rows)

    df = process_records(all_rows)
    if df.empty:
        log_pipeline(0)
        return

    df = df[~df["reason"].astype(str).str.strip().str.upper().str.contains("CANCEL", na=False)]
    start_ts = last_time_ist.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
    q = (
        f"SELECT call_id FROM `{TABLE_ID}` "
        f"WHERE call_datetime >= TIMESTAMP('{start_ts}') AND source = 'Mcube'"
    )
    try:
        bq_ids = client.query(q).to_dataframe()["call_id"].unique().tolist()
    except Exception:
        bq_ids = []

    new_records = df[~df["call_id"].isin(bq_ids)]

    if not new_records.empty:
        load_to_bigquery(new_records)
        print(f"Inserted {len(new_records)} rows into {TABLE_ID}.")
        log_pipeline(len(new_records))
    else:
        print("No new unique records found.")
        log_pipeline(0)

if __name__ == "__main__":
    run_pipeline()
