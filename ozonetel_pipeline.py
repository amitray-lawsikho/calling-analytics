import requests
import os
import pandas as pd
import pytz
import time
from datetime import datetime, timedelta
from google.cloud import bigquery

# ================= CONFIG =================
PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "ozonetel_calls"
LOG_TABLE = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"
TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"

API_URL = "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails"
API_KEY = os.getenv("OZONETEL_API_KEY")
USERNAME = os.getenv("OZONETEL_USERNAME")
TEAM_SHEET_URL = os.getenv("TEAM_SHEET_URL")

client = bigquery.Client()
india = pytz.timezone("Asia/Kolkata")

def log_pipeline(pipeline_name, rows):

    ist_now = datetime.now(india)

    log_data = [{
        "run_date": ist_now.date(),
        "pipeline_name": pipeline_name,
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),  # 👈 AM/PM format
        "rows_inserted": int(rows)
    }]

    df = pd.DataFrame(log_data)

    job = client.load_table_from_dataframe(
        df,
        LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()

# ================= GET TEAM LIST (FROM SHEET) =================
def get_team_map():
    try:
        df = pd.read_csv(TEAM_SHEET_URL)

        # 🔥 IMPORTANT: column name is "Caller Name"
        df = df[df["Caller Name"].notna()]

        df["clean"] = df["Caller Name"].astype(str).str.strip().str.lower()

        # mapping: lowercase → proper case
        team_map = dict(zip(df["clean"], df["Caller Name"]))

        print(f"✅ Loaded {len(team_map)} agents from sheet")

        return team_map

    except Exception as e:
        print("❌ Team sheet error:", e)
        return {}

# ================= GET RECENT BQ DATA =================
def get_recent_bq_data(last_time):
    try:
        query = f"""
            SELECT *
            FROM `{TABLE_ID}`
            WHERE EndTime >= TIMESTAMP('{last_time.strftime('%Y-%m-%d %H:%M:%S')}')
        """

        df = client.query(query).to_dataframe()

        if not df.empty:
            print(f"🟢 Pulled {len(df)} records from BQ for dedup")

        return df

    except Exception as e:
        print("BQ fetch error:", e)
        return pd.DataFrame()

# ================= LAST END TIME =================
def get_last_time():
    try:
        df = client.query(f"""
            SELECT MAX(EndTime) AS last_time
            FROM `{TABLE_ID}`
        """).to_dataframe()

        last_time = df.iloc[0]["last_time"]

        if pd.isna(last_time):
            return datetime.now(india) - timedelta(days=2)

        if last_time.tzinfo is None:
            last_time = pytz.utc.localize(last_time)

        return last_time.astimezone(india) - timedelta(minutes=30)

    except:
        return datetime.now(india) - timedelta(days=2)

# ================= HELPERS =================
def time_to_seconds(t):
    try:
        h, m, s = map(int, str(t).split(":"))
        return h*3600 + m*60 + s
    except:
        return 0

# ================= FETCH =================
def fetch_day(day, team_map):

    day_from = day.strftime("%Y-%m-%d 00:00:00")
    day_to   = day.strftime("%Y-%m-%d 23:59:59")

    payload = {
        "fromDate": day_from,
        "toDate": day_to,
        "userName": USERNAME
    }

    headers = {
        "Content-Type": "application/json",
        "apiKey": API_KEY
    }

    for attempt in range(5):

        res = requests.get(API_URL, headers=headers, json=payload)
        data = res.json()
        details = data.get("details", [])

        if details:
            df = pd.DataFrame(details)

            # -------- AGENT CLEANING --------
            df["AgentName"] = df["AgentName"].astype(str).str.strip()
            df["clean"] = df["AgentName"].str.lower()

            df = df[df["clean"].isin(team_map.keys())]
            df["AgentName"] = df["clean"].map(team_map)

            # -------- TIME --------
            df["duration_sec"] = df["Duration"].apply(time_to_seconds)
            df["talktime_sec"] = df["TalkTime"].apply(time_to_seconds)

            # -------- TIMESTAMP FIX --------
            df["StartTime"] = pd.to_datetime(
                day.strftime("%Y-%m-%d") + " " + df["StartTime"].astype(str),
                errors="coerce"
            )

            df["EndTime"] = pd.to_datetime(
                day.strftime("%Y-%m-%d") + " " + df["EndTime"].astype(str),
                errors="coerce"
            )

            # IST → UTC
            df["StartTime"] = df["StartTime"].dt.tz_localize(india).dt.tz_convert("UTC")
            df["EndTime"]   = df["EndTime"].dt.tz_localize(india).dt.tz_convert("UTC")

            # -------- FINAL --------
            df["CallDate"] = day.date()
            df["CallID"] = df["CallID"].astype(str)
            df["phone_number"] = df["E164"].astype(str).str[-10:]
            df["Source"] = "Ozonetel"

            now = datetime.now(india)
            df["updated_at_ampm"] = now.strftime("%d/%m/%Y %I:%M %p")

            skill_col = "Skill" if "Skill" in df.columns else "skillName"
            status_col = "Status" if "Status" in df.columns else "DialStatus"

            df = df.rename(columns={
                status_col: "Status",
                skill_col: "Skill",
                "CampaignName": "campaignName"
            })

            return df[
                [
                    "CallDate","CallID","AgentName","Status","Disposition",
                    "StartTime","EndTime","duration_sec","talktime_sec",
                    "Skill","Type","campaignName","CallFlow",
                    "phone_number","Source","updated_at_ampm"
                ]
            ]

        time.sleep(40)

    return pd.DataFrame()

# ================= MAIN =================
def run_pipeline():

    team_map = get_team_map()
    last_time = get_last_time()

    # 🔥 Step 1: Get recent BQ data
    bq_df = get_recent_bq_data(last_time)

    start_date = last_time.date()
    end_date = datetime.now(india).date()

    all_data = []
    current = start_date

    while current <= end_date:

        print(f"📡 Fetching: {current}")

        df = fetch_day(datetime.combine(current, datetime.min.time()), team_map)

        if not df.empty:
            all_data.append(df)
            print(f"✅ {len(df)} records")
        else:
            print("⚠️ No data")

        time.sleep(40)
        current += timedelta(days=1)

    if not all_data and bq_df.empty:
        print("❌ No data")
        return

    api_df = pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()

    if not api_df.empty:
        api_df = api_df.drop_duplicates(subset=["CallID"])

    final_df = pd.concat([api_df, bq_df], ignore_index=True)

    print(f"📊 Before dedup: {len(final_df)}")

    final_df = final_df.drop_duplicates(subset=["CallID"], keep="last")

    print(f"✅ After dedup: {len(final_df)}")

    # 🔥 Only push NEW records
    if not bq_df.empty:
        final_df = final_df[~final_df["CallID"].isin(bq_df["CallID"])]
    
    rows_count = len(final_df) 
    print(f"🚀 Final push records: {len(final_df)}")

    if final_df.empty:
        log_pipeline("OZONETEL", 0)
        return

    job = client.load_table_from_dataframe(
        final_df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()
    log_pipeline("OZONETEL", rows_count)
    print("✅ Ozonetel data pushed")

if __name__ == "__main__":
    run_pipeline()
