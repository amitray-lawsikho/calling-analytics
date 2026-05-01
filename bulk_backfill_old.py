"""
ONE-SHOT bulk backfill: copies every row currently in `crm_dashboard.*` into
the matching `crm_dashboard_old.*_old` table, setting
`archived_at = event_date + 55 days` per row.

Why this exists:
The daily cron (`restore_all_pipeline_old.py`) only archives rows that are
exactly 55 days old, so `_old` takes ~55 days to reach steady-state coverage.
This script "pre-fills" _old by writing each existing main row at the
archived_at it WOULD have received if the cron had been running for the past
55 days. Effect: dashboard reaches ~115-day retention ~45 days sooner.

When to run:
- After the 6 _old tables exist (DDL done) and ARE EMPTY (TRUNCATE first if
  they have smoke-test residue).
- BEFORE the first production run of restore_all_pipeline_old.py — or any
  time after; NOT EXISTS makes it idempotent and conflict-free.

Reuses the TABLES, PROJECT, etc. config from restore_all_pipeline_old.py to
keep the dedup keys and event columns in lock-step.
"""

import datetime
import pytz
import pandas as pd
from google.cloud import bigquery

from restore_all_pipeline_old import (
    PROJECT, SRC_DATASET, DST_DATASET, LOG_TABLE, TABLES, client, india,
)

# Same offset used by the daily cron — keep in sync if you ever change it
OFFSET_DAYS = 55


def backfill_table(cfg):
    src_fq    = f"`{PROJECT}.{SRC_DATASET}.{cfg['src']}`"
    dst_fq    = f"`{PROJECT}.{DST_DATASET}.{cfg['dst']}`"
    dst_ref   = f"{PROJECT}.{DST_DATASET}.{cfg['dst']}"
    event_col = cfg["event_col"]

    join_clause = " AND ".join(f"o.{k} = m.{k}" for k in cfg["key_cols"])

    # WHERE clause shared between the count and the load.
    # event_col >= today - 60 keeps us inside main's live window (rows older
    # than 60 days have already been auto-deleted, so they wouldn't match anyway —
    # but the explicit filter avoids scanning beyond what's reachable).
    where_clause = f"""
        WHERE {event_col} IS NOT NULL
          AND {event_col} >= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
          AND NOT EXISTS (
            SELECT 1 FROM {dst_fq} AS o
            WHERE {join_clause}
          )
    """

    # Pre-count for accurate logging (destination-write jobs report total
    # destination rows after write, not the delta).
    count_sql = f"SELECT COUNT(*) AS n FROM {src_fq} AS m {where_clause}"
    n_to_insert = list(
        client.query(
            count_sql,
            job_config=bigquery.QueryJobConfig(use_query_cache=False),
        ).result()
    )[0]["n"]

    if n_to_insert == 0:
        return 0

    # Staggered archived_at: each row lands in the partition it WOULD have
    # been archived into if the daily cron had been running for the past 55 days.
    insert_sql = f"""
        SELECT m.*, DATE_ADD({event_col}, INTERVAL {OFFSET_DAYS} DAY) AS archived_at
        FROM {src_fq} AS m
        {where_clause}
    """
    job_config = bigquery.QueryJobConfig(
        destination=dst_ref,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        use_query_cache=False,
    )
    client.query(insert_sql, job_config=job_config).result()
    return n_to_insert


def log_backfill(rows_total, per_table_counts):
    ist_now = datetime.datetime.now(india)
    df_log  = pd.DataFrame([{
        "run_date":      ist_now.date(),
        "pipeline_name": "BULK_BACKFILL_OLD",
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows_total),
    }])
    client.load_table_from_dataframe(
        df_log,
        LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
    ).result()
    note = ", ".join(f"{name}={n}" for name, n in per_table_counts.items())
    print(f"Logged BULK_BACKFILL_OLD: total={rows_total} ({note})")


def run():
    print("=" * 60)
    print("BULK BACKFILL — pre-fills _old with archived_at = event_date + 55")
    print("=" * 60)
    print(f"Tables in scope: {len(TABLES)}")

    per_table = {}
    total     = 0

    for cfg in TABLES:
        try:
            n = backfill_table(cfg)
            per_table[cfg["src"]] = n
            total += n
            print(f"  {cfg['src']}: +{n} rows")
        except Exception as e:
            print(f"  {cfg['src']}: ERROR — {e}")
            per_table[cfg["src"]] = -1

    try:
        log_backfill(total, per_table)
    except Exception as e:
        print(f"Logging failed: {e}")


if __name__ == "__main__":
    run()
