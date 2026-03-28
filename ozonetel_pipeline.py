
import os
import time
import math
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta
from google.cloud import bigquery
from google.oauth2 import service_account



# Ozonetel API
API_URL  = "https://in1-ccaas-api.ozonetel.com/ca_reports/getCDRDefaultByPagination"
API_KEY  = "YOUR_API_KEY"       
USERNAME = "YOUR_USERNAME"        

# BigQuery
PROJECT_ID = "studious-apex-488820-c3"
DATASET_ID = "crm_dashboard"
TABLE_NAME = "ozonetel_calls"
TABLE_ID   = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_NAME}"
TEAM_SHEET_URL = "https://docs.google.com/spreadsheets/d/e/YOUR_SHEET_ID/pub?gid=0&single=true&output=csv"
SERVICE_ACCOUNT_FILE = "/content/drive/MyDrive/credentials/bigquery_key.json"

# Pipeline behaviour
PAGE_SIZE         = 500   
RATE_LIMIT_SLEEP  = 32    
MAX_LOOKBACK_DAYS = 2   
LAG_MINUTES       = 5    

india = pytz.timezone("Asia/Kolkata")


# ─────────────────────────────────────────────
# BIGQUERY CLIENT SETUP
# ─────────────────────────────────────────────

def get_bq_client():
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return bigquery.Client(credentials=credentials, project=PROJECT_ID)
    else:
        # Falls back to Application Default Credentials (GCE / Cloud Run / env var)
        return bigquery.Client(project=PROJECT_ID)

client = get_bq_client()


# ─────────────────────────────────────────────
# HELPER: Get last date already in BigQuery
# ─────────────────────────────────────────────

def get_last_date_from_bq():
    """
    Returns the most recent CallDate in BigQuery as a date object.
    Falls back to today - MAX_LOOKBACK_DAYS if table is empty or query fails.
    """
    fallback = datetime.now(india).date() - timedelta(days=MAX_LOOKBACK_DAYS)
    try:
        query  = f"SELECT MAX(CallDate) AS last_date FROM `{TABLE_ID}`"
        result = client.query(query).to_dataframe()
        val    = result["last_date"].iloc[0]
        if pd.isna(val):
            print("ℹ️  BigQuery table is empty — using fallback start date.")
            return fallback
        return pd.to_datetime(val).date()
    except Exception as e:
        print(f"⚠️  Could not query BQ for last date: {e}. Using fallback.")
        return fallback


# ─────────────────────────────────────────────
# HELPER: Safe start date (prevents stale loops)
# ─────────────────────────────────────────────

def get_safe_start_date():
    """
    Caps the start date to MAX_LOOKBACK_DAYS ago.
    Prevents the loop burning rate-limit slots on old dates when
    yesterday had zero calls and BQ's max date is 2+ days back.
    """
    bq_last   = get_last_date_from_bq()
    today     = datetime.now(india).date()
    floor     = today - timedelta(days=MAX_LOOKBACK_DAYS)
    safe      = max(bq_last, floor)
    print(f"📅 BQ last date : {bq_last}")
    print(f"📅 Safe start   : {safe}  (floor = {floor}, today = {today})")
    return safe


# ─────────────────────────────────────────────
# HELPER: Convert HH:MM:SS duration to seconds
# ─────────────────────────────────────────────

def hhmmss_to_seconds(val):
    try:
        if pd.isna(val) or str(val).strip() in ("", "00:00:00"):
            return 0
        parts = str(val).strip().split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        return 0


# ─────────────────────────────────────────────
# HELPER: Process raw API details for one day
# ─────────────────────────────────────────────

