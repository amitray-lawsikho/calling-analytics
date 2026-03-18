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
TABLE = "acefone_calls"

TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"

# ACEFONE
ACE_API_KEY = os.getenv("ACEFONE_API_KEY")
ACCOUNT_ID = os.getenv("ACEFONE_ACCOUNT_ID")
ACE_URL = "https://api.acefone.in/v1/call/records"

# OZONETEL
OZO_API_KEY = os.getenv("OZONETEL_API_KEY")
OZO_USERNAME = os.getenv("OZONETEL_USERNAME")
OZO_URL = "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails"

TEAM_SHEET_URL = os.getenv("TEAM_SHEET_URL")

LIMIT = 1000
india = pytz.timezone("Asia/Kolkata")

client = bigquery.Client()

# ---------------- GET LAST TIME ----------------

def get_last_call_time():
    try:
        query = f"""
        SELECT MAX(call_datetime) last_time
        FROM `{TABLE_ID}`
        """
        df = client.query(query).to_dataframe()
        last_time = df.iloc[0]["last_time"]

        if pd.isna(last_time):
            return datetime.utcnow() - timedelta(hours=6)

        return last_time

    except:
        return datetime.utcnow() - timedelta(hours=6)


# ---------------- FETCH TEAM ----------------

def get_team_members():
    try:
        df = pd.read_csv(TEAM_SHEET_URL)
        team = df.iloc[:, 2].dropna().astype(str).str.strip().unique().tolist()
        print("Team Loaded:", len(team))
        return team
    except:
        print("⚠️ Team sheet failed, skipping filter")
        return []


# ---------------- ACEFONE ----------------

def fetch_acefone(last_time):

    fetch_start = last_time - timedelta(minutes=90)

    from_date = fetch_start.strftime("%Y-%m-%d %H:%M:%S")
    to_date = datetime.now(india).strftime("%Y-%m-%d %H:%M:%S")

    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID,
        "accept": "application/json"
    }

    page = 1
    rows = []

    while True:

        params = {
            "from_date": from_date,
            "to_date": to_date,
            "page": page,
            "limit": LIMIT
        }

        try:
            response = requests.get(ACE_URL, headers=headers, params=params)

            if response.status_code != 200:
                break

            results = response.json().get("results", [])

            if not results:
                break

            for call in results:

                call_id = call.get("call_id")
                if not call_id:
                    continue

                client_number = str(call.get("client_number", "")).replace("+91", "")[-10:]

                agent = call.get("agent_name")

                if not agent and call.get("missed_agents"):
                    agent = call["missed_agents"][0].get("name")

                if not agent:
                    for flow in call.get("call_flow", []):
                        if flow.get("type") == "Agent":
                            agent = flow.get("name")
                            break

                rows.append({
                    "client_number": client_number,
                    "call_datetime": call.get("end_stamp"),
                    "call_owner": agent,
                    "call_duration": call.get("call_duration", 0),
                    "status": "Answered" if call.get("status") == "answered" else "Missed",
                    "call_id": call_id,
                    "direction": call.get("direction"),
                    "service": call.get("service"),
                    "reason": call.get("reason"),
                    "call_type": call.get("dialer_call_details", {}).get("call_type"),
                    "source": "Acefone",
                    "updated_at": datetime.utcnow(),
                    "updated_at_ampm": datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
                })

            page += 1
            time.sleep(0.2)

        except:
            break

    print("Acefone rows:", len(rows))
    return rows


# ---------------- OZONETEL ----------------

def fetch_ozonetel(last_time, team):

    fetch_start = last_time - timedelta(minutes=120)

    from_date = fetch_start.strftime("%Y-%m-%d %H:%M:%S")
    to_date = datetime.now(india).strftime("%Y-%m-%d %H:%M:%S")

    headers = {
        "Content-Type": "application/json",
        "apiKey": OZO_API_KEY
    }

    payload = {
        "fromDate": from_date,
        "toDate": to_date,
        "userName": OZO_USERNAME
    }

    try:
        response = requests.get(OZO_URL, headers=headers, json=payload)
        data = response.json()
    except:
        print("Ozonetel API failed")
        return []

    if data.get("status") == "false":
        print("Ozonetel Error:", data.get("message"))
        return []

    rows = []

    for call in data.get("details", []):

        try:
            agent = str(call.get("AgentName", "")).strip()

            if team and agent not in team:
                continue

            call_id = call.get("CallID")
            if not call_id:
                continue

            call_dt = pd.to_datetime(
                f"{call.get('CallDate')} {call.get('StartTime')}",
                format="%d/%m/%Y %H:%M:%S",
                errors="coerce"
            )

            if pd.notna(call_dt):
                call_dt = india.localize(call_dt).astimezone(pytz.UTC)

            def to_sec(t):
                try:
                    h, m, s = map(int, str(t).split(":"))
                    return h*3600 + m*60 + s
                except:
                    return 0

            rows.append({
                "client_number": str(call.get("E164", ""))[-10:],
                "call_datetime": call_dt,
                "call_owner": agent,
                "call_duration": to_sec(call.get("Duration")),
                "status": call.get("Status") or call.get("DialStatus"),
                "call_id": call_id,
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

    print("Ozonetel rows:", len(rows))
    return rows


# ---------------- REMOVE DUPLICATES ----------------

def remove_existing(df):

    if df.empty:
        return df

    try:
        call_ids = df["call_id"].tolist()

        query = f"""
        SELECT call_id
        FROM `{TABLE_ID}`
        WHERE call_id IN UNNEST(@ids)
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("ids", "STRING", call_ids)
            ]
        )

        existing = client.query(query, job_config=job_config).to_dataframe()
        existing_ids = set(existing["call_id"].tolist())

        return df[~df["call_id"].isin(existing_ids)]

    except:
        return df


# ---------------- LOAD ----------------

def load_to_bigquery(rows):

    if not rows:
        print("No rows fetched")
        return

    df = pd.DataFrame(rows)

    df["call_id"] = df["call_id"].astype(str)
    df["client_number"] = df["client_number"].astype(str)

    df["call_datetime"] = pd.to_datetime(
        df["call_datetime"],
        utc=True,
        errors="coerce"
    )

    df["call_duration"] = pd.to_numeric(
        df["call_duration"],
        errors="coerce"
    ).fillna(0).astype(int)

    df.drop_duplicates(subset=["call_id"], inplace=True)

    df = remove_existing(df)

    if df.empty:
        print("No new rows")
        return

    job = client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND"
        )
    )

    job.result()

    print(len(df), "rows inserted")


# ---------------- MAIN ----------------

def run_pipeline():

    last_time = get_last_call_time()
    team = get_team_members()

    ace_rows = fetch_acefone(last_time)
    ozo_rows = fetch_ozonetel(last_time, team)

    all_rows = ace_rows + ozo_rows

    load_to_bigquery(all_rows)

    print("✅ Pipeline completed safely")


if __name__ == "__main__":
    run_pipeline()
