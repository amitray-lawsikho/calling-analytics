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

def fetch_and_clean(target_date, team_agents_clean):
    """Fetches a single day from Ozonetel and formats it for BQ."""
    day_str = target_date.strftime("%Y-%m-%d")
    payload = {"fromDate": f"{day_str} 00:00:00", "toDate": f"{day_str} 23:59:59", "userName": USERNAME}
    headers = {"Content-Type": "application/json", "apiKey": API_KEY}
    
    try:
        res = requests.get(API_URL, headers=headers, json=payload, timeout=60)
        details = res.json().get("details", [])
        if not details: return pd.DataFrame()
        
        df = pd.DataFrame(details)
        
        # 1. Agent Filter (Match Backfill)
        df["AgentName"] = df["AgentName"].astype(str).str.strip()
        df = df[df["AgentName"].isin(team_agents_clean)].copy()
        if df.empty: return pd.DataFrame()

        # 2. Standardize Columns (Handling dynamic Ozonetel headers)
        skill_col = "Skill" if "Skill" in df.columns else "skillName"
        status_col = "Status" if "Status" in df.columns else "DialStatus"
        
        df = df.rename(columns={
            status_col: "Status", 
            skill_col: "Skill", 
            "CampaignName": "campaignName",
            "E164": "phone_number"
        })

        # 🔥 CRITICAL FILTER: Remove "Inbound" + "Unanswered"
        # Must happen after renaming to 'Status'
        df = df[~((df["Type"] == "Inbound") & (df["Status"] == "Unanswered"))].copy()
        if df.empty: return pd.DataFrame()

        # 3. Durations & formatting
        def time_to_sec(t):
            try:
                parts = str(t).split(":")
                return sum(int(x) * 60**i for i, x in enumerate(reversed(parts)))
            except: return 0

        df["duration_sec"] = df["Duration"].apply(time_to_sec)
        df["talktime_sec"] = df["TalkTime"].apply(time_to_sec)
        df["phone_number"] = df["phone_number"].astype(str).str[-10:]
        df["Source"] = "Ozonetel"
        df["updated_at_ampm"] = datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
        df["CallDate"] = pd.to_datetime(day_str).date()
        df["CallID"] = df["CallID"].astype(str)

        # 4. Timezone Handling (IST to UTC)
        for col in ["StartTime", "EndTime"]:
            df[col] = pd.to_datetime(day_str + " " + df[col].astype(str), errors="coerce")
            df[col] = df[col].dt.tz_localize(india, ambiguous='infer').dt.tz_convert("UTC")

        return df[["CallDate","CallID","AgentName","Status","Disposition","StartTime","EndTime",
                   "duration_sec","talktime_sec","Skill","Type","campaignName","CallFlow",
                   "phone_number","Source","updated_at_ampm"]]
    except Exception as e:
        print(f"Error fetching {day_str}: {e}")
        return pd.DataFrame()

# ================= MAIN PIPELINE =================
def run_pipeline():
    # Load Team Mapping
    team_df = pd.read_csv(TEAM_SHEET_URL)
    team_agents_clean = [str(x).strip() for x in team_df.iloc[:, 2].dropna().unique()]

    start_date = get_last_date_from_bq()
    today = datetime.now(india).date()
    
    api_results = []
    curr = start_date
    while curr <= today:
        print(f"Fetching Ozonetel data for: {curr}")
        day_df = fetch_and_clean(curr, team_agents_clean)
        if not day_df.empty:
            api_results.append(day_df)
        curr += timedelta(days=1)
        time.sleep(1)
    
    if not api_results:
        print("No valid data found in API for range.")
        return

    api_df = pd.concat(api_results, ignore_index=True)

    # ==========================================
    # INCREMENTAL LOGIC (Deduplication)
    # ==========================================
    api_df["CallID"] = api_df["CallID"].astype(str)

    # Query BQ to see what we already have for this date range
    query = f"""
        SELECT CAST(CallID AS STRING) as CallID, StartTime, EndTime 
        FROM `{TABLE_ID}` 
        WHERE CallDate >= '{start_date}'
    """
    existing_bq = client.query(query).to_dataframe()
    
    if not existing_bq.empty:
        # Standardize timestamps to UTC for perfect comparison
        existing_bq['StartTime'] = pd.to_datetime(existing_bq['StartTime'], utc=True)
        existing_bq['EndTime'] = pd.to_datetime(existing_bq['EndTime'], utc=True)
        api_df['StartTime'] = pd.to_datetime(api_df['StartTime'], utc=True)
        api_df['EndTime'] = pd.to_datetime(api_df['EndTime'], utc=True)
        
        # Deduplicate using CallID + StartTime + EndTime (Triple Key)
        merged = api_df.merge(
            existing_bq, 
            on=["CallID", "StartTime", "EndTime"], 
            how="left", 
            indicator=True
        )
        final_to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
    else:
        final_to_push = api_df

    # ==========================================
    # PUSH TO BIGQUERY
    # ==========================================
    if not final_to_push.empty:
        print(f"Pushing {len(final_to_push)} unique records to BigQuery.")
        
        # Final type enforcement
        final_to_push["duration_sec"] = pd.to_numeric(final_to_push["duration_sec"]).fillna(0).astype(int)
        final_to_push["talktime_sec"] = pd.to_numeric(final_to_push["talktime_sec"]).fillna(0).astype(int)

        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema=[
                bigquery.SchemaField("StartTime", "TIMESTAMP"),
                bigquery.SchemaField("EndTime", "TIMESTAMP"),
                bigquery.SchemaField("CallDate", "DATE"),
            ]
        )
        client.load_table_from_dataframe(final_to_push, TABLE_ID, job_config=job_config).result()
        print("✅ Pipeline run successful.")
    else:
        print("No new unique records found.")

if __name__ == "__main__":
    run_pipeline()
