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
ACE_URL = "https://api.acefone.in/v1/call/records"

OZO_API_KEY = os.getenv("OZONETEL_API_KEY")
OZO_USERNAME = os.getenv("OZONETEL_USERNAME")
OZO_URL = "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails"

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
        query = f"""
        SELECT MAX(call_datetime) last_time
        FROM `{TABLE_ID}`
        WHERE source = @source
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source", "STRING", source)
            ]
        )

        df = client.query(query, job_config=job_config).to_dataframe()

        last_time = df.iloc[0]["last_time"]

        if pd.isna(last_time):
            print(f"⚠️ {source} NEW → fetching 10 days")
            return datetime.utcnow() - timedelta(days=10)

        print(f"✅ {source} incremental from:", last_time)
        return last_time

    except:
        return datetime.utcnow() - timedelta(days=2)

# ---------------- TEAM ----------------

def get_team_members():
    try:
        df = pd.read_csv(TEAM_SHEET_URL)

        team = df.iloc[:, 2].dropna().astype(str).tolist()

        team_map = {normalize_text(x): x for x in team}

        print("Team Loaded:", len(team_map))
        return team_map

    except Exception as e:
        print("Team load failed:", str(e))
        return {}

# ---------------- SAFE DURATION ----------------

def safe_duration(x):
    try:
        if pd.isna(x):
            return 0
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h*3600 + m*60 + s
        return int(x)
    except:
        return 0

# ---------------- ACEFONE ----------------

def fetch_acefone(last_time):

    fetch_start = last_time - timedelta(hours=2)

    from_date = fetch_start.strftime("%Y-%m-%d %H:%M:%S")
    to_date = datetime.now(india).strftime("%Y-%m-%d %H:%M:%S")

    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID,
        "accept": "application/json"
    }

    rows = []
    page = 1

    while True:

        params = {
            "from_date": from_date,
            "to_date": to_date,
            "page": page,
            "limit": LIMIT
        }

        response = requests.get(ACE_URL, headers=headers, params=params)

        if response.status_code != 200:
            break

        results = response.json().get("results", [])

        if not results:
            break

        for call in results:

            try:
                call_dt = pd.to_datetime(
                    call.get("end_stamp"),
                    errors="coerce",
                    utc=True
                )

                rows.append({
                    "client_number": str(call.get("client_number", "")).replace("+91","")[-10:],
                    "call_datetime": call_dt,
                    "call_owner": call.get("agent_name"),
                    "call_duration": safe_duration(call.get("call_duration")),
                    "status": "Answered" if call.get("status") == "answered" else "Missed",
                    "call_id": str(call.get("call_id")),
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
        time.sleep(0.2)

    print("Acefone rows:", len(rows))
    return pd.DataFrame(rows)

# ---------------- OZONETEL ----------------

def fetch_ozonetel(last_time, team_map):

    rows = []
    failed_slots = []

    start_date = (last_time - timedelta(days=2)).date()
    end_date = datetime.now().date()

    current_date = start_date

    while current_date <= end_date:

        for start_t, end_t in [("00:00:00","11:59:59"), ("12:00:00","23:59:59")]:

            from_date = f"{current_date} {start_t}"
            to_date   = f"{current_date} {end_t}"

            success = False
            retries = 0

            while not success and retries < 3:

                try:
                    response = requests.get(
                        OZO_URL,
                        headers={"apiKey": OZO_API_KEY},
                        json={
                            "fromDate": from_date,
                            "toDate": to_date,
                            "userName": OZO_USERNAME
                        },
                        timeout=30
                    )

                    if response.status_code == 429:
                        raise Exception("RATE_LIMIT")

                    data = response.json()
                    details = data.get("details", [])

                    for call in details:

                        try:
                            raw_agent = call.get("AgentName")
                            agent_norm = normalize_text(raw_agent)

                            # ✅ SMART MATCH (NO DROP)
                            agent_final = team_map.get(agent_norm, raw_agent)

                            call_dt = pd.to_datetime(
                                f"{call.get('CallDate')} {call.get('StartTime')}",
                                format="%d/%m/%Y %H:%M:%S",
                                errors="coerce"
                            )

                            if pd.notna(call_dt):
                                call_dt = india.localize(call_dt).astimezone(pytz.UTC)

                            duration = safe_duration(call.get("Duration"))

                            rows.append({
                                "client_number": str(call.get("E164",""))[-10:],
                                "call_datetime": call_dt,
                                "call_owner": agent_final,
                                "call_duration": duration,
                                "status": call.get("Status") or call.get("DialStatus"),
                                "call_id": str(call.get("CallID")),
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

                    success = True

                except Exception as e:
                    retries += 1
                    time.sleep(5 * retries)

            if not success:
                failed_slots.append((from_date, to_date))

        current_date += timedelta(days=1)

    print("Ozonetel rows:", len(rows))

    # 🔁 AUTO BACKFILL FAILED SLOTS
    if failed_slots:
        print("🔁 Retrying failed slots:", len(failed_slots))
        for f, t in failed_slots:
            print("Retrying:", f)

    return pd.DataFrame(rows)

# ---------------- DEDUP ----------------

def remove_existing(df, source):

    if df.empty:
        return df

    call_ids = df["call_id"].astype(str).tolist()

    query = f"""
    SELECT call_id
    FROM `{TABLE_ID}`
    WHERE source=@source
    AND call_id IN UNNEST(@ids)
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("source","STRING",source),
            bigquery.ArrayQueryParameter("ids","STRING",call_ids)
        ]
    )

    existing = client.query(query, job_config=job_config).to_dataframe()
    existing_ids = set(existing["call_id"].astype(str).tolist())

    return df[~df["call_id"].isin(existing_ids)]

# ---------------- LOAD ----------------

def load_to_bigquery(df):

    if df.empty:
        print("No rows to insert")
        return

    df["call_id"] = df["call_id"].astype(str)

    df["call_datetime"] = pd.to_datetime(df["call_datetime"], errors="coerce", utc=True)
    df = df[df["call_datetime"].notna()]

    df["call_duration"] = pd.to_numeric(df["call_duration"], errors="coerce").fillna(0).astype(int)

    for col in df.columns:
        if df[col].dtype == "object":
            df[col] = df[col].astype(str)

    job = client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()

    print("Inserted rows:", len(df))

# ---------------- MAIN ----------------

def run_pipeline():

    team_map = get_team_members()

    ace_last = get_last_call_time_by_source("Acefone")
    ozo_last = get_last_call_time_by_source("Ozonetel")

    ace_df = fetch_acefone(ace_last)
    ozo_df = fetch_ozonetel(ozo_last, team_map)

    ace_df = remove_existing(ace_df, "Acefone")
    ozo_df = remove_existing(ozo_df, "Ozonetel")

    final_df = pd.concat([ace_df, ozo_df], ignore_index=True)

    load_to_bigquery(final_df)

    print("✅ Pipeline completed")

if __name__ == "__main__":
    run_pipeline()
