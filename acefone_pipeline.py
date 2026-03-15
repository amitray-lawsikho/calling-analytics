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

API_KEY = os.getenv("ACEFONE_API_KEY")
ACCOUNT_ID = os.getenv("ACEFONE_ACCOUNT_ID")

BASE_URL = "https://api.acefone.in/v1/call/records"

LIMIT = 1000

india = pytz.timezone("Asia/Kolkata")

# ---------------- BIGQUERY CLIENT ----------------

client = bigquery.Client()

# ---------------- GET LAST TIME ----------------

def get_last_call_time():

    query = f"""
    SELECT MAX(call_datetime) last_time
    FROM `{TABLE_ID}`
    """

    df = client.query(query).to_dataframe()

    last_time = df.iloc[0]["last_time"]

    if pd.isna(last_time):
        last_time = datetime.utcnow() - timedelta(hours=2)

    return last_time


# ---------------- FETCH CALLS ----------------

def fetch_calls():

    last_time = get_last_call_time()

    # 60 minute overlap for safety
    fetch_start = last_time - timedelta(minutes=90)

    from_date = fetch_start.strftime("%Y-%m-%d %H:%M:%S")
    to_date = datetime.now(india).strftime("%Y-%m-%d %H:%M:%S")

    print("Fetching calls from:", from_date)

    headers = {
        "Authorization": f"Bearer {API_KEY}",
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

        response = requests.get(BASE_URL, headers=headers, params=params)

        if response.status_code != 200:
            print("API ERROR:", response.text)
            break

        data = response.json()
        results = data.get("results", [])

        if not results:
            break

        for call in results:

            call_id = call.get("call_id")
            client_number = call.get("client_number")

            if not call_id or not client_number:
                continue

            client_number = str(client_number).replace("+91", "")[-10:]

            duration = call.get("call_duration", 0)
            direction = call.get("direction")
            status = call.get("status")

            agent = call.get("agent_name")

            if not agent and call.get("missed_agents"):
                agent = call["missed_agents"][0].get("name")

            if not agent:
                for flow in call.get("call_flow", []):
                    if flow.get("type") == "Agent":
                        agent = flow.get("name")
                        break

            call_type = None

            if call.get("dialer_call_details"):
                call_type = call["dialer_call_details"].get("call_type")

            updated = datetime.utcnow()
            updated_ampm = updated.astimezone(india).strftime("%d/%m/%Y %I:%M %p")

            row = {

                "client_number": client_number,
                "call_datetime": call.get("end_stamp"),
                "call_owner": agent,
                "call_duration": duration,
                "status": "Answered" if status == "answered" else "Missed",
                "call_id": call_id,
                "direction": direction,
                "service": call.get("service"),
                "reason": call.get("reason"),
                "call_type": call_type,
                "updated_at": updated,
                "updated_at_ampm": updated_ampm
            }

            rows.append(row)

        page += 1
        time.sleep(0.2)

    return rows


# ---------------- REMOVE DUPLICATES ----------------

def remove_existing(df):

    if df.empty:
        return df

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

    df = df[~df["call_id"].isin(existing_ids)]

    return df


# ---------------- LOAD TO BIGQUERY ----------------

def load_to_bigquery(rows):

    if not rows:
        print("No rows fetched")
        return

    df = pd.DataFrame(rows)

    df["call_datetime"] = pd.to_datetime(df["call_datetime"], utc=True)
    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)

    df.drop_duplicates(subset=["call_id"], inplace=True)

    df = remove_existing(df)

    if df.empty:
        print("No new rows")
        return

    job = client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()

    print(len(df), "rows inserted")


# ---------------- MAIN PIPELINE ----------------

def run_pipeline():

    rows = fetch_calls()

    load_to_bigquery(rows)

    print("Pipeline completed")


if __name__ == "__main__":
    run_pipeline()
