import pandas as pd
import requests
import io
from datetime import datetime, timedelta
import pytz
from google.cloud import bigquery
from google.oauth2 import service_account
import os
import json

# Configuration
CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTN5y1tWu18GfvHx3l5wUEgW_b1CpsweZcoAOzK6Erqe2ATvTNhdFFvkc0b32QNGO3pk1d3ACiAoSS6/pub?gid=1479782046&single=true&output=csv"
PROJECT_ID = "studious-apex-488820-c3"
DATASET_ID = "crm_dashboard"
TABLE_ID = "manual_calls"
IST = pytz.timezone('Asia/Kolkata')

def run_pipeline():
    print("Fetching data from Google Sheets...")
    response = requests.get(CSV_URL)
    df = pd.read_csv(io.StringIO(response.text))

    # CLEAN HEADERS: This fixes the KeyError by removing hidden spaces
    df.columns = df.columns.str.strip()

    # Updated Mapping (matching the exact strings in the Sheet)
    mapping = {
        'Email address': 'Email',
        'Date': 'Call_Date',
        'Slot Timing': 'Slot',
        'Counselor Name (Refer your LSQ name)': 'call_owner',
        'Duration of the call (for a single call only)': 'call_duration',
        'Phone Number of Submitted Manual (Numbers will be audited kindly provide accurate details)': 'client_number',
        'Google Drive Link of added calls and screenshots (Please give access of the drive)*': 'Drive_Link',
        'Approved By': 'Approved_By',
        'Name of Submitted Manual lead': 'lead_name'
    }
    
    # Filter only the columns we need
    try:
        df = df[list(mapping.keys())].rename(columns=mapping)
    except KeyError as e:
        print(f"Error: Could not find columns. Available columns are: {list(df.columns)}")
        raise e

    # Data Transformation
    df['Call_Date'] = pd.to_datetime(df['Call_Date'], dayfirst=True).dt.date
    fifty_nine_days_ago = (datetime.now(IST) - timedelta(days=59)).date()
    df = df[df['Call_Date'] >= fifty_nine_days_ago]

    # Convert Duration (Minutes to Seconds)
    df['call_duration'] = (pd.to_numeric(df['call_duration'], errors='coerce').fillna(0) * 60).astype(int)
    
    # Clean Phone Numbers (remove .0 and ensure string)
    df['client_number'] = df['client_number'].astype(str).str.replace(r'\.0$', '', regex=True)
    
    df['updated_at'] = datetime.now(IST)

    # Load to BigQuery
    print(f"Loading {len(df)} rows to BigQuery...")
    info = json.loads(os.environ.get("BQ_CREDENTIALS"))
    credentials = service_account.Credentials.from_service_account_info(info)
    client = bigquery.Client(credentials=credentials, project=PROJECT_ID)

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")

    target_table = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
    job = client.load_table_from_dataframe(df, target_table, job_config=job_config)
    job.result()
    
    print("Pipeline executed successfully.")

if __name__ == "__main__":
    run_pipeline()
