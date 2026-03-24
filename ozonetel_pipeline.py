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
TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"

API_URL = "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails"
API_KEY = os.getenv("OZONETEL_API_KEY") 
USERNAME = os.getenv("OZONETEL_USERNAME")
TEAM_SHEET_URL = os.getenv("TEAM_SHEET_URL")

client = bigquery.Client()
india = pytz.timezone("Asia/Kolkata")

# ================= HELPERS =================
def get_last_date_from_bq():
    """Finds the most recent date in BQ. If empty, looks back 7 days."""
    try:
        query = f"SELECT MAX(CallDate) as last_date FROM `{TABLE_ID}`"
        df = client.query(query).to_dataframe()
        last_date = df.iloc[0]['last_date']
        if pd.isna(last_date):
            return datetime.now(india).date() - timedelta(days=7)
        return last_date
    except:
        return datetime.now(india).date() - timedelta(days=2)

def fetch_and_clean(target_date, team_map):
    """Fetches a single day from Ozonetel and formats it for BQ."""
    day_str = target_date.strftime("%Y-%m-%d")
    payload = {"fromDate": f"{day_str} 00:00:00", "toDate": f"{day_str} 23:59:59", "userName": USERNAME}
    headers = {"Content-Type": "application/json", "apiKey": API_KEY}
    
    try:
        res = requests.get(API_URL, headers=headers, json=payload, timeout=60)
        details = res.json().get("details", [])
        if not details: return pd.DataFrame()
        
        df = pd.DataFrame(details)
        # Agent Map logic
        df["clean"] = df["AgentName"].astype(str).str.strip().str.lower()
        df = df[df["clean"].isin(team_map.keys())].copy()
        df["AgentName"] = df["clean"].map(team_map)
        
        # Standardize columns to match your ozonetel_calls table schema
        df["CallDate"] = pd.to_datetime(day_str).date()
        df["duration_sec"] = df["Duration"].apply(lambda x: sum(int(a) * 60**i for i, a in enumerate(reversed(str(x).split(":")))) if ":" in str(x) else 0)
        df["talktime_sec"] = df["TalkTime"].apply(lambda x: sum(int(a) * 60**i for i, a in enumerate(reversed(str(x).split(":")))) if ":" in str(x) else 0)
        
        for col in ["StartTime", "EndTime"]:
            df[col] = pd.to_datetime(day_str + " " + df[col].astype(str), errors="coerce")
            df[col] = df[col].dt.tz_localize(india, ambiguous='infer').dt.tz_convert("UTC")

        df["phone_number"] = df["E164"].astype(str).str[-10:]
        df["Source"] = "Ozonetel"
        df["updated_at_ampm"] = datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
        
        # Rename API fields to match BQ Schema
        skill_col = "Skill" if "Skill" in df.columns else "skillName"
        status_col = "Status" if "Status" in df.columns else "DialStatus"
        df = df.rename(columns={status_col: "Status", skill_col: "Skill", "CampaignName": "campaignName"})
        
        return df[["CallDate","CallID","AgentName","Status","Disposition","StartTime","EndTime",
                   "duration_sec","talktime_sec","Skill","Type","campaignName","CallFlow",
                   "phone_number","Source","updated_at_ampm"]]
    except:
        return pd.DataFrame()

# ================= MAIN PIPELINE =================
def run_pipeline():
    # 1. Determine the gap (Dynamic Start Date)
    start_date = get_last_date_from_bq()
    today = datetime.now(india).date()
    
    # Existing team map function
    from_team_sheet = pd.read_csv(TEAM_SHEET_URL)
    team_map = dict(zip(from_team_sheet.iloc[:, 2].str.strip().str.lower(), from_team_sheet.iloc[:, 2].str.strip()))

    # 2. Fetch all data for the gap period from API
    api_results = []
    curr = start_date
    while curr <= today:
        api_results.append(fetch_and_clean(curr, team_map))
        curr += timedelta(days=1)
        time.sleep(1)
    
    api_df = pd.concat(api_results, ignore_index=True) if api_results else pd.DataFrame()
    if api_df.empty:
        print("No data found in API for the specified range.")
        return

    # 3. Fetch existing data from BQ for the same window to compare
    query = f"SELECT CallID, StartTime, EndTime FROM `{TABLE_ID}` WHERE CallDate >= '{start_date}'"
    existing_bq = client.query(query).to_dataframe()
    
    # Standardize types for comparison
    if not existing_bq.empty:
        existing_bq['StartTime'] = pd.to_datetime(existing_bq['StartTime'], utc=True)
        existing_bq['EndTime'] = pd.to_datetime(existing_bq['EndTime'], utc=True)

    # 4. Logical Deduplication (Pure Pandas)
    # This identifies rows in api_df that DO NOT exist in existing_bq
    if not existing_bq.empty:
        merged = api_df.merge(
            existing_bq, 
            on=["CallID", "StartTime", "EndTime"], 
            how="left", 
            indicator=True
        )
        final_to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
    else:
        final_to_push = api_df

    # 5. Append Only New Rows
    if not final_to_push.empty:
        print(f"Pushing {len(final_to_push)} new records to BigQuery.")
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
        client.load_table_from_dataframe(final_to_push, TABLE_ID, job_config=job_config).result()
    else:
        print("Data is already up to date. No new records found.")

if __name__ == "__main__":
    run_pipeline()
