import requests
import pandas as pd
import pytz
import os
from datetime import datetime
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

DEDUP_KEYS = ["ProspectID", "Old_Contact_Stage", "New_Contact_Stage", "OwnerId"]
FINAL_COLS = [
    "Timestamp", "ProspectID", "FirstName", "LastName", "EmailAddress",
    "Phone", "Alternate_Phone_Number", "Source", "Old_Contact_Stage",
    "New_Contact_Stage", "OwnerId", "OwnerIdEmailAddress", "Stage_Changed_On",
    "ModifiedByName", "StageChangeComment", "Date", "Stage_Changed_By",
]

india  = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()


# ─── FETCH SHEET ─────────────────────────────────────────
def fetch_sheet() -> pd.DataFrame:
    """Pull full public CSV (first row = header, rest = data)."""
    print("Fetching Google Sheet...")
    df = pd.read_csv(SHEET_URL, dtype=str)
    print(f"Rows downloaded: {len(df):,}")

    # Vectorized timestamp parse: DD/MM/YYYY HH:MM:SS in IST
    df["Timestamp"] = pd.to_datetime(
        df["Timestamp"], format="%d/%m/%Y %H:%M:%S", errors="coerce"
    )
    df["Timestamp"] = df["Timestamp"].dt.tz_localize(
        india, ambiguous="NaT", nonexistent="NaT"
    )

    before = len(df)
    df = df.dropna(subset=["Timestamp"])
    print(f"Rows with valid Timestamp: {len(df):,} (dropped {before - len(df):,})")
    return df


# ─── FETCH LSQ OWNER MAP ─────────────────────────────────
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

    print(f"LSQ owner map loaded: {len(owner_map):,} users")
    return owner_map


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
    print("LSQ Stage Change Pipeline — FULL TRUNCATE MODE")
    print("=" * 55)
    t_start = datetime.now(india)

    # 1. Fetch sheet
    df = fetch_sheet()

    if df.empty:
        print("⚠️  Sheet is empty. Exiting without truncating.")
        log_pipeline(0)
        return

    # 2. Normalise dedup-key columns to string
    for col in DEDUP_KEYS:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # 3. Dedup within the sheet — keep LAST occurrence per key combo
    before = len(df)
    df = df.drop_duplicates(subset=DEDUP_KEYS, keep="last")
    print(f"After dedup on {DEDUP_KEYS}: {before:,} → {len(df):,} rows")

    # 4. Fetch LSQ owner map
    owner_map = fetch_owner_map()

    # 5. Vectorized Date column (IST date, before UTC conversion)
    df["Date"] = df["Timestamp"].dt.tz_convert(india).dt.date

    # 6. Vectorized Stage_Changed_By
    #    If ModifiedByName is a real name → use it
    #    Else fallback to owner_map[OwnerId], else OwnerId itself
    system_values = {"system", "", "nan", "none", "null"}
    modified      = df["ModifiedByName"].astype(str).str.strip()
    owner_id_str  = df["OwnerId"].astype(str).str.strip()

    is_system = modified.str.lower().isin(system_values)
    fallback  = owner_id_str.map(owner_map).fillna(owner_id_str)

    df["Stage_Changed_By"] = modified.where(~is_system, fallback)

    # 7. Vectorized phone cleaning
    for phone_col in ["Phone", "Alternate_Phone_Number"]:
        if phone_col in df.columns:
            df[phone_col] = (
                df[phone_col].astype(str)
                .str.replace("+91", "", regex=False)
                .str.strip()
                .str[-10:]
            )

    # 8. Convert Timestamp → UTC (vectorized) for BQ storage
    df["Timestamp"] = df["Timestamp"].dt.tz_convert(pytz.utc)

    # 9. Ensure all final columns exist
    for col in FINAL_COLS:
        if col not in df.columns:
            df[col] = None

    df_final = df[FINAL_COLS].copy()

    # 10. Load to BigQuery (FULL TRUNCATE)
    print(f"Loading {len(df_final):,} rows to BigQuery (WRITE_TRUNCATE)...")
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(df_final, TABLE_ID, job_config=job_config).result()

    log_pipeline(len(df_final))

    elapsed = (datetime.now(india) - t_start).total_seconds()
    print("=" * 55)
    print(f"✅ Pipeline complete. Inserted: {len(df_final):,} rows in {elapsed:.1f}s")
    print("=" * 55)


if __name__ == "__main__":
    run_pipeline()
