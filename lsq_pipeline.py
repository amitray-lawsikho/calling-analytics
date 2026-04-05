import requests
import pandas as pd
import time
import os
from datetime import datetime, timedelta
import pytz
from google.cloud import bigquery

client = bigquery.Client()

# CONFIG
ACCESS_KEY = os.environ["LSQ_ACCESS_KEY"]
SECRET_KEY = os.environ["LSQ_SECRET_KEY"]

PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "lsq_leads"
LOG_TABLE = f"{PROJECT_ID}.crm_scheduled_results.MASTER_QUERY_REFRESH"
table_id = f"{PROJECT_ID}.{DATASET}.{TABLE}"

# TIMEZONE
india_tz = pytz.timezone("Asia/Kolkata")
now = datetime.now(india_tz)
date_60_days = (now - timedelta(days=59)).strftime("%Y-%m-%d %H:%M:%S")

# LOG FUNCTION
def log_pipeline(pipeline_name, rows):
    ist_now = datetime.now(india_tz)

    df_log = pd.DataFrame([{
        "run_date": ist_now.date(),
        "pipeline_name": pipeline_name,
        "run_timestamp": ist_now.strftime("%d/%m/%Y %I:%M %p"),
        "rows_inserted": int(rows)
    }])

    job = client.load_table_from_dataframe(
        df_log,
        LOG_TABLE,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )
    job.result()

# =========================
# FETCH USERS (OWNER MAP)
# =========================
user_url = "https://api-in21.leadsquared.com/v2/UserManagement.svc/Users.Get"

params = {
    "accessKey": ACCESS_KEY,
    "secretKey": SECRET_KEY
}

response = requests.get(user_url, params=params)
users = response.json()

owner_map = {}
if isinstance(users, list):
    for user in users:
        owner_id = user.get("ID")
        owner = f"{user.get('FirstName','')} {user.get('LastName','')}".strip()
        owner_map[owner_id] = owner
else:
    raise Exception("Users API response not valid")

# =========================
# FETCH REVENUE DATA 🔥
# =========================
revenue_query = f"""
SELECT 
  Contact_No,
  Email_Id,
  Caller_name
FROM `{PROJECT_ID}.crm_dashboard.revenue_sheet`
WHERE Enrollment = 'New enrollment'
AND Fee_paid >= 999
"""

rev_df = client.query(revenue_query).to_dataframe()

# CLEAN REVENUE DATA
rev_df["Contact_No"] = rev_df["Contact_No"].astype(str).str.replace("+91","", regex=False).str[-10:]
rev_df["Email_Id"] = rev_df["Email_Id"].astype(str).str.lower().str.strip()

# LOOKUP DICTS
phone_map = dict(zip(rev_df["Contact_No"], rev_df["Caller_name"]))
email_map = dict(zip(rev_df["Email_Id"], rev_df["Caller_name"]))

# =========================
# LSQ FETCH
# =========================
FIELDS = (
"ProspectID,FirstName,LastName,Phone,Mobile,EmailAddress,mx_Bootcamp_attended,"
"mx_Funnel_name,mx_Bootcamp_Attendance,"
"OwnerId,ProspectStage,"
"mx_Follow_up_date_and_time,mx_Course_Fees,"
"mx_Campaign_Name,mx_Enquired_Course,mx_Phone_call_counter,"
"mx_Last_Call_new,mx_Report_Type,ModifiedByName,"
"mx_Assigned_By,mx_Assigned_On,mx_Assigned_On_Call_Counter"
)

URL = f"https://api-in21.leadsquared.com/v2/LeadManagement.svc/Leads.Get?accessKey={ACCESS_KEY}&secretKey={SECRET_KEY}"
HEADERS = {"Content-Type": "application/json"}

page = 1
page_size = 1000
all_data = []
retry_count = 0
MAX_RETRIES = 10

