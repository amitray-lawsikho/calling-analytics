import requests
import pandas as pd
import time
import os
import json
from datetime import datetime, timedelta
import pytz
from google.cloud import bigquery
from google.oauth2 import service_account

# --------------------------------
# CONFIG
# --------------------------------

ACCESS_KEY = os.environ["LSQ_ACCESS_KEY"]
SECRET_KEY = os.environ["LSQ_SECRET_KEY"]

PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "lsq_leads"

table_id = f"{PROJECT_ID}.{DATASET}.{TABLE}"

# --------------------------------
# AUTHENTICATION
# --------------------------------

credentials = service_account.Credentials.from_service_account_info(
    json.loads(os.environ["BQ_CREDENTIALS"])
)

client = bigquery.Client(
    credentials=credentials,
    project=PROJECT_ID
)

# --------------------------------
# TIME WINDOW (LAST 59 DAYS)
# --------------------------------

india_tz = pytz.timezone("Asia/Kolkata")

now = datetime.now(india_tz)

date_60_days = (now - timedelta(days=59)).strftime("%Y-%m-%d %H:%M:%S")

# --------------------------------
# FETCH USERS
# --------------------------------

print("Fetching LSQ Users...")

user_url = "https://api-in21.leadsquared.com/v2/UserManagement.svc/Users.Get"

params = {
    "accessKey": ACCESS_KEY,
    "secretKey": SECRET_KEY
}

response = requests.get(user_url, params=params)

users = response.json()

owner_map = {}

for user in users:

    owner_id = user.get("ID")

    owner = f"{user.get('FirstName','')} {user.get('LastName','')}".strip()

    owner_map[owner_id] = owner

print("Users Loaded:", len(owner_map))

# --------------------------------
# FETCH LEADS
# --------------------------------

URL = f"https://api-in21.leadsquared.com/v2/LeadManagement.svc/Leads.Get?accessKey={ACCESS_KEY}&secretKey={SECRET_KEY}"

HEADERS = {"Content-Type": "application/json"}

FIELDS = (
"ProspectID,FirstName,LastName,Phone,EmailAddress,"
"mx_Funnel_name,mx_Bootcamp_Date,mx_Bootcamp_collections,mx_Bootcamp_Attendance,"
"OwnerId,ProspectStage,ModifiedOn,"
"mx_Follow_up_date_and_time,mx_Followup_comments,mx_Course_Fees,"
"mx_Campaign_Name,mx_Enquired_Course,mx_Phone_call_counter,"
"mx_Last_Call_new,mx_Report_Type,ModifiedByName"
)

page = 1
page_size = 1000

all_data = []

retry_count = 0
MAX_RETRIES = 10

print("Fetching Leads...")

while True:

    payload = {
        "Parameter": {
            "LookupName": "ModifiedOn",
            "LookupValue": date_60_days,
            "SqlOperator": ">"
        },
        "Columns": {
            "Include_CSV": FIELDS
        },
        "Paging": {
            "PageIndex": page,
            "PageSize": page_size
        }
    }

    response = requests.post(URL, headers=HEADERS, json=payload)

    data = response.json()

    if isinstance(data, dict):

        retry_count += 1

        if retry_count > MAX_RETRIES:
            print("Too many retries")
            break

        wait_time = retry_count * 5

        print("Retrying...")

        time.sleep(wait_time)

        continue

    retry_count = 0

    if not data:
        print("All pages fetched")
        break

    all_data.extend(data)

    print("Page", page, "Rows:", len(data))

    page += 1

    time.sleep(1)

# --------------------------------
# DATAFRAME
# --------------------------------

df = pd.DataFrame(all_data)

all_fields = FIELDS.split(",")

for col in all_fields:
    if col not in df.columns:
        df[col] = None

df = df[all_fields]

# --------------------------------
# OWNER NAME MAPPING
# --------------------------------

df["OwnerId"] = df["OwnerId"].map(owner_map).fillna(df["OwnerId"])

# --------------------------------
# CLEAN DATA
# --------------------------------

df["ModifiedOn"] = pd.to_datetime(df["ModifiedOn"], errors="coerce")

df["Phone"] = (
    df["Phone"]
    .astype(str)
    .str.replace("+91","", regex=False)
    .str.replace(" ","", regex=False)
    .str[-10:]
)

df.drop_duplicates(subset=["ProspectID"], inplace=True)

# --------------------------------
# ADD DATE FIELD
# --------------------------------

df["Modified_Date"] = df["ModifiedOn"].dt.date

# --------------------------------
# UPDATED TIMESTAMP
# --------------------------------

df["updated_at"] = pd.Timestamp.utcnow()

df["updated_at_ampm"] = now.strftime("%d/%m/%Y %I:%M %p")

print("Total Rows:", len(df))

# --------------------------------
# LOAD TO BIGQUERY
# --------------------------------

job_config = bigquery.LoadJobConfig(

    write_disposition="WRITE_TRUNCATE",

    schema=[
        bigquery.SchemaField("ProspectID","STRING"),
        bigquery.SchemaField("FirstName","STRING"),
        bigquery.SchemaField("LastName","STRING"),
        bigquery.SchemaField("Phone","STRING"),
        bigquery.SchemaField("EmailAddress","STRING"),
        bigquery.SchemaField("mx_Funnel_name","STRING"),
        bigquery.SchemaField("mx_Bootcamp_Date","STRING"),
        bigquery.SchemaField("mx_Bootcamp_collections","STRING"),
        bigquery.SchemaField("mx_Bootcamp_Attendance","STRING"),
        bigquery.SchemaField("OwnerId","STRING"),
        bigquery.SchemaField("ProspectStage","STRING"),
        bigquery.SchemaField("ModifiedOn","TIMESTAMP"),
        bigquery.SchemaField("mx_Follow_up_date_and_time","STRING"),
        bigquery.SchemaField("mx_Followup_comments","STRING"),
        bigquery.SchemaField("mx_Course_Fees","STRING"),
        bigquery.SchemaField("mx_Campaign_Name","STRING"),
        bigquery.SchemaField("mx_Enquired_Course","STRING"),
        bigquery.SchemaField("mx_Phone_call_counter","STRING"),
        bigquery.SchemaField("mx_Last_Call_new","STRING"),
        bigquery.SchemaField("mx_Report_Type","STRING"),
        bigquery.SchemaField("ModifiedByName","STRING"),
        bigquery.SchemaField("Modified_Date","DATE"),
        bigquery.SchemaField("updated_at","TIMESTAMP"),
        bigquery.SchemaField("updated_at_ampm","STRING"),
    ],

    time_partitioning=bigquery.TimePartitioning(
        field="Modified_Date"
    ),

    clustering_fields=["OwnerId","ProspectStage"]

)

print("Uploading to BigQuery...")

job = client.load_table_from_dataframe(
    df,
    table_id,
    job_config=job_config
)

job.result()

print("Rows inserted:", len(df))
print("Pipeline finished successfully")
