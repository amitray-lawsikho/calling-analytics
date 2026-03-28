# =============================================================
#  OZONETEL → BIGQUERY PIPELINE 
# =============================================================

import os
import time
import requests
import pandas as pd
import pytz
from datetime import datetime, timedelta
from google.cloud import bigquery
from google.oauth2 import service_account

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

API_URL  = "https://in1-ccaas-api.ozonetel.com/ca_reports/getCDRDefaultByPagination"
API_KEY  = "YOUR_API_KEY"
USERNAME = "YOUR_USERNAME"

PROJECT_ID = "studious-apex-488820-c3"
DATASET_ID = "crm_dashboard"
TABLE_NAME = "ozonetel_calls"
TABLE_ID   = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_NAME}"

TEAM_SHEET_URL       = "https://docs.google.com/spreadsheets/d/e/YOUR_SHEET_ID/pub?gid=0&single=true&output=csv"
SERVICE_ACCOUNT_FILE = "/content/drive/MyDrive/credentials/bigquery_key.json"

# ── Pipeline tuning ──
PAGE_SIZE         = 100   # SAFE: reduced from 500 — some APIs reject large page sizes
RATE_LIMIT_SLEEP  = 32    # seconds between requests (2 req/min limit)
MAX_LOOKBACK_DAYS = 2
LAG_MINUTES       = 5

# ── Set to True to print raw API response for debugging ──
DEBUG_MODE = True

india = pytz.timezone("Asia/Kolkata")


# ─────────────────────────────────────────────
# BIGQUERY CLIENT
# ─────────────────────────────────────────────

def get_bq_client():
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        credentials = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return bigquery.Client(credentials=credentials, project=PROJECT_ID)
    return bigquery.Client(project=PROJECT_ID)

client = get_bq_client()


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_last_date_from_bq():
    fallback = datetime.now(india).date() - timedelta(days=MAX_LOOKBACK_DAYS)
    try:
        result = client.query(f"SELECT MAX(CallDate) AS last_date FROM `{TABLE_ID}`").to_dataframe()
        val    = result["last_date"].iloc[0]
        if pd.isna(val):
            print("ℹ️  BQ table is empty — using fallback start date.")
            return fallback
        return pd.to_datetime(val).date()
    except Exception as e:
        print(f"⚠️  Could not query BQ: {e}. Using fallback.")
        return fallback


def get_safe_start_date():
    bq_last = get_last_date_from_bq()
    today   = datetime.now(india).date()
    floor   = today - timedelta(days=MAX_LOOKBACK_DAYS)
    safe    = max(bq_last, floor)
    print(f"📅 BQ last date : {bq_last}")
    print(f"📅 Safe start   : {safe}  (floor={floor}, today={today})")
    return safe


def hhmmss_to_seconds(val):
    try:
        if pd.isna(val) or str(val).strip() in ("", "00:00:00"):
            return 0
        parts = str(val).strip().split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        return 0


# ─────────────────────────────────────────────
# SINGLE API CALL WITH FULL DIAGNOSTICS
# ─────────────────────────────────────────────

