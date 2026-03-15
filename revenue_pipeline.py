import pandas as pd
import pytz
import os
import json
from datetime import datetime, timedelta
from google.cloud import bigquery
from google.oauth2 import service_account
from googleapiclient.discovery import build

# -----------------------------
# BIGQUERY CONFIG
# -----------------------------

PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "revenue_sheet"

table_id = f"{PROJECT_ID}.{DATASET}.{TABLE}"

# -----------------------------
# AUTHENTICATION (FROM GITHUB SECRET)
# -----------------------------

credentials = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["BQ_CREDENTIALS"])
)

client = bigquery.Client(
    credentials=credentials,
    project=PROJECT_ID
)

# -----------------------------
# FETCH GOOGLE SHEET
# -----------------------------


SPREADSHEET_ID = "1fFdwIWzS_JOcDLQ8ZP0BlNUm__OTW2vT99JB9hDukJA"
RANGE_NAME = "LookerRevenueSheet!A:Z"

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


# -----------------------------
# NORMALIZE COLUMN NAMES
# -----------------------------

df.columns = (
    df.columns
    .str.strip()
    .str.lower()
    .str.replace(" ", "_")
)

# -----------------------------
# SELECT REQUIRED COLUMNS
# -----------------------------

df = df[
[
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
"lawsikho/skill_arbitrage"
]
]

# -----------------------------
# RENAME COLUMNS
# -----------------------------

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
"LawSikho_Skill_Arbitrage"
]

# -----------------------------
# CLEAN DATE
# -----------------------------

df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)

# -----------------------------
# CLEAN PHONE
# -----------------------------

df["Contact_No"] = (
df["Contact_No"]
.astype(str)
.str.replace("+91","", regex=False)
.str.replace(" ","", regex=False)
.str[-10:]
)

# -----------------------------
# CLEAN FEE PAID
# -----------------------------

df["Fee_paid"] = (
df["Fee_paid"]
.astype(str)
.str.replace(",", "", regex=False)
)

df["Fee_paid"] = pd.to_numeric(df["Fee_paid"], errors="coerce")

before_rows = len(df)

df = df[df["Fee_paid"].notna()]

after_rows = len(df)

# -----------------------------
# CLEAN COURSE PRICE
# -----------------------------

df["Course_Price"] = (
df["Course_Price"]
.astype(str)
.str.replace(",", "", regex=False)
)

df["Course_Price"] = pd.to_numeric(df["Course_Price"], errors="coerce")

# -----------------------------
# CLEAN LAWSIKHO / SKILL ARBITRAGE
# -----------------------------

df["LawSikho_Skill_Arbitrage"] = (
df["LawSikho_Skill_Arbitrage"]
.astype(str)
.str.strip()
)

df["LawSikho_Skill_Arbitrage"] = df["LawSikho_Skill_Arbitrage"].replace("", "Not Identified")

# -----------------------------
# FILTER LAST 59 DAYS
# -----------------------------

india_tz = pytz.timezone("Asia/Kolkata")

today = datetime.now(india_tz).date()

cutoff_date = today - timedelta(days=59)

df = df[
(df["Date"].dt.date >= cutoff_date) &
(df["Date"].dt.date <= today)
]

# -----------------------------
# CREATE REV_MONTH
# -----------------------------

df["Rev_Month"] = df["Date"].values.astype("datetime64[M]")

# -----------------------------
# UPDATED_AT
# -----------------------------

now = datetime.now(india_tz)

df["updated_at"] = pd.Timestamp(now)

df["updated_at_ampm"] = now.strftime("%d/%m/%Y %I:%M %p")

df = df[df["Date"].notna()]

# -----------------------------
# LOAD TO BIGQUERY
# -----------------------------

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
bigquery.SchemaField("Rev_Month","DATE"),
bigquery.SchemaField("updated_at","TIMESTAMP"),
bigquery.SchemaField("updated_at_ampm","STRING"),
]
)

job = client.load_table_from_dataframe(
df,
table_id,
job_config=job_config
)

job.result()

