import requests, os, pandas as pd, pytz
from datetime import datetime, timedelta
from google.cloud import bigquery

PROJECT_ID = "studious-apex-488820-c3"
TABLE_ID = f"{PROJECT_ID}.crm_dashboard.acefone_calls"
india = pytz.timezone("Asia/Kolkata")
client = bigquery.Client()
HEADERS = {"Authorization": f"Bearer {os.getenv('ACEFONE_API_KEY')}",
           "accountid": os.getenv("ACEFONE_ACCOUNT_ID")}

def safe_duration(x):
    try:
        if isinstance(x, str) and ":" in x:
            h, m, s = map(int, x.split(":"))
            return h*3600 + m*60 + s
        return int(x or 0)
    except:
        return 0

print("=== STAGE 0: TABLE STATE BEFORE ===")
st = client.query(f"""
    SELECT MAX(call_datetime) AS mx,
           COUNTIF(DATE(call_datetime,'Asia/Kolkata')='2026-07-17') AS today_rows
    FROM `{TABLE_ID}` WHERE source='Acefone'
""").to_dataframe().iloc[0]
print(f"MAX(call_datetime): {st['mx']} | rows dated 17th IST: {st['today_rows']}")

last_utc = st["mx"]
if last_utc.tzinfo is None:
    last_utc = pytz.utc.localize(last_utc)
start_ist = last_utc.astimezone(india) - timedelta(minutes=30)
end_ist = datetime.now(india) - timedelta(minutes=5)
batch_now_utc = datetime.now(pytz.utc)
print(f"Fetch window: {start_ist}  ->  {end_ist}")

print("\n=== STAGE 1: FETCH (pipeline's exact params, limit=1000) ===")
rows, seen, page = [], set(), 1
while True:
    params = {"from_date": start_ist.strftime("%Y-%m-%d %H:%M:%S"),
              "to_date": end_ist.strftime("%Y-%m-%d %H:%M:%S"),
              "page": page, "limit": 1000}
    try:
        r = requests.get("https://api.acefone.in/v1/call/records",
                         headers=HEADERS, params=params, timeout=180)
    except Exception as e:
        print(f"Page {page}: REQUEST EXCEPTION -> {e}")
        break
    print(f"Page {page}: HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"  BODY: {r.text[:500]}")
        break
    p = r.json()
    data = p.get("results", [])
    print(f"  api_count={p.get('count')} | records_this_page={len(data)}")
    if not data:
        break
    ids = {str(c.get("call_id")) for c in data}
    if ids.issubset(seen):
        print("  pagination repeat detected -> stopping")
        break
    seen |= ids
    for call in data:
        dt = pd.to_datetime(call.get("end_stamp"), errors="coerce")
        if pd.isna(dt):
            continue
        dt_ist = india.localize(dt)
        agent = None
        if call.get("agent_name"):
            agent = call["agent_name"]
        elif call.get("missed_agents"):
            m = call.get("missed_agents")
            agent = m[0].get("name") if m and isinstance(m, list) else None
        else:
            for f in call.get("call_flow", []):
                if f.get("type") == "Agent":
                    agent = f.get("name")
                    break
        rows.append({
            "client_number": str(call.get("client_number",""))[-10:],
            "call_datetime": dt_ist.astimezone(pytz.utc),
            "call_owner": str(agent or "").replace("-Extension", "").strip(),
            "call_duration": safe_duration(call.get("call_duration")),
            "status": call.get("status"),
            "call_id": "ACE_" + str(call.get("call_id")),
            "direction": call.get("direction"),
            "service": call.get("service"),
            "reason": call.get("reason"),
            "call_type": (call.get("dialer_call_details") or {}).get("call_type"),
            "source": "Acefone",
            "updated_at": batch_now_utc,
            "updated_at_ampm": end_ist.strftime("%d/%m/%Y %I:%M %p"),
            "Call Date": dt_ist.date()
        })
    if len(data) < 1000:
        break
    page += 1
    if page > 50:
        break

df = pd.DataFrame(rows)
print(f"TOTAL fetched rows: {len(df)}")
if df.empty:
    print(">>> FETCH RETURNED NOTHING — this is the failure point. See HTTP status above.")
    raise SystemExit(0)
print(f"call_datetime range: {df['call_datetime'].min()} -> {df['call_datetime'].max()}")

print("\n=== STAGE 2: FILTER ===")
df = df[(df["direction"]=="outbound") |
        ((df["direction"]=="inbound") & (df["status"].str.lower()=="answered"))]
df = df[~((df["status"].str.lower()=="answered") & (df["call_duration"]==0))]
print(f"Rows after filter: {len(df)}")

print("\n=== STAGE 3: DEDUP ===")
start_ts = start_ist.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S")
bq_ids = client.query(f"""
    SELECT call_id FROM `{TABLE_ID}`
    WHERE call_datetime >= TIMESTAMP('{start_ts}') AND source='Acefone'
""").to_dataframe()["call_id"].unique().tolist()
print(f"Existing IDs in BQ within window: {len(bq_ids)}")
new = df[~df["call_id"].isin(bq_ids)].drop_duplicates(subset=["call_id"])
print(f"NEW records to insert: {len(new)}")
if new.empty:
    print(">>> Everything blocked by dedup — table still contains these IDs.")
    raise SystemExit(0)

print("\n=== STAGE 4: LOAD + VERIFY ===")
new = new.copy()
new["call_datetime"] = pd.to_datetime(new["call_datetime"], utc=True)
new["updated_at"] = pd.to_datetime(new["updated_at"], utc=True)
new["call_duration"] = pd.to_numeric(new["call_duration"], errors="coerce").fillna(0).astype(int)
for col in new.columns:
    if new[col].dtype == "object" and col not in ["updated_at","call_datetime","Call Date"]:
        new[col] = new[col].astype(str)
job = client.load_table_from_dataframe(
    new, TABLE_ID, bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"))
job.result()
print(f"Load job {job.job_id} state={job.state}, output_rows={job.output_rows}")

after = client.query(f"""
    SELECT MAX(call_datetime) AS mx,
           COUNTIF(DATE(call_datetime,'Asia/Kolkata')='2026-07-17') AS today_rows
    FROM `{TABLE_ID}` WHERE source='Acefone'
""").to_dataframe().iloc[0]
print(f"AFTER LOAD -> MAX(call_datetime): {after['mx']} | rows dated 17th IST: {after['today_rows']}")
