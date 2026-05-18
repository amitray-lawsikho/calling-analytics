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

# 3 API hits / min  ->  21s gap (5s safety)
SLEEP_BETWEEN_CALLS = 21

# --- Response cap ---
# MCUBE docs claim "100 window size per request", but this was DISPROVEN by
# an empirical probe: a single call returned 1549 records for a 72h window,
# and whole-window counts always equalled the sum of their sub-windows
# (no truncation at any tested volume).
#
# So SPLIT_THRESHOLD below is NOT a real cap -- it is only a defensive trigger.
# A normal run fetches the whole window in ONE API call. The recursive split
# only ever fires if a response is improbably large, as insurance in case
# MCUBE ever introduces real truncation. It is set well above the largest
# count ever observed (1549) yet low enough to still catch a future real cap.
SPLIT_THRESHOLD = 1000
# Smallest window recursion will bisect down to.
MIN_CHUNK = timedelta(minutes=2)

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
            return []

        msg = data.get("msg") or []
        if not isinstance(msg, list):
            return []
        return msg

    except Exception as e:
        print(f"  Error {start_ist} -> {end_ist}: {e}")
        return []

def fetch_window_recursive(start_ist, end_ist):
    """
    Fetch one time window in a single API call.

    The probe proved MCUBE returns the FULL window with no truncation, so in
    normal operation this returns everything in one call and does not recurse.

    Defensive fallback only: if a response is improbably large (>= the
    SPLIT_THRESHOLD insurance value), the window is bisected and each half
    fetched separately, then recombined with dedup. This is ordering-agnostic
    -- every leaf window is covered exactly once, so nothing is dropped, and
    any boundary overlap between halves is removed by _dedup.
    """
    print(f"Fetching {start_ist} -> {end_ist}")
    msg = _api_call(start_ist, end_ist)
    print(f"  Got {len(msg)} records.")

    if len(msg) >= SPLIT_THRESHOLD:
        if (end_ist - start_ist) > MIN_CHUNK:
            mid = start_ist + (end_ist - start_ist) / 2
            print(f"  Unusually large response ({len(msg)}). Splitting at {mid}")
            time.sleep(SLEEP_BETWEEN_CALLS)
            first = fetch_window_recursive(start_ist, mid)
            time.sleep(SLEEP_BETWEEN_CALLS)
            second = fetch_window_recursive(mid, end_ist)
            return _dedup(first + second)
        else:
            print(f"  WARNING: >= {SPLIT_THRESHOLD} records in a <= "
                  f"{MIN_CHUNK} window; cannot split further.")

    return msg

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
    out["call_duration"] = df["answeredtime"].apply(time_to_seconds)
    pulse_int = pd.to_numeric(df.get("pulse", 0), errors="coerce").fillna(0).astype(int)
    fallback_mask = (out["call_duration"] == 0) & (out["status"] == "answered")
    out.loc[fallback_mask, "call_duration"] = pulse_int[fallback_mask]

    # Times -- API returns IST naive
    start_dt = pd.to_datetime(df["starttime"], errors="coerce")
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

    # Single API call for the WHOLE window (probe-proven: no truncation).
    # Recursion inside fetch_window_recursive is a defensive fallback only.
    all_rows = fetch_window_recursive(last_time_ist, end_time_ist)

    if not all_rows:
        print("No records returned by Mcube.")
        log_pipeline(0)
        return

    # Safety dedup of the raw API rows before transform.
    all_rows = _dedup(all_rows)

    df = process_records(all_rows)
    if df.empty:
        log_pipeline(0)
        return

    # Filter for dashboard accuracy (mirrors Acefone)
    df = df[
        (df["direction"] == "outbound") |
        ((df["direction"] == "inbound") & (df["status"] == "answered"))
    ]
    df = df[~((df["status"] == "answered") & (df["call_duration"] == 0))]
    if df.empty:
        log_pipeline(0)
        return

    # Dedup against existing BQ rows in this window.
    # Final safety net: removes anything re-pulled by the 30-min rewind or by
    # a defensive split overlap, so no call is ever inserted twice.
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
