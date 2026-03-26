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

        # Look back 30 mins from last record to catch any late API arrivals
        return last_time_utc.astimezone(india) - timedelta(minutes=30)

    except Exception as e:
        print(f"Error fetching last time: {e}")
        return datetime.now(india) - timedelta(days=2)

# ---------------- FETCH ACEFONE ----------------

def fetch_acefone(last_time_ist):
    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID
    }

    # SET THE 5-MINUTE LAG HERE
    batch_now_utc = datetime.now(pytz.utc)
    fetch_to_ist = datetime.now(india) - timedelta(minutes=5)
    
    rows = []
    page = 1
    seen_ids = set()

    print(f"Fetching from {last_time_ist} to {fetch_to_ist} (5-min lag applied)")

    while True:
        params = {
            "from_date": last_time_ist.strftime("%Y-%m-%d %H:%M:%S"),
            "to_date": fetch_to_ist.strftime("%Y-%m-%d %H:%M:%S"),
            "page": page,
            "limit": 1000
        }

        res = requests.get("https://api.acefone.in/v1/call/records", headers=headers, params=params)
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
            dt_raw = pd.to_datetime(call.get("end_stamp"), errors="coerce")
            if pd.isna(dt_raw): continue

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

            clean_owner = str(agent or "").replace("-Extension", "").strip()

            rows.append({
                "client_number": str(call.get("client_number",""))[-10:],
                "call_datetime": dt_ist.astimezone(pytz.utc), 
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
                "updated_at_ampm": fetch_to_ist.strftime("%d/%m/%Y %I:%M %p"), # Logs the "To" time
                "Call Date": dt_ist.date() 
            })

        if len(data) < 1000: break
        page += 1
        if page > 50: break

    return pd.DataFrame(rows), last_time_ist

# ---------------- MAIN ----------------

def run_pipeline():
    # 1. Get Start Time
    last_time_ist = get_last_call_time()

    # 2. Fetch with 5-minute lag
    ace_df, start_range = fetch_acefone(last_time_ist)
    
    if ace_df.empty:
        log_pipeline("ACEFONE", 0)
        return

    # 3. Apply Filters
    ace_df = ace_df[
        (ace_df["direction"] == "outbound") | 
        ((ace_df["direction"] == "inbound") & (ace_df["status"].str.lower() == "answered"))
    ]

    if ace_df.empty:
        log_pipeline("ACEFONE", 0)
        return

    # 4. Deduplicate against existing BQ data
    # (Since DML is off, we must NOT insert anything that already exists)
    start_ts_query = start_range.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    query_bq = f"SELECT call_id FROM `{TABLE_ID}` WHERE call_datetime >= TIMESTAMP('{start_ts_query}') AND source = 'Acefone'"
    
    try:
        bq_ids = client.query(query_bq).to_dataframe()["call_id"].unique().tolist()
    except:
        bq_ids = []
    
    new_records = ace_df[~ace_df["call_id"].isin(bq_ids)]

    # 5. Load
    if not new_records.empty:
        load_to_bigquery(new_records)
        log_pipeline("ACEFONE", len(new_records))
    else:
        print("No new unique records found.")
        log_pipeline("ACEFONE", 0)


if __name__ == "__main__":
    run_pipeline()
