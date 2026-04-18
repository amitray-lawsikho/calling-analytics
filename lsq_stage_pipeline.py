import requests
import pandas as pd
import pytz
import os
from datetime import datetime, timedelta
from google.cloud import bigquery

# ─── CONFIG ───────────────────────────────────────────────
PROJECT_ID  = "studious-apex-488820-c3"
DATASET     = "crm_dashboard"
TABLE       = "stage_changed"
TABLE_ID    = f"{PROJECT_ID}.{DATASET}.{TABLE}"
LOG_TABLE   = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vQx3vMVDA8FnTtUNIawCN_DljdbkRRMcOdgAM5iCOiQ4Uy_R2Y5KnwytCqGyMpAr1FMgvulDfo5e2rc"
    "/pub?gid=1843414438&single=true&output=csv"
)

LSQ_ACCESS_KEY = os.environ["LSQ_ACCESS_KEY"]
LSQ_SECRET_KEY = os.environ["LSQ_SECRET_KEY"]

DEDUP_KEYS  = ["ProspectID", "Old_Contact_Stage", "New_Contact_Stage", "OwnerId"]
FINAL_COLS  = [
    "Timestamp", "ProspectID", "FirstName", "LastName", "EmailAddress",
    "Phone", "Alternate_Phone_Number", "Source", "Old_Contact_Stage",
    "New_Contact_Stage", "OwnerId", "OwnerIdEmailAddress", "Stage_Changed_On",
    "ModifiedByName", "StageChangeComment", "Date", "Stage_Changed_By",
]

india = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()


# ─── STEP 1: Last Timestamp from BQ ──────────────────────
def get_last_timestamp() -> datetime:
    """Returns last BQ Timestamp in IST, minus 5 min overlap."""
    try:
        query = f"SELECT MAX(Timestamp) AS last_ts FROM `{TABLE_ID}`"
        df    = client.query(query).to_dataframe()
        last_ts = df.iloc[0]["last_ts"]

        if pd.isna(last_ts):
            print("BQ table empty — defaulting to 60 days back.")
            return datetime.now(india) - timedelta(days=60)

        if last_ts.tzinfo is None:
            last_ts = pytz.utc.localize(last_ts)

        ist_ts = last_ts.astimezone(india) - timedelta(minutes=5)
        print(f"Last BQ Timestamp (IST, −5 min): {ist_ts}")
        return ist_ts

    except Exception as e:
        print(f"⚠️  Could not fetch last timestamp: {e} — defaulting to 60 days back.")
        return datetime.now(india) - timedelta(days=60)


