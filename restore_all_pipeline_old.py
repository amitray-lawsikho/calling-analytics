
import datetime
import pytz
import pandas as pd
from google.cloud import bigquery

# ---------------- CONFIG ----------------
PROJECT      = "studious-apex-488820-c3"
SRC_DATASET  = "crm_dashboard"
DST_DATASET  = "crm_dashboard_old"
LOG_TABLE    = f"{PROJECT}.crm_scheduled_results.MASTER_QUERY_REFRESH"

# Archive rows whose event_date is exactly this many days old.
# Must match the `>` cutoff used in test.py's unioned() helper if you ever
# switch from ROW_NUMBER dedup to a hard partition split.
ARCHIVE_AGE_DAYS = 55

# Per-table archive config.
# event_col   - the date column to filter on (already backticked if it has a space)
# key_cols    - natural-key columns used by NOT EXISTS to prevent duplicate inserts
TABLES = [
    {
        "src": "acefone_calls",
        "dst": "acefone_calls_old",
        "event_col": "`Call Date`",
        "key_cols": ["call_id"],
    },
    {
        "src": "mcube_calls",
        "dst": "mcube_calls_old",
        "event_col": "`Call Date`",
        "key_cols": ["call_id"],
    },
    {
        "src": "ozonetel_calls",
        "dst": "ozonetel_calls_old",
        "event_col": "CallDate",
        "key_cols": ["CallID"],
    },
    {
        "src": "stage_changed",
        "dst": "stage_changed_old",
        "event_col": "Date",
        # Matches DEDUP_KEYS in stage_changed_truncate.py (the active pipeline).
        # Stage_Changed_On is the LSQ-side change timestamp; combined with the
        # 4 other fields gives the same natural-key contract the source uses.
        "key_cols": [
            "ProspectID",
            "Old_Contact_Stage",
            "New_Contact_Stage",
            "Stage_Changed_By",
            "Stage_Changed_On",
        ],
    },
    {
        "src": "lsq_leads",
        "dst": "lsq_leads_old",
        "event_col": "AssignedOn",
        "key_cols": ["ProspectID"],
    },
    {
        "src": "manual_calls",
        "dst": "manual_calls_old",
        "event_col": "Call_Date",
        "key_cols": ["client_number", "Call_Date", "call_owner", "call_duration"],
    },
]

india  = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()


# ---------------- ARCHIVE ONE TABLE ----------------

def archive_table(cfg, today_date, target_event_date):
    """
    BQ sandbox forbids DML (`INSERT INTO ... SELECT`), so we use a query job
    with a destination table + WRITE_APPEND. This is a "load" operation that
    is permitted on the free tier.
    """
    src_fq    = f"`{PROJECT}.{SRC_DATASET}.{cfg['src']}`"
    dst_fq    = f"`{PROJECT}.{DST_DATASET}.{cfg['dst']}`"
    dst_ref   = f"{PROJECT}.{DST_DATASET}.{cfg['dst']}"
    event_col = cfg["event_col"]

    # NOT EXISTS join on natural key
    join_clause = " AND ".join(
        f"o.{k} = m.{k}" for k in cfg["key_cols"]
    )

    where_clause = f"""
        WHERE {event_col} = DATE '{target_event_date.isoformat()}'
          AND NOT EXISTS (
            SELECT 1
            FROM {dst_fq} AS o
            WHERE {join_clause}
          )
    """

    # Pre-count: how many rows will actually be inserted? (for accurate logging.
    # `result.total_rows` after a destination-write reports the dest's full count,
    # not the delta, so we need this separate query.)
    count_sql = f"SELECT COUNT(*) AS n FROM {src_fq} AS m {where_clause}"
    count_job = client.query(
        count_sql,
        job_config=bigquery.QueryJobConfig(use_query_cache=False),
    )
    to_insert = list(count_job.result())[0]["n"]

    if to_insert == 0:
        return 0

    # Actual archive: SELECT with destination + WRITE_APPEND (sandbox-safe alternative to DML).
    insert_sql = f"""
        SELECT m.*, DATE '{today_date.isoformat()}' AS archived_at
        FROM {src_fq} AS m
        {where_clause}
    """
    job_config = bigquery.QueryJobConfig(
        destination=dst_ref,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        use_query_cache=False,
    )
    client.query(insert_sql, job_config=job_config).result()

    return to_insert


# ---------------- LOG ----------------

def log_pipeline(rows_total, per_table_counts):
    ist_now = datetime.datetime.now(india)
    note    = ", ".join(f"{name}={n}" for name, n in per_table_counts.items())

    df_log = pd.DataFrame([{
        "run_date":      ist_now.date(),
        "pipeline_name": "ARCHIVE_OLD",
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows_total),
    }])

    client.load_table_from_dataframe(
        df_log,
        LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
    ).result()
    print(f"Logged ARCHIVE_OLD: total={rows_total} ({note})")


# ---------------- MAIN ----------------

def run_pipeline():
    # Capture today ONCE so all 6 tables share the same archived_at value
    # even if the script crosses midnight IST mid-run.
    today_date        = datetime.datetime.now(india).date()
    target_event_date = today_date - datetime.timedelta(days=ARCHIVE_AGE_DAYS)

    print(f"Archive run: today={today_date}, target_event_date={target_event_date}")
    print(f"Tables in scope: {len(TABLES)}")

    per_table_counts = {}
    rows_total       = 0

    for cfg in TABLES:
        try:
            n = archive_table(cfg, today_date, target_event_date)
            per_table_counts[cfg["src"]] = n
            rows_total += n
            print(f"  {cfg['src']}: +{n} rows")
        except Exception as e:
            print(f"  {cfg['src']}: ERROR — {e}")
            per_table_counts[cfg["src"]] = -1
            # continue to other tables

    try:
        log_pipeline(rows_total, per_table_counts)
    except Exception as e:
        print(f"Logging failed: {e}")


if __name__ == "__main__":
    run_pipeline()
