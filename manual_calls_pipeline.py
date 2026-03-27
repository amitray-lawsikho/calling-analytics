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
        'Timestamp': 'Timestamp',
        'Email address': 'Email',
        'Date': 'Manual_Date',
        'Slot Timing': 'Slot',
        'Counselor Name (Refer your LSQ name)': 'call_owner',
        'Duration of the call in minutes(for a single call only)': 'call_duration', # 👈 THIS IS THE FIXED LINE
        'Phone Number of Submitted Manual (Numbers will be audited kindly provide accurate details)': 'client_number',
        'Google Drive Link of added calls and screenshots (Please give access of the drive)*': 'Drive_Link',
        'Approved By': 'Approved_By',
        'Name of Submitted Manual lead': 'lead_name'
    }
    
    df = df[list(mapping.keys())].rename(columns=mapping)

    # 2. DATA TRANSFORMATION
    for col in ['lead_name', 'call_owner']:
        df[col] = df[col].astype(str).str.title().replace(['Nan', 'None', ''], 'N/A').str.strip()

    # --- TIMEZONE-SAFE DATE LOGIC ---
    # 1. Parse Timestamp as IST directly
    df['Timestamp_Dt'] = pd.to_datetime(df['Timestamp'], dayfirst=True, errors='coerce')
    # 2. Parse Manual_Date
    df['Manual_Date_Dt'] = pd.to_datetime(df['Manual_Date'], dayfirst=True, errors='coerce')

    # 3. Resolve Final Date: Use Manual Date if valid, else Timestamp
    # We ensure we take ONLY the .date() part to avoid UTC shifting in BigQuery
    df['Call_Date'] = df['Manual_Date_Dt'].fillna(df['Timestamp_Dt']).dt.date

    # 4. Filter last 59 days
    today = datetime.now(IST).date()
    fifty_nine_days_ago = today - timedelta(days=59)
    df_filtered = df[(df['Call_Date'] >= fifty_nine_days_ago) & (df['Call_Date'] <= today)].copy()

    # Minutes to Seconds
    df_filtered['call_duration'] = (pd.to_numeric(df_filtered['call_duration'], errors='coerce').fillna(0) * 60).astype(int)
    
    # Clean Phone Numbers
    df_filtered['client_number'] = df_filtered['client_number'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    
    # updated_at should be IST
    df_filtered['updated_at'] = datetime.now(IST)

    # Cleanup extra technical columns
    final_cols = ['Email', 'Call_Date', 'Slot', 'call_owner', 'call_duration', 'client_number', 'Drive_Link', 'Approved_By', 'lead_name', 'updated_at']
    df_filtered = df_filtered[final_cols]

    # 3. PUSH TO BIGQUERY
    if df_filtered.empty:
        print("No rows found.")
        return

    print(f"Loading {len(df_filtered)} rows to BigQuery...")
    
    try:
        info = json.loads(os.environ.get("BQ_CREDENTIALS"))
        credentials = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(credentials=credentials, project=PROJECT_ID)

        # WRITE_TRUNCATE ensures we don't have duplicates
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        target_table = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
        
        job = client.load_table_from_dataframe(df_filtered, target_table, job_config=job_config)
        job.result()
        
        # Log counts specifically for 24/03 to verify
        count_24 = len(df_filtered[df_filtered['Call_Date'] == datetime(2026, 3, 24).date()])
        print(f"Successfully uploaded {len(df_filtered)} total rows. (Count for 24/03: {count_24})")
        
    except Exception as e:
        print(f"BigQuery Error: {e}")

if __name__ == "__main__":
    run_pipeline()
