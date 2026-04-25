import pandas as pd
import pytz
from datetime import datetime
from google.cloud import bigquery

# ─── CONFIG ───────────────────────────────────────────────
PROJECT_ID = "studious-apex-488820-c3"
DATASET    = "crm_dashboard"
TABLE      = "stage_changed"
TABLE_ID   = f"{PROJECT_ID}.{DATASET}.{TABLE}"
LOG_TABLE  = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"

SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vS_DlibGh09XYxLh8SOexTLstUmNPxdLc_2LuX-qUqh6ZbvmAbRxGtm8TaZwz6Zuyz5-04tXp55PLWD"
    "/pub?gid=0&single=true&output=csv"
)

# Dedup keys (sheet header names)
DEDUP_KEYS = [
    "ProspectID",
    "Old_Contact_Stage",
    "New_Contact_Stage",
    "Stage Changed By",
    "Stage_Changed_On",
]

# Final BigQuery column order
FINAL_COLS = [
    "Timestamp",
    "ProspectID",
    "FirstName",
    "LastName",
    "EmailAddress",
    "Phone",
    "Alternate_Phone_Number",
    "Source",
    "Old_Contact_Stage",
    "New_Contact_Stage",
    "Stage_Changed_On",
    "StageChangeComment",
    "Date",
    "Stage_Changed_By",
]

india  = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()


# ─── FETCH SHEET ─────────────────────────────────────────
def fetch_sheet() -> pd.DataFrame:
    """Pull full public CSV (first row = header, rest = data)."""
    print("Fetching Google Sheet...")
    df = pd.read_csv(SHEET_URL, dtype=str)
    print(f"Rows downloaded: {len(df):,}")

    # Apps Script writes Timestamp as a real Date object — Sheets renders it as
    # DD/MM/YYYY HH:MM:SS by default in IST. Parse accordingly.
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


# ─── LOG ─────────────────────────────────────────────────
def log_pipeline(rows: int):
    ist_now = datetime.now(india)
    df_log  = pd.DataFrame([{
        "run_date":      ist_now.date(),
        "pipeline_name": "LSQ_STAGE",
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows),
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
        else:
            print(f"⚠️  Dedup key '{col}' not found in sheet — skipping.")

    # 3. Dedup within the sheet — keep LAST occurrence per key combo
    available_keys = [k for k in DEDUP_KEYS if k in df.columns]
    before = len(df)
    df = df.drop_duplicates(subset=available_keys, keep="last")
    print(f"After dedup on {available_keys}: {before:,} → {len(df):,} rows")

    # 4. Vectorized Date column (IST date, before UTC conversion)
    df["Date"] = df["Timestamp"].dt.tz_convert(india).dt.date

    # 5. Vectorized phone cleaning — defensive (Apps Script already trims)
    for phone_col in ["Phone", "Alternate_Phone_Number"]:
        if phone_col in df.columns:
            df[phone_col] = (
                df[phone_col].astype(str)
                .str.replace("+91", "", regex=False)
                .str.replace("-", "", regex=False)
                .str.strip()
                .str[-10:]
            )

    # 6. LastName = Phone (per spec)
    df["LastName"] = df["Phone"]

    # 7. Rename sheet headers → BigQuery column names
    df = df.rename(columns={
        "Stage Changed By": "Stage_Changed_By",
        "BotComment":       "StageChangeComment",
    })

    # 8. Convert Timestamp → UTC (vectorized) for BQ storage
    df["Timestamp"] = df["Timestamp"].dt.tz_convert(pytz.utc)

    # 9. Ensure all final columns exist (missing ones become NULL)
    for col in FINAL_COLS:
        if col not in df.columns:
            df[col] = None

    df_final = df[FINAL_COLS].copy()

    # 10. Drop any rows where Date is NaT — partition column cannot be null
    before = len(df_final)
    df_final = df_final.dropna(subset=["Date"])
    if before != len(df_final):
        print(f"⚠️  Dropped {before - len(df_final):,} rows with null Date (partition column)")

    # 11. Load to BigQuery (FULL TRUNCATE) — preserve partition + cluster config
    print(f"Loading {len(df_final):,} rows to BigQuery (WRITE_TRUNCATE)...")
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE",
        time_partitioning=bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field="Date",
            expiration_ms=60 * 24 * 60 * 60 * 1000,  # 60 days
        ),
        clustering_fields=["Stage_Changed_By", "New_Contact_Stage"],
    )
    client.load_table_from_dataframe(df_final, TABLE_ID, job_config=job_config).result()

    log_pipeline(len(df_final))

    elapsed = (datetime.now(india) - t_start).total_seconds()
    print("=" * 55)
    print(f"✅ Pipeline complete. Inserted: {len(df_final):,} rows in {elapsed:.1f}s")
    print("=" * 55)


if __name__ == "__main__":
    run_pipeline()
