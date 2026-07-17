import requests
import os
import time
import pandas as pd
import pytz
from datetime import datetime, timedelta
from google.cloud import bigquery

# ---------------- CONFIG ----------------
PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "acefone_calls"
LOG_TABLE = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"
TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"

ACE_API_KEY = os.getenv("ACEFONE_API_KEY")
ACCOUNT_ID = os.getenv("ACEFONE_ACCOUNT_ID")

# Fetch tuning (prevents 504 gateway timeouts on heavy windows)
CHUNK_HOURS = 2      # fetch window sliced into chunks of this size; drop to 1 if 504s persist
PAGE_LIMIT = 500     # records per page (was 1000 - too heavy for peak hours)
MAX_RETRIES = 3      # retry attempts per page before failing loudly

india = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()

# ---------------- HELPERS ----------------

def safe_duration(x):
    try:
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h*3600 + m*60 + s
        return int(x or 0)
    except:
        return 0

# ---------------- INCREMENTAL LOGIC ----------------

def get_last_call_time():
    try:
        query = f"SELECT MAX(call_datetime) AS last_time FROM `{TABLE_ID}` WHERE source = 'Acefone'"
        df = client.query(query).to_dataframe()
        last_time_utc = df.iloc[0]["last_time"]

        if pd.isna(last_time_utc):
            return datetime.now(india) - timedelta(days=2)

        if last_time_utc.tzinfo is None:
            last_time_utc = pytz.utc.localize(last_time_utc)

        result = last_time_utc.astimezone(india) - timedelta(minutes=30)
        print(f"Watermark from BQ: {last_time_utc} UTC -> fetching from {result} IST")
        return result

    except Exception as e:
        print(f"Error fetching last time: {e}")
        return datetime.now(india) - timedelta(days=2)

# ---------------- FETCH ACEFONE (CHUNKED) ----------------

def fetch_acefone(last_time_ist):
    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID
    }

    # Apply 5-minute lag for accurate durations
    batch_now_utc = datetime.now(pytz.utc)
    fetch_to_ist = datetime.now(india) - timedelta(minutes=5)

    rows = []
    processed_ids = set()

    print(f"Fetching {last_time_ist} -> {fetch_to_ist} in {CHUNK_HOURS}h chunks (5-min lag applied)")

    chunk_start = last_time_ist
    while chunk_start < fetch_to_ist:
        chunk_end = min(chunk_start + timedelta(hours=CHUNK_HOURS), fetch_to_ist)
        page = 1
        page_ids_prev = set()

        while True:
            params = {
                "from_date": chunk_start.strftime("%Y-%m-%d %H:%M:%S"),
                "to_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                "page": page,
                "limit": PAGE_LIMIT
            }

            data = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    res = requests.get(
                        "https://api.acefone.in/v1/call/records",
                        headers=headers, params=params, timeout=120
                    )
                    if res.status_code == 200:
                        data = res.json().get("results", [])
                        break
                    print(f"[{chunk_start:%d-%b %H:%M}-{chunk_end:%H:%M}] page {page}: "
                          f"HTTP {res.status_code} (attempt {attempt}) {res.text[:200]}")
                except requests.RequestException as e:
                    print(f"[{chunk_start:%d-%b %H:%M}-{chunk_end:%H:%M}] page {page}: "
                          f"exception (attempt {attempt}): {e}")
                time.sleep(5 * attempt)

            if data is None:
                print("ACEFONE FETCH FAILED after all retries - aborting so the Action goes RED.")
                raise SystemExit(1)

            if not data:
                break

            current_ids = {str(call.get("call_id")) for call in data}
            if current_ids == page_ids_prev:
                break  # API repeating the same page
            page_ids_prev = current_ids

            for call in data:
                cid = str(call.get("call_id"))
                if cid in processed_ids:
                    continue  # boundary overlap between chunks
                processed_ids.add(cid)

                dt_raw = pd.to_datetime(call.get("end_stamp"), errors="coerce")
                if pd.isna(dt_raw):
                    continue

                dt_ist = india.localize(dt_raw)

                # Agent Logic
                agent = None
                if call.get("agent_name"):
                    agent = call["agent_name"]
                elif call.get("missed_agents"):
                    missed = call.get("missed_agents")
                    agent = missed[0].get("name") if missed and isinstance(missed, list) else None
                else:
                    for flow in call.get("call_flow", []):
                        if flow.get("type") == "Agent":
                            agent = flow.get("name")
                            break

                # Clean Agent Name
                clean_owner = str(agent or "").replace("-Extension", "").strip()

                rows.append({
                    "client_number": str(call.get("client_number",""))[-10:],
                    "call_datetime": dt_ist.astimezone(pytz.utc),
                    "call_owner": clean_owner,
                    "call_duration": safe_duration(call.get("call_duration")),
                    "status": call.get("status"),
                    "call_id": "ACE_" + cid,
                    "direction": call.get("direction"),
                    "service": call.get("service"),
                    "reason": call.get("reason"),
                    "call_type": (call.get("dialer_call_details") or {}).get("call_type"),
                    "source": "Acefone",
                    "updated_at": batch_now_utc,
                    "updated_at_ampm": fetch_to_ist.strftime("%d/%m/%Y %I:%M %p"),
                    "Call Date": dt_ist.date()
                })

            if len(data) < PAGE_LIMIT:
                break
            page += 1
            if page > 50:
                break

        print(f"  chunk {chunk_start:%d-%b %H:%M} -> {chunk_end:%H:%M}: cumulative rows = {len(rows)}")
        chunk_start = chunk_end

    return pd.DataFrame(rows), last_time_ist

