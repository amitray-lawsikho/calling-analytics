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
            return (datetime.now(india) - timedelta(days=7)).date()
        return last_date
    except:
        return (datetime.now(india) - timedelta(days=2)).date()

def time_to_seconds(t):
    try:
        h, m, s = map(int, str(t).split(":"))
        return h*3600 + m*60 + s
    except:
        return 0

def process_day_data(details, team_agents_clean):
    """Processes API details exactly like the Colab process_df function."""
    df = pd.DataFrame(details)
    
    # 1. Agent Filter (Mirroring Backfill)
    df["AgentName"] = df["AgentName"].astype(str).str.strip()
    df = df[df["AgentName"].isin(team_agents_clean)].copy()
    if df.empty: return pd.DataFrame()

    # 2. Add derived fields
    df["duration_sec"] = df["Duration"].apply(time_to_seconds)
    df["talktime_sec"] = df["TalkTime"].apply(time_to_seconds)
    df["phone_number"] = df["E164"].astype(str).str[-10:]
    df["Source"] = "Ozonetel"

    # 3. Dynamic Column Mapping
    skill_col = "Skill" if "Skill" in df.columns else "skillName"
    status_col = "Status" if "Status" in df.columns else "DialStatus"

    final_columns = {
        "CallID": "CallID",
        "AgentName": "AgentName",
        status_col: "Status",
        "Disposition": "Disposition",
        "StartTime": "StartTime",
        "EndTime": "EndTime",
        "duration_sec": "duration_sec",
        "talktime_sec": "talktime_sec",
        skill_col: "Skill",
        "Type": "Type",
        "CampaignName": "campaignName",
        "CallFlow": "CallFlow",
        "phone_number": "phone_number",
        "Source": "Source"
    }

    available_cols = [col for col in final_columns.keys() if col in df.columns]
    df = df[available_cols].copy()
    df.rename(columns=final_columns, inplace=True)

    return df

# ================= MAIN PIPELINE =================
def run_pipeline():
    # Load Team Mapping (Matching Colab Logic)
    team_df = pd.read_csv(TEAM_SHEET_URL)
    team_agents = team_df.iloc[:, 2].dropna().unique().tolist()
    team_agents_clean = [str(x).strip() for x in team_agents]

    start_date = get_last_date_from_bq()
    today = datetime.now(india).date()
    
    all_data = []
    curr = start_date
    
    headers = {"Content-Type": "application/json", "apiKey": API_KEY}

    while curr <= today:
        day_str = curr.strftime("%Y-%m-%d")
        print(f"Fetching Ozonetel data for: {day_str}")
        
        payload = {"fromDate": f"{day_str} 00:00:00", "toDate": f"{day_str} 23:59:59", "userName": USERNAME}
        
        try:
            res = requests.get(API_URL, headers=headers, json=payload, timeout=60)
            details = res.json().get("details", [])
            if details:
                processed_df = process_day_data(details, team_agents_clean)
                if not processed_df.empty:
                    processed_df["CallDate"] = curr # Keep date object for later
                    all_data.append(processed_df)
        except Exception as e:
            print(f"Error on {day_str}: {e}")
        
        curr += timedelta(days=1)
        time.sleep(1)
    
    if not all_data:
        print("No valid data found in API for range.")
        return

    # Combine everything
    final_df = pd.concat(all_data, ignore_index=True)

    # 🔥 CRITICAL FILTER: Exactly as per Backfill script
    # This removes "Inbound" + "Unanswered"
    initial_count = len(final_df)
    final_df = final_df[~((final_df["Type"] == "Inbound") & (final_df["Status"] == "Unanswered"))].copy()
    print(f"Filtering: Removed {initial_count - len(final_df)} missed inbound calls.")

    if final_df.empty:
        print("No records left after filtering.")
        return

    # 1. Standardize Timestamps (IST to UTC)
    final_df["CallDate_str"] = final_df["CallDate"].astype(str)
    for col in ["StartTime", "EndTime"]:
        final_df[col] = pd.to_datetime(final_df["CallDate_str"] + " " + final_df[col].astype(str), errors='coerce')
        final_df[col] = final_df[col].dt.tz_localize(india, ambiguous='infer').dt.tz_convert("UTC")

    final_df.drop(columns=["CallDate_str"], inplace=True)
    final_df["CallDate"] = pd.to_datetime(final_df["CallDate"]).dt.date

    # 2. Add Update Timestamp & Enforce Types
    final_df["updated_at_ampm"] = datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
    final_df["CallID"] = final_df["CallID"].astype(str)
    final_df["duration_sec"] = pd.to_numeric(final_df["duration_sec"]).fillna(0).astype(int)
    final_df["talktime_sec"] = pd.to_numeric(final_df["talktime_sec"]).fillna(0).astype(int)

    # 3. INCREMENTAL LOGIC (Deduplication)
    # We query BQ to find records we already have to avoid duplicates
    query = f"""
        SELECT CAST(CallID AS STRING) as CallID, StartTime, EndTime 
        FROM `{TABLE_ID}` 
        WHERE CallDate >= '{start_date}'
    """
    existing_bq = client.query(query).to_dataframe()
    
    if not existing_bq.empty:
        existing_bq['StartTime'] = pd.to_datetime(existing_bq['StartTime'], utc=True)
        existing_bq['EndTime'] = pd.to_datetime(existing_bq['EndTime'], utc=True)
        final_df['StartTime'] = pd.to_datetime(final_df['StartTime'], utc=True)
        final_df['EndTime'] = pd.to_datetime(final_df['EndTime'], utc=True)
        
        merged = final_df.merge(
            existing_bq, 
            on=["CallID", "StartTime", "EndTime"], 
            how="left", 
            indicator=True
        )
        to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
    else:
        to_push = final_df

    # 4. PUSH TO BIGQUERY
    if not to_push.empty:
        print(f"Pushing {len(to_push)} unique records to BigQuery.")
        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema=[
                bigquery.SchemaField("StartTime", "TIMESTAMP"),
                bigquery.SchemaField("EndTime", "TIMESTAMP"),
                bigquery.SchemaField("CallDate", "DATE"),
            ]
        )
        client.load_table_from_dataframe(to_push, TABLE_ID, job_config=job_config).result()
        print("✅ Pipeline run successful.")
    else:
        print("No new unique records found.")

if __name__ == "__main__":
    run_pipeline()
