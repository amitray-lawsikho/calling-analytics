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

    # Mapping including Timestamp for fallback
    mapping = {
        'Timestamp': 'Timestamp',
        'Email address': 'Email',
        'Date': 'Manual_Date',
        'Slot Timing': 'Slot',
        'Counselor Name (Refer your LSQ name)': 'call_owner',
        'Duration of the call (for a single call only)': 'call_duration',
        'Phone Number of Submitted Manual (Numbers will be audited kindly provide accurate details)': 'client_number',
        'Google Drive Link of added calls and screenshots (Please give access of the drive)*': 'Drive_Link',
        'Approved By': 'Approved_By',
        'Name of Submitted Manual lead': 'lead_name'
    }
    
    df = df[list(mapping.keys())].rename(columns=mapping)

    # 2. DATA TRANSFORMATION
    # Proper Case for names
    for col in ['lead_name', 'call_owner']:
        df[col] = df[col].astype(str).str.title().replace(['Nan', 'None', ''], 'N/A').str.strip()

    # --- NEW DATE LOGIC: Fallback to Timestamp ---
    # Parse Manual_Date and Timestamp
    df['Manual_Date_Dt'] = pd.to_datetime(df['Manual_Date'], dayfirst=True, errors='coerce')
    df['Timestamp_Dt'] = pd.to_datetime(df['Timestamp'], dayfirst=True, errors='coerce')

    # Use Manual_Date if available, otherwise use Timestamp date
    df['Call_Date_Final'] = df['Manual_Date_Dt'].fillna(df['Timestamp_Dt'])
    
    # Calculate 59-day window
    today = datetime.now(IST).date()
    fifty_nine_days_ago = today - timedelta(days=59)

    # Filter by the resolved date
    mask = (df['Call_Date_Final'].dt.date >= fifty_nine_days_ago) & (df['Call_Date_Final'].dt.date <= today)
    df_filtered = df[mask].copy()

    # Assign final date for BigQuery
    df_filtered['Call_Date'] = df_filtered['Call_Date_Final'].dt.date
    
    # Cleanup extra columns before upload
    cols_to_drop = ['Timestamp', 'Manual_Date', 'Manual_Date_Dt', 'Timestamp_Dt', 'Call_Date_Final']
    df_filtered = df_filtered.drop(columns=[c for c in cols_to_drop if c in df_filtered.columns])

    # Minutes to Seconds
    df_filtered['call_duration'] = (pd.to_numeric(df_filtered['call_duration'], errors='coerce').fillna(0) * 60).astype(int)
    
    # Clean Phone Numbers
    df_filtered['client_number'] = df_filtered['client_number'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    
    df_filtered['updated_at'] = datetime.now(IST)

    # 3. PUSH TO BIGQUERY
    if df_filtered.empty:
        print("No rows found matching the date criteria.")
        return

    print(f"Loading {len(df_filtered)} rows to BigQuery...")
    
    try:
        info = json.loads(os.environ.get("BQ_CREDENTIALS"))
        credentials = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(credentials=credentials, project=PROJECT_ID)

        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        target_table = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
        
        job = client.load_table_from_dataframe(df_filtered, target_table, job_config=job_config)
        job.result()
        
        print(f"Successfully uploaded {len(df_filtered)} rows. Verification: {len(df_filtered[df_filtered['Call_Date'] == datetime(2026,3,24).date()])} rows for 24/03.")
    except Exception as e:
        print(f"BigQuery Error: {e}")

if __name__ == "__main__":
    run_pipeline()
