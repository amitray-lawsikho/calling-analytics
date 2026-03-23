import requests
import os
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

india = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()

# ---------------- LOG FUNCTION ----------------
def log_pipeline(pipeline_name, rows):
    ist_now = datetime.now(india)

    log_data = [{
        "run_date": ist_now.date(),
        "pipeline_name": pipeline_name,
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows)
    }]

    df_log = pd.DataFrame(log_data)

    client.load_table_from_dataframe(
        df_log,
        LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    ).result()

# ---------------- HELPERS ----------------
def safe_duration(x):
    try:
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h*3600 + m*60 + s
        return int(x)
    except:
        return 0

# ---------------- GET LAST TIME ----------------
def get_last_call_time():
    try:
        df = client.query(f"""
            SELECT MAX(call_datetime) AS last_time
            FROM `{TABLE_ID}`
            WHERE source = 'Acefone'
        """).to_dataframe()

        last_time = df.iloc[0]["last_time"]

        if pd.isna(last_time):
            return datetime.now(india) - timedelta(days=2)

        if last_time.tzinfo is None:
            last_time = pytz.utc.localize(last_time)

        return last_time.astimezone(india) - timedelta(minutes=30)

    except:
        return datetime.now(india) - timedelta(days=2)

# ---------------- FETCH RECENT BQ DATA ----------------
def fetch_recent_bq_data(last_time):
    query = f"""
        SELECT *
        FROM `{TABLE_ID}`
        WHERE call_datetime >= TIMESTAMP('{last_time.strftime("%Y-%m-%d %H:%M:%S")}')
        AND source = 'Acefone'
    """
    return client.query(query).to_dataframe()

# ---------------- FETCH ACEFONE ----------------
def fetch_acefone(last_time):
    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID
    }

    rows = []
    page = 1
    seen_ids = set()
    
    # Capture batch times
    batch_now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    batch_now_ist = datetime.now(india)

    while True:
        params = {
            "from_date": last_time.strftime("%Y-%m-%d %H:%M:%S"),
            "to_date": batch_now_ist.strftime("%Y-%m-%d %H:%M:%S"),
            "page": page,
            "limit": 1000
        }

        res = requests.get(
            "https://api.acefone.in/v1/call/records",
            headers=headers,
            params=params
        )

        if res.status_code != 200:
            break

        data = res.json().get("results", [])
        if not data:
            break

        current_ids = {str(call.get("call_id")) for call in data}
        if current_ids.issubset(seen_ids):
            break

        seen_ids.update(current_ids)

        for call in data:
            # Parse the UTC timestamp from API
            dt_utc = pd.to_datetime(call.get("end_stamp"), utc=True, errors="coerce")
            if pd.isna(dt_utc):
                continue

            # Clean Owner Name
            raw_owner = str(call.get("agent_name", "")).strip()
            clean_owner = raw_owner.replace("-Extension", "") if raw_owner.endswith("-Extension") else raw_owner

            rows.append({
                "client_number": str(call.get("client_number",""))[-10:],
                "call_datetime": dt_utc,
                "call_owner": clean_owner,
                "call_duration": safe_duration(call.get("call_duration")),
                "status": call.get("status"),
                "call_id": "ACE_" + str(call.get("call_id")),
                "direction": call.get("direction"),
                "service": call.get("service"),
                "reason": call.get("reason"),
                "call_type": (call.get("dialer_call_details") or {}).get("call_type"),
                "source": "Acefone",
                "updated_at": batch_now_utc,
                "updated_at_ampm": batch_now_ist.strftime("%d/%m/%Y %I:%M %p")
            })

        if len(data) < 1000:
            break
        page += 1
        if page > 50:
            break

    df = pd.DataFrame(rows)
    return df

# ---------------- LOAD ----------------
def load_to_bigquery(df):
    if df.empty:
        return

    df = df.copy()

    # Deduplicate: Keep latest call info per call_id
    df = df.sort_values("call_datetime", ascending=False)
    df = df.drop_duplicates(subset=["call_id"], keep="first")

    # --- THE TIMEZONE FIX ---
    # 1. Ensure call_datetime is datetime objects
    df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True)
    
    # 2. Localize to IST then extract the date
    # This ensures a call at 1:00 AM IST (which is previous day UTC) shows the correct IST date
    df["Call Date"] = df["call_datetime"].dt.tz_convert("Asia/Kolkata").dt.date
    
    # Standardize other fields
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True)
    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)

    # Cast objects to string for BQ compatibility
    for col in df.columns:
        if df[col].dtype == "object" and col not in ["updated_at", "call_datetime", "Call Date"]:
            df[col] = df[col].astype(str)

    client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    ).result()

# ---------------- MAIN ----------------
def run_pipeline():
    last_time = get_last_call_time()

    ace_df = fetch_acefone(last_time)
    bq_df = fetch_recent_bq_data(last_time)
    
    if ace_df.empty:
        log_pipeline("ACEFONE", 0)
        return
    
    # Combine fresh API data with recent BQ data to find updates/duplicates
    combined_df = pd.concat([bq_df, ace_df], ignore_index=True)
    
    # Remove duplicates from the combined set
    combined_df = combined_df.sort_values("call_datetime", ascending=False)
    combined_df = combined_df.drop_duplicates(subset=["call_id"], keep="first")

    # Only load the records that were actually present in the new Acefone fetch
    new_data = combined_df[combined_df["call_id"].isin(ace_df["call_id"])]

    rows_count = len(new_data)
    load_to_bigquery(new_data)
    log_pipeline("ACEFONE", rows_count)

if __name__ == "__main__":
    run_pipeline()