def process_day_data(details, team_agents_clean):
    """
    Converts the raw Ozonetel API detail list into a clean DataFrame.
    Only keeps records where AgentName is in the team roster.
    Returns an empty DataFrame if nothing matches.
    """
    if not details:
        return pd.DataFrame()

    df = pd.DataFrame(details)

    # ── Rename fields to match BQ schema ──
    rename_map = {
        "CallID"      : "CallID",
        "AgentName"   : "AgentName",
        "CallerID"    : "phone_number",
        "StartTime"   : "StartTime",
        "EndTime"     : "EndTime",
        "Duration"    : "Duration",
        "TalkTime"    : "TalkTime",
        "DialStatus"  : "Status",
        "Type"        : "Type",
        "Disposition" : "Disposition",
        "CampaignName": "CampaignName",
        "HangupBy"    : "HangupBy",
        "Location"    : "Location",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # ── Convert durations to seconds ──
    df["duration_sec"]  = df["Duration"].apply(hhmmss_to_seconds)  if "Duration"  in df.columns else 0
    df["talktime_sec"]  = df["TalkTime"].apply(hhmmss_to_seconds)  if "TalkTime"  in df.columns else 0

    # ── Standardise text fields ──
    for col in ["Status", "Type", "AgentName"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # ── Filter: only keep team agents ──
    if team_agents_clean:
        df = df[df["AgentName"].isin(team_agents_clean)].copy()

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# CORE: Paginated fetch for a single day
# ─────────────────────────────────────────────

def fetch_day_data_paginated(day_str, start_time_str, end_time_str, headers):
    """
    Fetches ALL records for a single day using pagination.
    - Respects the 2 req/min rate limit (RATE_LIMIT_SLEEP between pages)
    - Retries automatically on 429 with 65-second backoff
    - Returns a flat list of raw record dicts
    """
    all_details = []
    page_no     = 1

    while True:
        payload = {
            "fromDate" : f"{day_str} {start_time_str}",
            "toDate"   : f"{day_str} {end_time_str}",
            "userName" : USERNAME,
        }
        params = {"pageNo": page_no, "pageSize": PAGE_SIZE}

        try:
            res = requests.get(
                API_URL, headers=headers,
                json=payload, params=params, timeout=60
            )

            # ── 429: rate limited — wait and retry same page ──
            if res.status_code == 429:
                print(f"  ⚠️  429 Rate limited ({day_str} p{page_no}). "
                      f"Waiting 65s before retry…")
                time.sleep(65)
                continue   # retry the same page

            data = res.json()

            # ── API-level error response ──
            status_val = str(data.get("status", "")).lower()
            if status_val not in ("success", "true"):
                print(f"  ❌ API error on {day_str} p{page_no}: {data.get('message')}")
                break

            details     = data.get("details", [])
            total_count = int(data.get("totalCount", 0))

            if not details:
                print(f"  ℹ️  {day_str} p{page_no}: empty response "
                      f"(totalCount={total_count})")
                break

            all_details.extend(details)
            fetched = len(all_details)
            print(f"  ✅ {day_str} p{page_no}: +{len(details)} records "
                  f"({fetched}/{total_count})")

            # ── All pages consumed ──
            if fetched >= total_count or len(details) < PAGE_SIZE:
                break

            # ── More pages: sleep before next request ──
            page_no += 1
            print(f"  ⏳ Sleeping {RATE_LIMIT_SLEEP}s (rate limit)…")
            time.sleep(RATE_LIMIT_SLEEP)

        except Exception as e:
            print(f"  ❌ Exception on {day_str} p{page_no}: {e}")
            break

    return all_details


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline():
    print("=" * 60)
    print("  OZONETEL → BIGQUERY PIPELINE STARTED")
    print("=" * 60)

    # ── Load team roster ──
    try:
        team_df           = pd.read_csv(TEAM_SHEET_URL)
        team_agents       = team_df.iloc[:, 2].dropna().unique().tolist()
        team_agents_clean = [str(x).strip() for x in team_agents]
        print(f"👥 Team agents loaded: {len(team_agents_clean)}")
    except Exception as e:
        print(f"❌ Failed to load team sheet: {e}")
        return

    # ── Determine date range ──
    start_date    = get_safe_start_date()
    batch_now_utc = datetime.now(pytz.utc)
    fetch_to_ist  = datetime.now(india) - timedelta(minutes=LAG_MINUTES)
    today_date    = fetch_to_ist.date()

    headers = {
        "Content-Type" : "application/json",
        "apiKey"       : API_KEY,
    }

    # ── Day loop ──
    all_data = []
    curr     = start_date

    while curr <= today_date:
        day_str        = curr.strftime("%Y-%m-%d")
        start_time_str = "00:00:00"
        end_time_str   = (fetch_to_ist.strftime("%H:%M:%S")
                          if curr == today_date else "23:59:59")

        print(f"\n🔄 Day: {day_str}  [{start_time_str} → {end_time_str}]")

        raw = fetch_day_data_paginated(
            day_str, start_time_str, end_time_str, headers
        )

        if raw:
            processed = process_day_data(raw, team_agents_clean)
            if not processed.empty:
                processed["CallDate"] = curr
                all_data.append(processed)
                print(f"  📦 {len(processed)} processable records for {day_str}")
            else:
                print(f"  ⏭️  {day_str}: records fetched but none matched team roster")
        else:
            print(f"  ⏭️  {day_str}: no records from API — skipping")

        curr += timedelta(days=1)

        # Sleep between days (page-level sleep is inside fetch_day_data_paginated)
        if curr <= today_date:
            print(f"\n  ⏳ Sleeping {RATE_LIMIT_SLEEP}s before next day…")
            time.sleep(RATE_LIMIT_SLEEP)

    # ── Nothing fetched ──
    if not all_data:
        print("\n⚠️  No valid data found for the range. Exiting.")
        return

    final_df = pd.concat(all_data, ignore_index=True)
    print(f"\n📊 Total records after concat: {len(final_df)}")

    # ── Filter: remove missed inbound calls ──
    before = len(final_df)
    final_df = final_df[
        ~(
            (final_df["Type"].str.lower()   == "inbound") &
            (final_df["Status"].str.lower() == "unanswered")
        )
    ].copy()
    print(f"🗑️  Removed {before - len(final_df)} missed-inbound rows → {len(final_df)} remain")

    if final_df.empty:
        print("⚠️  No records left after filtering.")
        return

    # ── Standardise Timestamps: IST → UTC ──
    final_df["CallDate_str"] = final_df["CallDate"].astype(str)
    for col in ["StartTime", "EndTime"]:
        if col in final_df.columns:
            final_df[col] = pd.to_datetime(
                final_df["CallDate_str"] + " " + final_df[col].astype(str),
                errors="coerce"
            )
            final_df[col] = (
                final_df[col]
                .dt.tz_localize(india, ambiguous="infer", nonexistent="shift_forward")
                .dt.tz_convert("UTC")
            )
    final_df.drop(columns=["CallDate_str"], inplace=True)
    final_df["CallDate"] = pd.to_datetime(final_df["CallDate"]).dt.date

    # ── Add metadata ──
    final_df["updated_at_ampm"] = fetch_to_ist.strftime("%d/%m/%Y %I:%M %p")
    final_df["updated_at_utc"]  = batch_now_utc
    final_df["CallID"]          = final_df["CallID"].astype(str)
    final_df["duration_sec"]    = pd.to_numeric(final_df["duration_sec"], errors="coerce").fillna(0).astype(int)
    final_df["talktime_sec"]    = pd.to_numeric(final_df["talktime_sec"], errors="coerce").fillna(0).astype(int)

    # ── Incremental dedup: Triple Key (CallID + StartTime + EndTime) ──
    dedup_query = f"""
        SELECT
            CAST(CallID AS STRING) AS CallID,
            StartTime,
            EndTime
        FROM `{TABLE_ID}`
        WHERE CallDate >= '{start_date}'
    """

    try:
        existing_bq = client.query(dedup_query).to_dataframe()
    except Exception as e:
        print(f"⚠️  Could not fetch existing BQ records for dedup: {e}")
        print("   Proceeding without dedup — duplicates may occur.")
        existing_bq = pd.DataFrame()

    if not existing_bq.empty:
        existing_bq["StartTime"] = pd.to_datetime(existing_bq["StartTime"], utc=True)
        existing_bq["EndTime"]   = pd.to_datetime(existing_bq["EndTime"],   utc=True)
        final_df["StartTime"]    = pd.to_datetime(final_df["StartTime"],    utc=True)
        final_df["EndTime"]      = pd.to_datetime(final_df["EndTime"],      utc=True)

        merged  = final_df.merge(
            existing_bq,
            on=["CallID", "StartTime", "EndTime"],
            how="left",
            indicator=True
        )
        to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
        print(f"\n🔍 Dedup: {len(final_df)} fetched  →  "
              f"{len(final_df) - len(to_push)} already in BQ  →  "
              f"{len(to_push)} new records to push")
    else:
        to_push = final_df
        print(f"\n🔍 No existing BQ records from {start_date}. "
              f"Pushing all {len(to_push)} records.")

    # ── Push to BigQuery ──
    if not to_push.empty:
        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema=[
                bigquery.SchemaField("StartTime",  "TIMESTAMP"),
                bigquery.SchemaField("EndTime",    "TIMESTAMP"),
                bigquery.SchemaField("CallDate",   "DATE"),
            ]
        )
        try:
            job = client.load_table_from_dataframe(
                to_push, TABLE_ID, job_config=job_config
            )
            job.result()   # waits for completion
            print(f"✅ Successfully pushed {len(to_push)} records to BigQuery.")
        except Exception as e:
            print(f"❌ BigQuery push failed: {e}")
            raise
    else:
        print("✅ BigQuery is already up to date — nothing to push.")

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_pipeline()