def call_api_once(day_str, start_time_str, end_time_str, headers, page_no):
    """
    Makes a single API request and returns (details_list, total_count, success_bool).
    Prints the raw response when DEBUG_MODE is True so you can see exactly what's coming back.
    """
    payload = {
        "fromDate" : f"{day_str} {start_time_str}",
        "toDate"   : f"{day_str} {end_time_str}",
        "userName" : USERNAME,
    }
    params = {"pageNo": page_no, "pageSize": PAGE_SIZE}

    if DEBUG_MODE and page_no == 1:
        print(f"\n  🔬 DEBUG — Request payload : {payload}")
        print(f"  🔬 DEBUG — Query params    : {params}")
        print(f"  🔬 DEBUG — Headers         : { {k: v[:8]+'...' if k == 'apiKey' else v for k, v in headers.items()} }")

    try:
        res = requests.get(
            API_URL, headers=headers,
            json=payload, params=params, timeout=60
        )

        if DEBUG_MODE and page_no == 1:
            print(f"  🔬 DEBUG — HTTP status     : {res.status_code}")
            # Print first 500 chars of raw response so we can see what came back
            raw_text = res.text[:500]
            print(f"  🔬 DEBUG — Raw response    : {raw_text}")

        # ── 429 Rate limit ──
        if res.status_code == 429:
            print(f"  ⚠️  429 Rate limited. Waiting 65s…")
            time.sleep(65)
            return None, 0, False   # caller should retry

        # ── Parse JSON safely ──
        try:
            data = res.json()
        except Exception as json_err:
            print(f"  ❌ JSON parse failed: {json_err}")
            print(f"     Raw response: {res.text[:300]}")
            return [], 0, False

        if DEBUG_MODE and page_no == 1:
            print(f"  🔬 DEBUG — status field    : {data.get('status')!r}")
            print(f"  🔬 DEBUG — message field   : {data.get('message')!r}")
            print(f"  🔬 DEBUG — totalCount      : {data.get('totalCount')!r}")
            details_preview = data.get("details", [])
            print(f"  🔬 DEBUG — details count   : {len(details_preview)}")
            if details_preview:
                print(f"  🔬 DEBUG — first record keys: {list(details_preview[0].keys())}")
                print(f"  🔬 DEBUG — first record AgentName: {details_preview[0].get('AgentName')!r}")

        # ── Check API-level status ──
        # Handle all common success representations: "success", "Success", True, "true", "1"
        raw_status = data.get("status", "")
        is_success = str(raw_status).strip().lower() in ("success", "true", "1")

        if not is_success:
            print(f"  ❌ API returned non-success status: {raw_status!r}")
            print(f"     Message: {data.get('message', 'no message')!r}")
            return [], 0, False

        details     = data.get("details", [])
        total_count = int(data.get("totalCount", 0))

        return details, total_count, True

    except requests.exceptions.Timeout:
        print(f"  ❌ Request timed out on {day_str} p{page_no}")
        return [], 0, False
    except Exception as e:
        print(f"  ❌ Unexpected exception: {e}")
        return [], 0, False


# ─────────────────────────────────────────────
# PAGINATED FETCH FOR ONE DAY
# ─────────────────────────────────────────────

def fetch_day_data_paginated(day_str, start_time_str, end_time_str, headers):
    all_details = []
    page_no     = 1

    while True:
        details, total_count, success = call_api_once(
            day_str, start_time_str, end_time_str, headers, page_no
        )

        # None means 429 — retry same page
        if details is None:
            continue

        if not success:
            break

        if not details:
            print(f"  ℹ️  {day_str} p{page_no}: API success but 0 records (totalCount={total_count})")
            break

        all_details.extend(details)
        fetched = len(all_details)
        print(f"  ✅ {day_str} p{page_no}: +{len(details)} records ({fetched}/{total_count})")

        if fetched >= total_count or len(details) < PAGE_SIZE:
            break

        page_no += 1
        print(f"  ⏳ Sleeping {RATE_LIMIT_SLEEP}s before next page…")
        time.sleep(RATE_LIMIT_SLEEP)

    return all_details


# ─────────────────────────────────────────────
# PROCESS RAW RECORDS INTO DATAFRAME
# ─────────────────────────────────────────────

