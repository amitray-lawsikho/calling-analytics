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

TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"

ACE_API_KEY = os.getenv("ACEFONE_API_KEY")
ACCOUNT_ID = os.getenv("ACEFONE_ACCOUNT_ID")

OZO_API_KEY = os.getenv("OZONETEL_API_KEY")
OZO_USERNAME = os.getenv("OZONETEL_USERNAME")

TEAM_SHEET_URL = os.getenv("TEAM_SHEET_URL")

india = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()

# ---------------- HELPERS ----------------

def safe_duration(x):
    try:
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h*3600 + m*60 + s
        return int(x)
    except:
        return 0

def parse_ozonetel_datetime(call_date, start_time):

    if not call_date or not start_time:
        return None

    dt_str = f"{call_date} {start_time}"

    for fmt in ["%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"]:
        try:
            naive = datetime.strptime(dt_str, fmt)
            ist_dt = india.localize(naive)
            return ist_dt.astimezone(pytz.UTC)
        except:
            continue

    return None

# ---------------- INCREMENTAL ----------------

def get_last_call_time_by_source(source):
    try:
        df = client.query(f"""
            SELECT MAX(call_datetime) AS last_time
            FROM `{TABLE_ID}`
            WHERE source = '{source}'
        """).to_dataframe()

        last_time = df.iloc[0]["last_time"]

        # ✅ FIRST RUN → LAST 2 DAYS
        if pd.isna(last_time):
            print(f"{source} first run → 2 days back")
            return datetime.utcnow() - timedelta(days=2)

        # ✅ 30 MIN BUFFER
        buffered_time = last_time - timedelta(minutes=30)

        print(f"{source} incremental from:", buffered_time)
        return buffered_time

    except Exception as e:
        print("Error fetching last time:", e)
        return datetime.utcnow() - timedelta(days=2)

# ---------------- ACEFONE ----------------

def fetch_acefone(last_time):

    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID
    }

    rows = []
    page = 1

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
            break

        data = res.json().get("results", [])
        if not data:
            break

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

        page += 1

    return pd.DataFrame(rows)

# ---------------- OZONETEL ----------------

def fetch_ozonetel(last_time):

    rows = []

    # Team filter
    team_df = pd.read_csv(TEAM_SHEET_URL)
    team_agents = team_df.iloc[:, 2].dropna().astype(str).str.strip().tolist()

    start_date = last_time.date()
    end_date = datetime.now().date()

    while start_date <= end_date:

        for s, e in [("00:00:00","11:59:59"), ("12:00:00","23:59:59")]:

            body = {
                "fromDate": f"{start_date} {s}",
                "toDate": f"{start_date} {e}",
                "userName": OZO_USERNAME
            }

            res = requests.get(
                "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails",
                headers={"apiKey": OZO_API_KEY},
                json=body
            )

            try:
                data = res.json()
            except:
                continue

            details = data.get("details", [])

            for call in details:

                dt = parse_ozonetel_datetime(
                    call.get("CallDate"),
                    call.get("StartTime")
                )

                if not dt:
                    continue

                agent = str(call.get("AgentName","")).strip()

                if agent not in team_agents:
                    continue

                rows.append({
                    "client_number": str(call.get("E164",""))[-10:],
                    "call_datetime": dt,
                    "call_owner": agent,
                    "call_duration": safe_duration(call.get("Duration")),
                    "status": call.get("Status") or call.get("DialStatus"),
                    "call_id": "OZO_" + str(call.get("CallID")),
                    "direction": call.get("Type"),
                    "service": call.get("Type"),
                    "reason": call.get("DialStatus"),
                    "call_type": call.get("Type"),
                    "source": "Ozonetel",
                    "updated_at": datetime.utcnow(),
                    "updated_at_ampm": datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
                })

        start_date += timedelta(days=1)

    return pd.DataFrame(rows)

# ---------------- LOAD ----------------

def load_to_bigquery(df):

    if df.empty:
        print("No data to load")
        return

    # ✅ DEDUP ONLY BY call_id
    df = df.drop_duplicates(subset=["call_id"])

    # ✅ FINAL CLEAN
    df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True, errors="coerce")
    df = df[df["call_datetime"].notna()]

    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)

    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].astype(str)

    print("FINAL SOURCE SPLIT:\n", df["source"].value_counts())

    job = client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()
    print("Inserted:", len(df))

# ---------------- MAIN ----------------

def run_pipeline():

    ace_last = get_last_call_time_by_source("Acefone")
    ozo_last = get_last_call_time_by_source("Ozonetel")

    ace_df = fetch_acefone(ace_last)
    ozo_df = fetch_ozonetel(ozo_last)

    print("ACE ROWS:", len(ace_df))
    print("OZO ROWS:", len(ozo_df))

    final_df = pd.concat([ace_df, ozo_df], ignore_index=True)

    load_to_bigquery(final_df)

    print("DONE")

if __name__ == "__main__":
    run_pipeline()