while True:
    payload = {
        "Parameter": {
            "LookupName": "mx_Assigned_On",
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
            break
        time.sleep(retry_count * 5)
        continue

    retry_count = 0

    if not data:
        break

    all_data.extend(data)
    page += 1
    time.sleep(1)

# =========================
# DATAFRAME
# =========================
df = pd.DataFrame(all_data)

if df.empty:
    log_pipeline("LSQ", 0)
    exit()

# ENSURE FIELDS
for col in FIELDS.split(","):
    if col not in df.columns:
        df[col] = None

# OWNER MAP
df["OwnerId"] = df["OwnerId"].map(owner_map).fillna(df["OwnerId"])

# CLEAN DATA
df["Phone"] = df["Phone"].astype(str).str.replace("+91","", regex=False).str[-10:]
df["Mobile"] = df["Mobile"].astype(str).str.replace("+91","", regex=False).str[-10:]
df["EmailAddress"] = df["EmailAddress"].astype(str).str.lower().str.strip()

# DATE CONVERSION
df["Follow_up_date"] = pd.to_datetime(df["mx_Follow_up_date_and_time"], errors="coerce").dt.date
df["LastCalledDate"] = pd.to_datetime(df["mx_Last_Call_new"], errors="coerce").dt.date
df["AssignedOn"] = pd.to_datetime(df["mx_Assigned_On"], errors="coerce").dt.date

# =========================
# FINAL STRUCTURE
# =========================
df_final = pd.DataFrame({
    "ProspectID": df["ProspectID"],
    "FirstName": df["FirstName"],
    "LastName": df["LastName"],
    "PhoneNumber": df["Phone"],
    "Alternate_PhoneNumber": df["Mobile"],
    "Email": df["EmailAddress"],
    "Funnel_name": df["mx_Funnel_name"],
    "Bootcamp_Attendance": pd.to_numeric(df["mx_Bootcamp_Attendance"], errors="coerce"),
    "Bootcamp_attended": df["mx_Bootcamp_attended"],
    "Owner": df["OwnerId"],
    "ContactStage": df["ProspectStage"].astype(str),
    "Follow_up_date": df["Follow_up_date"],
    "Course_Fees": df["mx_Course_Fees"],
    "Campaign_Name": df["mx_Campaign_Name"],
    "Enquired_Course": df["mx_Enquired_Course"],
    "Phone_call_counter": df["mx_Phone_call_counter"],
    "LastCalledDate": df["LastCalledDate"],
    "Report_Type": df["mx_Report_Type"],
    "ModifiedBy": df["ModifiedByName"],
    "AssignedBy": df["mx_Assigned_By"],
    "AssignedOn": df["AssignedOn"],
    "Assigned_On_Call_Counter": pd.to_numeric(df["mx_Assigned_On_Call_Counter"], errors="coerce"),
    "updated_at_ampm": now.strftime("%d/%m/%Y %I:%M %p")
})

# =========================
# 🔥 OVERRIDE LOGIC
# =========================
def override_owner_stage(row):

    phone = str(row["PhoneNumber"])
    if phone and phone != "nan" and phone in phone_map:
        row["Owner"] = phone_map[phone]
        row["ContactStage"] = "Actually Enrolled"
        return row

    alt_phone = str(row["Alternate_PhoneNumber"])
    if alt_phone and alt_phone != "nan" and alt_phone in phone_map:
        row["Owner"] = phone_map[alt_phone]
        row["ContactStage"] = "Actually Enrolled"
        return row

    email = str(row["Email"]).lower().strip()
    if email and email != "nan" and email in email_map:
        row["Owner"] = email_map[email]
        row["ContactStage"] = "Actually Enrolled"
        return row

    return row

df_final = df_final.apply(override_owner_stage, axis=1)

# REMOVE DUPLICATES
df_final.drop_duplicates(subset=["ProspectID"], inplace=True)

# =========================
# LOAD TO BIGQUERY
# =========================
job_config = bigquery.LoadJobConfig(
    write_disposition="WRITE_TRUNCATE"
)

job = client.load_table_from_dataframe(df_final, table_id, job_config=job_config)
job.result()

# LOG
log_pipeline("LSQ", len(df_final))

print("✅ Pipeline Completed")
print("Total Rows:", len(df_final))
print("Overrides Applied:", (df_final["ContactStage"] == "Actually Enrolled").sum())