# ─── STEP 2: Fetch & Filter Sheet ────────────────────────
def fetch_sheet(last_ts_ist: datetime) -> pd.DataFrame:
    """Pull public CSV and keep rows newer than last_ts_ist."""
    print("Fetching Google Sheet...")
    df = pd.read_csv(SHEET_URL, dtype=str)

    # Parse IST Timestamp: DD/MM/YYYY HH:MM:SS
    df["Timestamp"] = pd.to_datetime(
        df["Timestamp"], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    df["Timestamp"] = df["Timestamp"].apply(
        lambda x: india.localize(x) if pd.notna(x) and x.tzinfo is None else x
    )

    # Drop rows with unparseable Timestamp
    df = df.dropna(subset=["Timestamp"])

    # Incremental filter
    df = df[df["Timestamp"] > last_ts_ist].copy()
    print(f"Sheet rows after incremental filter: {len(df)}")
    return df


# ─── STEP 3: Fetch LSQ Owner Map ─────────────────────────
def fetch_owner_map() -> dict:
    """Returns {OwnerId: 'Full Name'} from LSQ Users API."""
    url    = "https://api-in21.leadsquared.com/v2/UserManagement.svc/Users.Get"
    params = {"accessKey": LSQ_ACCESS_KEY, "secretKey": LSQ_SECRET_KEY}
    resp   = requests.get(url, params=params, timeout=30)
    users  = resp.json()

    if not isinstance(users, list):
        raise Exception(f"LSQ Users API returned unexpected response: {users}")

    owner_map = {}
    for u in users:
        uid  = str(u.get("ID", "")).strip()
        name = f"{u.get('FirstName', '')} {u.get('LastName', '')}".strip()
        if uid:
            owner_map[uid] = name

    print(f"LSQ owner map loaded: {len(owner_map)} users")
    return owner_map


# ─── STEP 4: Existing BQ keys in window ──────────────────
def get_existing_bq_keys(last_ts_ist: datetime) -> pd.DataFrame:
    """Fetch dedup-key columns from BQ for the incremental window."""
    start_utc = last_ts_ist.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
    query = f"""
        SELECT ProspectID, Old_Contact_Stage, New_Contact_Stage, OwnerId
        FROM `{TABLE_ID}`
        WHERE Timestamp >= TIMESTAMP('{start_utc}')
    """
    try:
        bq_df = client.query(query).to_dataframe()
        # Ensure string dtype to match sheet df
        for col in DEDUP_KEYS:
            bq_df[col] = bq_df[col].astype(str).str.strip()
        print(f"BQ existing keys in window: {len(bq_df)}")
        return bq_df
    except Exception as e:
        print(f"⚠️  Could not fetch BQ keys: {e}")
        return pd.DataFrame(columns=DEDUP_KEYS)


# ─── STEP 5: Stage_Changed_By ────────────────────────────
_SYSTEM_VALUES = {"system", "", "nan", "none", "null"}

def build_stage_changed_by(row: pd.Series, owner_map: dict) -> str:
    modified = str(row.get("ModifiedByName", "")).strip()
    if modified.lower() not in _SYSTEM_VALUES:
        return modified
    # Fallback: map OwnerId → name
    owner_id = str(row.get("OwnerId", "")).strip()
    return owner_map.get(owner_id, owner_id)  # return OwnerId itself if not mapped


# ─── LOG ─────────────────────────────────────────────────
def log_pipeline(rows: int):
    ist_now = datetime.now(india)
    df_log  = pd.DataFrame([{
        "run_date":       ist_now.date(),
        "pipeline_name":  "LSQ_STAGE",
        "run_timestamp":  ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted":  int(rows),
    }])
    client.load_table_from_dataframe(
        df_log, LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    ).result()


# ─── MAIN ────────────────────────────────────────────────
def run_pipeline():
    print("=" * 55)
    print("LSQ Stage Change Pipeline — STARTED")
    print("=" * 55)

    # 1. Last BQ Timestamp
    last_ts_ist = get_last_timestamp()

    # 2. Sheet data (incremental)
    df = fetch_sheet(last_ts_ist)
    if df.empty:
        print("No new rows from sheet. Exiting.")
        log_pipeline(0)
        return

    # 3. Normalise key columns to string (required for dedup merge)
    for col in DEDUP_KEYS:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # 4. Dedup within the sheet (keep LAST occurrence per key combo)
    before = len(df)
    df = df.drop_duplicates(subset=DEDUP_KEYS, keep="last")
    print(f"Sheet-internal dedup: {before} → {len(df)} rows")

    # 5. Dedup against BQ
    bq_keys = get_existing_bq_keys(last_ts_ist)
    if not bq_keys.empty:
        for col in DEDUP_KEYS:
            bq_keys[col] = bq_keys[col].astype(str).str.strip()
        merged = df.merge(bq_keys, on=DEDUP_KEYS, how="left", indicator=True)
        df = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"])
    print(f"After BQ dedup: {len(df)} rows remain")

    if df.empty:
        print("No new unique records. Exiting.")
        log_pipeline(0)
        return

    # 6. Fetch LSQ owner map
    owner_map = fetch_owner_map()

    # 7. Date column (IST date, extracted BEFORE converting to UTC)
    df["Date"] = df["Timestamp"].apply(
        lambda x: x.astimezone(india).date() if pd.notna(x) else None
    )

    # 8. Stage_Changed_By
    df["Stage_Changed_By"] = df.apply(
        lambda row: build_stage_changed_by(row, owner_map), axis=1
    )

    # 9. Clean phone numbers
    for phone_col in ["Phone", "Alternate_Phone_Number"]:
        if phone_col in df.columns:
            df[phone_col] = (
                df[phone_col].astype(str)
                .str.replace("+91", "", regex=False)
                .str.strip()
                .str[-10:]
            )

    # 10. Convert Timestamp to UTC for BQ storage
    df["Timestamp"] = df["Timestamp"].apply(
        lambda x: x.astimezone(pytz.utc) if pd.notna(x) else None
    )
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], utc=True)

    # 11. Ensure all final columns exist
    for col in FINAL_COLS:
        if col not in df.columns:
            df[col] = None

    df_final = df[FINAL_COLS].copy()

    # 12. Load to BigQuery
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    client.load_table_from_dataframe(df_final, TABLE_ID, job_config=job_config).result()

    log_pipeline(len(df_final))

    print("=" * 55)
    print(f"✅ Pipeline complete. Inserted: {len(df_final)} rows")
    print("=" * 55)


if __name__ == "__main__":
    run_pipeline()
