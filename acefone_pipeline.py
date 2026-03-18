import requests
import os
import pandas as pd
import pytz
import time
import re
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

LIMIT = 1000
india = pytz.timezone("Asia/Kolkata")

client = bigquery.Client()

# ---------------- NORMALIZER ----------------

def normalize_text(x):
    if pd.isna(x):
        return ""
    x = str(x).lower().strip()
    x = re.sub(r'[^a-z0-9 ]', '', x)
    return " ".join(x.split())

# ---------------- LAST TIME ----------------

def get_last_call_time_by_source(source):
    try:
        df = client.query(f"""
            SELECT MAX(call_datetime) last_time
            FROM `{TABLE_ID}`
            WHERE source = '{source}'
        """).to_dataframe()

        last_time = df.iloc[0]["last_time"]

        if pd.isna(last_time):
            print(f"⚠️ {source} NEW → 10 days fetch")
            return datetime.utcnow() - timedelta(days=10)

        print(f"✅ {source} from:", last_time)
        return last_time

    except Exception as e:
        print("Last time error:", e)
        return datetime.utcnow() - timedelta(days=2)

# ---------------- TEAM ----------------

def get_team_members():
    try:
        df = pd.read_csv(TEAM_SHEET_URL)
        team = df.iloc[:, 2].dropna().astype(str).tolist()
        return {normalize_text(x): x for x in team}
    except:
        return {}

# ---------------- SAFE ----------------

def safe_duration(x):
    try:
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h*3600 + m*60 + s
        return int(x)
    except:
        return 0

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
            "from_date": (last_time - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
            "to_date": datetime.now(india).strftime("%Y-%m-%d %H:%M:%S"),
            "page": page,
            "limit": LIMIT
        }

        res = requests.get("https://api.acefone.in/v1/call/records", headers=headers, params=params)

        if res.status_code != 200:
            print("Acefone API failed:", res.text)
            break

        data = res.json().get("results", [])
        if not data:
            break

        for call in data:
            try:
                rows.append({
                    "client_number": str(call.get("client_number",""))[-10:],
                    "call_datetime": pd.to_datetime(call.get("end_stamp"), utc=True, errors="coerce"),
                    "call_owner": call.get("agent_name"),
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
            except:
                continue

        page += 1

    df = pd.DataFrame(rows)
    print("Acefone DF:", df.shape)
    return df

# ---------------- OZONETEL ----------------

def fetch_ozonetel(last_time):

    rows = []

    start_date = (last_time - timedelta(days=2)).date()
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

            print(f"Ozonetel {start_date} {s} → status:", res.status_code)

            try:
                data = res.json()
            except:
                print("Invalid JSON")
                continue

            if data.get("status") == "false":
                print("Ozonetel API ERROR:", data)
                continue

            details = data.get("details", [])
            print("Records fetched:", len(details))

            for call in details:
                try:
                    dt = pd.to_datetime(
                        f"{call.get('CallDate')} {call.get('StartTime')}",
                        format="%d/%m/%Y %H:%M:%S",
                        errors="coerce"
                    )

                    if pd.notna(dt):
                        dt = india.localize(dt).astimezone(pytz.UTC)

                    rows.append({
                        "client_number": str(call.get("E164",""))[-10:],
                        "call_datetime": dt,
                        "call_owner": call.get("AgentName"),
                        "call_duration": safe_duration(call.get("Duration")),
                        "status": call.get("Status"),
                        "call_id": "OZO_" + str(call.get("CallID")),
                        "direction": call.get("Type"),
                        "service": call.get("Type"),
                        "reason": call.get("DialStatus"),
                        "call_type": call.get("Type"),
                        "source": "Ozonetel",
                        "updated_at": datetime.utcnow(),
                        "updated_at_ampm": datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
                    })

                except:
                    continue

        start_date += timedelta(days=1)

    df = pd.DataFrame(rows)
    print("Ozonetel DF:", df.shape)
    return df

# ---------------- LOAD ----------------

def load_to_bigquery(df):

    print("FINAL DF BEFORE LOAD:", df["source"].value_counts())

    job = client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )
    job.result()

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
