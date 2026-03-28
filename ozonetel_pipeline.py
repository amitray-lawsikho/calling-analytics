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
    if df.empty: return pd.DataFrame()
    
    # 1. Agent Filter (Mirroring Backfill)
    df["AgentName"] = df["AgentName"].astype(str).str.strip()
    # Case-insensitive agent check for better reliability
    df["AgentName_Lower"] = df["AgentName"].str.lower()
    team_agents_lower = [x.lower() for x in team_agents_clean]
    df = df[df["AgentName_Lower"].isin(team_agents_lower)].copy()
    
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
    team_df = pd.read_csv(TEAM_SHEET_URL)
    team_agents = team_df.iloc[:, 2].dropna().unique().tolist()
    team_agents_clean = [str(x).strip() for x in team_agents]

    start_date = get_last_date_from_bq()
    today      = datetime.now(india).date()

    all_data = []
    curr     = start_date
    headers  = {"Content-Type": "application/json", "apiKey": API_KEY}

    while curr <= today:
        day_str     = curr.strftime("%Y-%m-%d")
        all_details = []
        page_no     = 1
        page_size   = 500

        print(f"\nFetching {day_str}...")

        while True:
            payload = {
                "fromDate" : f"{day_str} 00:00:00",
                "toDate"   : f"{day_str} 23:59:59",
                "userName" : USERNAME
            }
            params = {"pageNo": page_no, "pageSize": page_size}

            try:
                res         = requests.get(API_URL, headers=headers,
                                           json=payload, params=params, timeout=60)

                if res.status_code == 429:
                    print(f"  Rate limited. Waiting 65s...")
                    time.sleep(65)
                    continue

                data        = res.json()
                details     = data.get("details", [])
                total_count = int(data.get("totalCount", 0))

                if not details:
                    print(f"  p{page_no}: 0 records (totalCount={total_count})")
                    break

                all_details.extend(details)
                print(f"  p{page_no}: +{len(details)} records ({len(all_details)}/{total_count})")

                if len(all_details) >= total_count or len(details) < page_size:
                    break

                page_no += 1
                time.sleep(32)   # 2 req/min rate limit

            except Exception as e:
                print(f"  Error on {day_str} p{page_no}: {e}")
                break

        if all_details:
            processed = process_day_data(all_details, team_agents_clean)
            if not processed.empty:
                processed["CallDate"] = curr
                all_data.append(processed)
                print(f"  Queued {len(processed)} records for {day_str}")

        curr += timedelta(days=1)
        if curr <= today:
            time.sleep(32)   # sleep between days too

    if not all_data:
        print("No valid data found.")
        return

    final_df = pd.concat(all_data, ignore_index=True)

    initial_count = len(final_df)
    final_df = final_df[~((final_df["Type"].str.lower()   == "inbound") &
                          (final_df["Status"].str.lower() == "unanswered"))].copy()
    print(f"Removed {initial_count - len(final_df)} missed inbound. {len(final_df)} remain.")

    if final_df.empty:
        print("No records left after filtering.")
        return

    final_df["CallDate_str"] = final_df["CallDate"].astype(str)
    for col in ["StartTime", "EndTime"]:
        final_df[col] = pd.to_datetime(
            final_df["CallDate_str"] + " " + final_df[col].astype(str), errors="coerce"
        )
        final_df[col] = final_df[col].dt.tz_localize(india, ambiguous="infer").dt.tz_convert("UTC")

    final_df.drop(columns=["CallDate_str"], inplace=True)
    final_df["CallDate"]       = pd.to_datetime(final_df["CallDate"]).dt.date
    final_df["updated_at_ampm"] = datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
    final_df["CallID"]          = final_df["CallID"].astype(str)
    final_df["duration_sec"]    = pd.to_numeric(final_df["duration_sec"]).fillna(0).astype(int)
    final_df["talktime_sec"]    = pd.to_numeric(final_df["talktime_sec"]).fillna(0).astype(int)

    # Dedup
    query = f"""
        SELECT CAST(CallID AS STRING) AS CallID, StartTime, EndTime
        FROM `{TABLE_ID}` WHERE CallDate >= '{start_date}'
    """
    existing_bq = client.query(query).to_dataframe()

    if not existing_bq.empty:
        existing_bq["StartTime"] = pd.to_datetime(existing_bq["StartTime"], utc=True)
        existing_bq["EndTime"]   = pd.to_datetime(existing_bq["EndTime"],   utc=True)
        final_df["StartTime"]    = pd.to_datetime(final_df["StartTime"],    utc=True)
        final_df["EndTime"]      = pd.to_datetime(final_df["EndTime"],      utc=True)

        merged  = final_df.merge(existing_bq, on=["CallID", "StartTime", "EndTime"],
                                 how="left", indicator=True)
        to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
    else:
        to_push = final_df

    if not to_push.empty:
        print(f"Pushing {len(to_push)} new records to BigQuery.")
        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema=[
                bigquery.SchemaField("StartTime", "TIMESTAMP"),
                bigquery.SchemaField("EndTime",   "TIMESTAMP"),
                bigquery.SchemaField("CallDate",  "DATE"),
            ]
        )
        client.load_table_from_dataframe(to_push, TABLE_ID, job_config=job_config).result()
        print("✅ Pipeline successful.")
    else:
        print("No new unique records to push.")

if __name__ == "__main__":
    run_pipeline()
