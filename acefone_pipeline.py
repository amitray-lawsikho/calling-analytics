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

    while True:

        params = {
            "from_date": last_time.strftime("%Y-%m-%d %H:%M:%S"),
            "to_date": datetime.now(india).strftime("%Y-%m-%d %H:%M:%S"),
            "page": page,
            "limit": 1000
        }

        res = requests.get(
            "https://api.acefone.in/v1/call/records",
            headers=headers,
            params=params
        )

        if res.status_code != 200:
            print("❌ API error:", res.status_code)
            break

        data = res.json().get("results", [])

        if not data:
            print("✅ No more data")
            break

        current_ids = {str(call.get("call_id")) for call in data}

        # 🔥 STOP duplicate page loop
        if current_ids.issubset(seen_ids):
            print("⚠️ Duplicate page detected, stopping")
            break

        seen_ids.update(current_ids)

        for call in data:
            dt = pd.to_datetime(call.get("end_stamp"), utc=True, errors="coerce")

            if pd.isna(dt):
                continue

            rows.append({
                "client_number": str(call.get("client_number",""))[-10:],
                "call_datetime": dt,
                "call_owner": str(call.get("agent_name","")).strip(),
                "call_duration": safe_duration(call.get("call_duration")),
                "status": call.get("status"),
                "call_id": "ACE_" + str(call.get("call_id")),
                "direction": call.get("direction"),
                "service": call.get("service"),
                "reason": call.get("reason"),
                "call_type": (call.get("dialer_call_details") or {}).get("call_type"),
                "source": "Acefone",
                "updated_at": datetime.utcnow(),
                "updated_at_ampm": datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
            })

        print(f"📄 Page {page}: {len(data)} records")

        if len(data) < 1000:
            print("✅ Last page reached")
            break

        page += 1

        if page > 50:
            print("⚠️ Max page limit reached")
            break

    expected_cols = [
        "client_number","call_datetime","call_owner","call_duration",
        "status","call_id","direction","service","reason",
        "call_type","source","updated_at","updated_at_ampm"
    ]
    
    df = pd.DataFrame(rows, columns=expected_cols)

    if not df.empty:
        df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True, errors="coerce")
        df = df[df["call_datetime"].notna()]

    return df

# ---------------- LOAD ----------------
def load_to_bigquery(df):

    if df.empty:
        return

    df = df.copy()

    # 🔥 KEEP ONLY TRUE DUPES FIXED
    df = df.drop_duplicates(subset=["call_id", "call_datetime"])

    df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True, errors="coerce")
    df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")

    df = df[df["call_datetime"].notna()]

    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)

    for col in df.columns:
        if df[col].dtype == "object" and col not in ["updated_at", "call_datetime"]:
            df[col] = df[col].astype(str)

    client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    ).result()

# ---------------- MAIN ----------------
def run_pipeline():

    last_time = get_last_call_time()
    print("⏱ Fetching from:", last_time)

    ace_df = fetch_acefone(last_time)
    bq_df = fetch_recent_bq_data(last_time)
    
    if ace_df.empty:
        print("⚠️ No data fetched from API")
        log_pipeline("ACEFONE", 0)
        return
    
    combined_df = pd.concat([bq_df, ace_df], ignore_index=True)
    combined_df = combined_df.drop_duplicates(subset=["call_id", "call_datetime"])

    if "call_id" not in ace_df.columns:
        log_pipeline("ACEFONE", 0)
        return
    
    new_data = combined_df[combined_df["call_id"].isin(ace_df["call_id"])]

    rows_count = len(new_data)
    print("New records to insert:", rows_count)

    load_to_bigquery(new_data)

    log_pipeline("ACEFONE", rows_count)
    print("✅ Acefone pipeline completed")

if __name__ == "__main__":
    run_pipeline()
