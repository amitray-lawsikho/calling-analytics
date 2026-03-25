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

    # 1. CLEAN HEADERS
    df.columns = df.columns.str.replace(r'\s+', ' ', regex=True).str.strip()

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
    
    # Filter columns and rename
    df = df[list(mapping.keys())].rename(columns=mapping)

    # 2. DATA TRANSFORMATION & FILTERING
    # Format names to Proper Case
    df['lead_name'] = df['lead_name'].astype(str).str.title().str.strip()
    df['call_owner'] = df['call_owner'].astype(str).str.title().str.strip()

    # CRITICAL: Handle Date with explicit Indian Format (DD/MM/YYYY)
    df['Call_Date'] = pd.to_datetime(df['Call_Date'], format='%d/%m/%Y', errors='coerce').dt.date
    df = df.dropna(subset=['Call_Date'])

    # Calculate the 59-day window
    today = datetime.now(IST).date()
    fifty_nine_days_ago = today - timedelta(days=59)

    # FILTER: Only keep rows within the [today - 59 days] to [today] range
    df = df[(df['Call_Date'] >= fifty_nine_days_ago) & (df['Call_Date'] <= today)]

    # Convert Duration (Minutes to Seconds)
    df['call_duration'] = (pd.to_numeric(df['call_duration'], errors='coerce').fillna(0) * 60).astype(int)
    
    # Clean Phone Numbers
    df['client_number'] = df['client_number'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    
    # Add timestamp for the update
    df['updated_at'] = datetime.now(IST)

    # 3. PUSH TO BIGQUERY
    if df.empty:
        print("No data found within the last 59 days. Skipping upload.")
        return

    print(f"Loading {len(df)} filtered rows to BigQuery (WRITE_TRUNCATE)...")
    
    # Credentials setup
    try:
        info = json.loads(os.environ.get("BQ_CREDENTIALS"))
        credentials = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(credentials=credentials, project=PROJECT_ID)

        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        target_table = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
        
        job = client.load_table_from_dataframe(df, target_table, job_config=job_config)
        job.result()
        
        print(f"Pipeline executed successfully. Table {TABLE_ID} updated.")
    except Exception as e:
        print(f"Error uploading to BigQuery: {e}")

if __name__ == "__main__":
    run_pipeline()
