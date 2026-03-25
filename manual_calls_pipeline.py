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

    # 1. CLEAN HEADERS (Handles hidden newlines/spaces)
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
    
    # Select columns that exist, ignore missing ones to prevent crash
    existing_cols = [col for col in mapping.keys() if col in df.columns]
    df = df[existing_cols].rename(columns=mapping)

    # 2. DATA TRANSFORMATION (FORGIVING LOGIC)
    # Proper Case for names, but handle non-string values safely
    for col in ['lead_name', 'call_owner']:
        if col in df.columns:
            df[col] = df[col].astype(str).str.title().str.replace('Nan', 'N/A').str.strip()

    # DATE HANDLING: Convert and keep even if imperfect
    # We use dayfirst=True to handle the DD/MM/YYYY format of your sheet
    df['Call_Date_Converted'] = pd.to_datetime(df['Call_Date'], dayfirst=True, errors='coerce')
    
    # Calculate the 59-day window
    today = datetime.now(IST).date()
    fifty_nine_days_ago = today - timedelta(days=59)

    # FILTER: Filter based on the converted date, but don't drop NaT until the very end
    # This ensures rows with weird characters in the date column aren't lost if they can be parsed
    mask = (df['Call_Date_Converted'].dt.date >= fifty_nine_days_ago) & (df['Call_Date_Converted'].dt.date <= today)
    df = df[mask].copy()
    
    # Convert the column back to the format BigQuery expects (YYYY-MM-DD)
    df['Call_Date'] = df['Call_Date_Converted'].dt.date
    df = df.drop(columns=['Call_Date_Converted'])

    # Convert Duration (Minutes to Seconds) - fill errors with 0 instead of dropping row
    df['call_duration'] = (pd.to_numeric(df['call_duration'], errors='coerce').fillna(0) * 60).astype(int)
    
    # Clean Phone Numbers
    df['client_number'] = df['client_number'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    
    # Set the update timestamp
    df['updated_at'] = datetime.now(IST)

    # 3. PUSH TO BIGQUERY
    if df.empty:
        print("No valid data found within the date range.")
        return

    print(f"Loading {len(df)} rows to BigQuery (WRITE_TRUNCATE)...")
    
    try:
        # Load credentials from Environment Variable
        info = json.loads(os.environ.get("BQ_CREDENTIALS"))
        credentials = service_account.Credentials.from_service_account_info(info)
        client = bigquery.Client(credentials=credentials, project=PROJECT_ID)

        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        target_table = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
        
        job = client.load_table_from_dataframe(df, target_table, job_config=job_config)
        job.result()
        
        print("Pipeline executed successfully. All valid rows uploaded.")
    except Exception as e:
        print(f"Error during BigQuery upload: {e}")

if __name__ == "__main__":
    run_pipeline()