def process_day_data(details, team_agents_clean):
    if not details:
        return pd.DataFrame()

    df = pd.DataFrame(details)

    if DEBUG_MODE:
        print(f"\n  🔬 DEBUG — Raw columns from API : {list(df.columns)}")
        print(f"  🔬 DEBUG — Total raw records    : {len(df)}")
        if "AgentName" in df.columns:
            unique_agents = df["AgentName"].dropna().unique()[:10]
            print(f"  🔬 DEBUG — Sample AgentNames    : {list(unique_agents)}")
        if team_agents_clean:
            print(f"  🔬 DEBUG — Sample team agents   : {team_agents_clean[:10]}")

    # ── Rename API fields → BQ column names ──
    rename_map = {
        "CallID"      : "CallID",
        "AgentName"   : "AgentName",
        "CallerID"    : "phone_number",
        "StartTime"   : "StartTime",
        "EndTime"     : "EndTime",
        "Duration"    : "Duration",
        "TalkTime"    : "TalkTime",
        "DialStatus"  : "Status",       # API uses DialStatus not Status
        "Type"        : "Type",
        "Disposition" : "Disposition",
        "CampaignName": "CampaignName",
        "HangupBy"    : "HangupBy",
        "Location"    : "Location",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # ── Duration conversions ──
    df["duration_sec"] = df["Duration"].apply(hhmmss_to_seconds) if "Duration" in df.columns else 0
    df["talktime_sec"] = df["TalkTime"].apply(hhmmss_to_seconds) if "TalkTime" in df.columns else 0

    # ── Standardise text ──
    for col in ["Status", "Type", "AgentName"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # ── Team filter ──
    # IMPORTANT: Only filter if team_agents_clean has entries AND AgentName column exists
    before_filter = len(df)
    if team_agents_clean and "AgentName" in df.columns:
        # Case-insensitive match to avoid misses due to casing differences
        df_agent_lower  = df["AgentName"].str.lower()
        team_lower      = [a.lower() for a in team_agents_clean]
        df = df[df_agent_lower.isin(team_lower)].copy()

        if DEBUG_MODE:
            print(f"  🔬 DEBUG — After team filter    : {len(df)}/{before_filter} records kept")
            if len(df) == 0:
                print("  ⚠️  DEBUG — TEAM FILTER REMOVED ALL RECORDS!")
                print("             Check that AgentName values above match your sheet column 3.")
    else:
        if DEBUG_MODE:
            print(f"  🔬 DEBUG — Team filter skipped (no roster or no AgentName col)")

    return df.reset_index(drop=True)


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def run_pipeline():
    print("=" * 60)
    print("  OZONETEL → BIGQUERY PIPELINE")
    print("=" * 60)

    # ── Load team roster ──
    try:
        team_df           = pd.read_csv(TEAM_SHEET_URL)
        if DEBUG_MODE:
            print(f"\n🔬 DEBUG — Sheet columns: {list(team_df.columns)}")
            print(f"🔬 DEBUG — Sheet shape  : {team_df.shape}")
            print(f"🔬 DEBUG — Col[2] sample: {team_df.iloc[:5, 2].tolist()}")

        team_agents       = team_df.iloc[:, 2].dropna().unique().tolist()
        team_agents_clean = [str(x).strip() for x in team_agents]
        print(f"👥 Team agents loaded: {len(team_agents_clean)}")
        if DEBUG_MODE:
            print(f"🔬 DEBUG — First 10 agents: {team_agents_clean[:10]}")
    except Exception as e:
        print(f"❌ Failed to load team sheet: {e}")
        return

    # ── Date range ──
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
        end_time_str   = fetch_to_ist.strftime("%H:%M:%S") if curr == today_date else "23:59:59"

        print(f"\n{'─'*50}")
        print(f"🔄 Fetching {day_str}  [{start_time_str} → {end_time_str}]")

        raw = fetch_day_data_paginated(day_str, start_time_str, end_time_str, headers)

        if raw:
            processed = process_day_data(raw, team_agents_clean)
            if not processed.empty:
                processed["CallDate"] = curr
                all_data.append(processed)
                print(f"  📦 {len(processed)} records queued for {day_str}")
            else:
                print(f"  ⚠️  {day_str}: {len(raw)} records fetched but 0 passed team filter")
                print(f"       → Check DEBUG output above: do AgentName values match your sheet?")
        else:
            print(f"  ⏭️  {day_str}: 0 records from API")

        curr += timedelta(days=1)

        if curr <= today_date:
            print(f"\n  ⏳ Sleeping {RATE_LIMIT_SLEEP}s before next day…")
            time.sleep(RATE_LIMIT_SLEEP)

    # ── Nothing to push ──
    if not all_data:
        print("\n" + "=" * 60)
        print("⚠️  PIPELINE DONE — No records to push.")
        print("   Check the DEBUG output above to find where data dropped out:")
        print("   1. API returned 0 records → wrong date/credentials")
        print("   2. Team filter removed all → AgentName mismatch with sheet")
        print("   3. API error → check status/message fields in DEBUG output")
        print("=" * 60)
        return

    final_df = pd.concat(all_data, ignore_index=True)
    print(f"\n📊 Total records after concat: {len(final_df)}")

    # ── Remove missed inbound ──
    before = len(final_df)
    final_df = final_df[
        ~(
            (final_df["Type"].str.lower()   == "inbound") &
            (final_df["Status"].str.lower() == "unanswered")
        )
    ].copy()
    print(f"🗑️  Removed {before - len(final_df)} missed-inbound → {len(final_df)} remain")

    if final_df.empty:
        print("⚠️  No records left after inbound filter.")
        return

    # ── IST → UTC timestamps ──
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

    # ── Metadata ──
    final_df["updated_at_ampm"] = fetch_to_ist.strftime("%d/%m/%Y %I:%M %p")
    final_df["updated_at_utc"]  = batch_now_utc
    final_df["CallID"]          = final_df["CallID"].astype(str)
    final_df["duration_sec"]    = pd.to_numeric(final_df["duration_sec"], errors="coerce").fillna(0).astype(int)
    final_df["talktime_sec"]    = pd.to_numeric(final_df["talktime_sec"], errors="coerce").fillna(0).astype(int)

    if DEBUG_MODE:
        print(f"\n🔬 DEBUG — Final DataFrame columns : {list(final_df.columns)}")
        print(f"🔬 DEBUG — Final DataFrame shape   : {final_df.shape}")
        print(f"🔬 DEBUG — StartTime sample        : {final_df['StartTime'].iloc[0] if 'StartTime' in final_df.columns else 'N/A'}")

    # ── Deduplication ──
    dedup_query = f"""
        SELECT CAST(CallID AS STRING) AS CallID, StartTime, EndTime
        FROM `{TABLE_ID}`
        WHERE CallDate >= '{start_date}'
    """
    try:
        existing_bq = client.query(dedup_query).to_dataframe()
        print(f"\n🔍 Existing BQ records from {start_date}: {len(existing_bq)}")
    except Exception as e:
        print(f"⚠️  Could not fetch BQ records for dedup: {e}. Pushing without dedup.")
        existing_bq = pd.DataFrame()

    if not existing_bq.empty:
        existing_bq["StartTime"] = pd.to_datetime(existing_bq["StartTime"], utc=True)
        existing_bq["EndTime"]   = pd.to_datetime(existing_bq["EndTime"],   utc=True)
        final_df["StartTime"]    = pd.to_datetime(final_df["StartTime"],    utc=True)
        final_df["EndTime"]      = pd.to_datetime(final_df["EndTime"],      utc=True)

        merged  = final_df.merge(
            existing_bq, on=["CallID", "StartTime", "EndTime"],
            how="left", indicator=True
        )
        to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
        print(f"🔍 {len(final_df)} fetched → {len(final_df)-len(to_push)} already in BQ → {len(to_push)} new")
    else:
        to_push = final_df
        print(f"🔍 No existing records from {start_date} → pushing all {len(to_push)}")

    # ── Push to BigQuery ──
    if not to_push.empty:
        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema=[
                bigquery.SchemaField("StartTime", "TIMESTAMP"),
                bigquery.SchemaField("EndTime",   "TIMESTAMP"),
                bigquery.SchemaField("CallDate",  "DATE"),
            ]
        )
        try:
            job = client.load_table_from_dataframe(to_push, TABLE_ID, job_config=job_config)
            job.result()
            print(f"\n✅ Successfully pushed {len(to_push)} records to BigQuery.")
        except Exception as e:
            print(f"\n❌ BigQuery push failed: {e}")
            raise
    else:
        print("\n✅ BigQuery is already up to date — nothing new to push.")

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_pipeline()