# ---------------- LOAD & LOG ----------------

def load_to_bigquery(df):
    if df.empty: return

    df = df.sort_values("call_datetime", ascending=False)
    df = df.drop_duplicates(subset=["call_id"], keep="first")

    df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True)
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True)
    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)

    for col in df.columns:
        if df[col].dtype == "object" and col not in ["updated_at", "call_datetime", "Call Date"]:
            df[col] = df[col].astype(str)

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df, TABLE_ID, job_config=job_config).result()

def log_pipeline(pipeline_name, rows):
    ist_now = datetime.now(india)
    log_data = [{
        "run_date": ist_now.date(),
        "pipeline_name": pipeline_name,
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows)
    }]
    client.load_table_from_dataframe(pd.DataFrame(log_data), LOG_TABLE).result()

# ---------------- MAIN ----------------

def run_pipeline():
    last_time_ist = get_last_call_time()
    ace_df, start_range = fetch_acefone(last_time_ist)

    print(f"Total fetched: {len(ace_df)} rows")

    if ace_df.empty:
        log_pipeline("ACEFONE", 0)
        return

    # Filter for dashboard accuracy
    ace_df = ace_df[
        (ace_df["direction"] == "outbound") |
        ((ace_df["direction"] == "inbound") & (ace_df["status"].str.lower() == "answered"))
    ]
    ace_df = ace_df[~((ace_df["status"].str.lower() == "answered") & (ace_df["call_duration"] == 0))]
    print(f"After filter: {len(ace_df)} rows")
    if ace_df.empty:
        log_pipeline("ACEFONE", 0)
        return

    # Deduplication
    start_ts_query = start_range.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    query_bq = f"SELECT call_id FROM `{TABLE_ID}` WHERE call_datetime >= TIMESTAMP('{start_ts_query}') AND source = 'Acefone'"

    try:
        bq_ids = client.query(query_bq).to_dataframe()["call_id"].unique().tolist()
    except:
        bq_ids = []

    new_records = ace_df[~ace_df["call_id"].isin(bq_ids)]
    print(f"New unique records to insert: {len(new_records)}")

    if not new_records.empty:
        load_to_bigquery(new_records)
        log_pipeline("ACEFONE", len(new_records))
    else:
        print("No new unique records found.")
        log_pipeline("ACEFONE", 0)

if __name__ == "__main__":
    run_pipeline()
