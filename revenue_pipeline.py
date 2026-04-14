import pandas as pd
import pytz
import os
from datetime import datetime, timedelta
from google.cloud import bigquery
from googleapiclient.discovery import build
client = bigquery.Client()
from google.auth import default
# BIGQUERY CONFIG
PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "revenue_sheet"

table_id = f"{PROJECT_ID}.{DATASET}.{TABLE}"
LOG_TABLE = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"

# LOG FUNCTION
def log_pipeline(pipeline_name, rows):

    ist_now = datetime.now(india_tz)

    log_data = [{
        "run_date": ist_now.date(),
        "pipeline_name": pipeline_name,
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows)
    }]

    df = pd.DataFrame(log_data)

    job = client.load_table_from_dataframe(
        df,
        LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()

# FETCH GOOGLE SHEET
SPREADSHEET_ID = "1fFdwIWzS_JOcDLQ8ZP0BlNUm__OTW2vT99JB9hDukJA"
RANGE_NAME = "LookerRevenueSheet!A:Z"
credentials, _ = default()
service = build("sheets", "v4", credentials=credentials)

sheet = service.spreadsheets()

result = sheet.values().get(
    spreadsheetId=SPREADSHEET_ID,
    range=RANGE_NAME
).execute()

values = result.get("values", [])

df = pd.DataFrame(values)

df.columns = df.iloc[0]

df = df[1:]

# NORMALIZE COLUMN NAMES
df.columns = (
    df.columns
    .str.strip()
    .str.lower()
    .str.replace(" ", "_")
)

# ── FETCH COMMUNITY HEAD MAPPING ──────────────────────────────────────────────
COMMUNITY_HEAD_CSV = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRT73ztvPNZSvIu5WLxo-3WQ76JMAnt4P9dITd4EAbjSvuDytfgvdfri1WPXotCjm_"
    "Etnb80_Q7S-wf/pub?gid=289940755&single=true&output=csv"
)

df_community = pd.read_csv(COMMUNITY_HEAD_CSV, usecols=[0, 1], header=0)
df_community.columns = ["Course_Name", "Community_Head"]
df_community["Course_Name"] = df_community["Course_Name"].astype(str).str.strip()
df_community["Community_Head"] = df_community["Community_Head"].astype(str).str.strip()

community_head_map = dict(
    zip(df_community["Course_Name"], df_community["Community_Head"])
)
# ─────────────────────────────────────────────────────────────────────────────

# SELECT REQUIRED COLUMNS
cols_to_select = [
    "date",
    "name",
    "contact_no",
    "email_id",
    "course",
    "fee_paid",
    "caller_name_________(for_ops)_for_enrollment_link_circulation",
    "enrollment",
    "source",
    "course_price",
    "lawsikho/skill_arbitrage",
    "full/installment_(welcome_callers_mis)",
]
df = df[cols_to_select]

df.columns = [
    "Date",
    "Name",
    "Contact_No",
    "Email_Id",
    "Course",
    "Fee_paid",
    "Caller_name",
    "Enrollment",
    "Source",
    "Course_Price",
    "LawSikho_Skill_Arbitrage",
    "Full_Installment",
]

# CLEAN DATE
df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)

# CLEAN PHONE
df["Contact_No"] = (
df["Contact_No"]
.astype(str)
.str.replace("+91","", regex=False)
.str.replace(" ","", regex=False)
.str[-10:]
)

# CLEAN FEE PAID
df["Fee_paid"] = (
df["Fee_paid"]
.astype(str)
.str.replace(",", "", regex=False)
)

df["Fee_paid"] = pd.to_numeric(df["Fee_paid"], errors="coerce")

before_rows = len(df)

df = df[df["Fee_paid"].notna()]

after_rows = len(df)

# CLEAN COURSE PRICE
df["Course_Price"] = (
df["Course_Price"]
.astype(str)
.str.replace(",", "", regex=False)
)

df["Course_Price"] = pd.to_numeric(df["Course_Price"], errors="coerce")

# CLEAN LAWSIKHO / SKILL ARBITRAGE
df["LawSikho_Skill_Arbitrage"] = (
df["LawSikho_Skill_Arbitrage"]
.astype(str)
.str.strip()
)

df["LawSikho_Skill_Arbitrage"] = df["LawSikho_Skill_Arbitrage"].replace("", "Not Identified")

# FILTER LAST 59 DAYS
india_tz = pytz.timezone("Asia/Kolkata")

today = datetime.now(india_tz).date()

cutoff_date = today - timedelta(days=59)

df = df[
(df["Date"].dt.date >= cutoff_date) &
(df["Date"].dt.date <= today)
]

# CREATE REV_MONTH
df["Rev_Month"] = df["Date"].values.astype("datetime64[M]")

# ── MAP COMMUNITY HEAD ────────────────────────────────────────────────────────
df["community_head"] = (
    df["Course"]
    .astype(str)
    .str.strip()
    .map(community_head_map)
    .fillna("")
)
# ─────────────────────────────────────────────────────────────────────────────

# UPDATED_AT
now = datetime.now(india_tz)

df["updated_at"] = pd.Timestamp(now)

df["updated_at_ampm"] = now.strftime("%d/%m/%Y %I:%M %p")

df = df[df["Date"].notna()]
if df.empty:
    log_pipeline("REVENUE", 0)
    exit()
# LOAD TO BIGQUERY
job_config = bigquery.LoadJobConfig(

write_disposition="WRITE_TRUNCATE",

schema=[
bigquery.SchemaField("Date","DATE"),
bigquery.SchemaField("Name","STRING"),
bigquery.SchemaField("Contact_No","STRING"),
bigquery.SchemaField("Email_Id","STRING"),
bigquery.SchemaField("Course","STRING"),
bigquery.SchemaField("Fee_paid","FLOAT"),
bigquery.SchemaField("Caller_name","STRING"),
bigquery.SchemaField("Enrollment","STRING"),
bigquery.SchemaField("Source","STRING"),
bigquery.SchemaField("Course_Price","FLOAT"),
bigquery.SchemaField("LawSikho_Skill_Arbitrage","STRING"),
bigquery.SchemaField("Full_Installment","STRING"),
bigquery.SchemaField("Rev_Month","DATE"),
bigquery.SchemaField("updated_at","TIMESTAMP"),
bigquery.SchemaField("updated_at_ampm","STRING"),
bigquery.SchemaField("community_head","STRING"),   # ← added
],
time_partitioning=bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="Date",
        expiration_ms=60 * 24 * 60 * 60 * 1000  # 60 days in ms
    ),
    clustering_fields=["Name", "Contact_No"],
)

job = client.load_table_from_dataframe(
df,
table_id,
job_config=job_config
)

job.result()
rows_count = len(df) 
log_pipeline("REVENUE", rows_count)
