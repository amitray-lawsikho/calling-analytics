import requests, os, pytz
from datetime import datetime, timedelta
from google.cloud import bigquery

india = pytz.timezone("Asia/Kolkata")
key = os.getenv("ACEFONE_API_KEY")
acc = os.getenv("ACEFONE_ACCOUNT_ID")

print("=== 1. SECRETS CHECK ===")
print(f"API key present: {bool(key)}, length: {len(key or '')}")
print(f"Account ID present: {bool(acc)}, length: {len(acc or '')}")

print("\n=== 2. ACEFONE API CHECK ===")
now = datetime.now(india)
params = {
    "from_date": (now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
    "to_date": now.strftime("%Y-%m-%d %H:%M:%S"),
    "page": 1, "limit": 5
}
res = requests.get("https://api.acefone.in/v1/call/records",
                   headers={"Authorization": f"Bearer {key}", "accountid": acc},
                   params=params)
print(f"Status code: {res.status_code}")
print(f"Body (first 1000 chars): {res.text[:1000]}")

if res.status_code == 200:
    payload = res.json()
    print(f"Top-level keys: {list(payload.keys())}")
    data = payload.get("results", [])
    print(f"Records in 'results': {len(data)}")
    if data:
        print(f"First record keys: {list(data[0].keys())}")
        print(f"end_stamp sample: {data[0].get('end_stamp')}")

print("\n=== 3. BIGQUERY WATERMARK CHECK ===")
client = bigquery.Client()
df = client.query("""
    SELECT MAX(call_datetime) AS last_time
    FROM `studious-apex-488820-c3.crm_dashboard.acefone_calls`
    WHERE source = 'Acefone'
""").to_dataframe()
print(f"MAX(call_datetime) in BQ: {df.iloc[0]['last_time']}")
print(f"Current UTC time:        {datetime.utcnow()}")

print("\n=== 4. RECENT PIPELINE RUNS ===")
logs = client.query("""
    SELECT run_date, run_timestamp, rows_inserted
    FROM `studious-apex-488820-c3.crm_scheduled_results.MASTER_QUERY_REFRESH`
    WHERE pipeline_name = 'ACEFONE'
    ORDER BY run_date DESC LIMIT 10
""").to_dataframe()
print(logs.to_string())
