import requests
import os
import pandas as pd
import pytz
import time
from datetime import datetime, timedelta
from google.cloud import bigquery

# ---------------- CONFIG ----------------

PROJECT_ID = "studious-apex-488820-c3"
DATASET = "crm_dashboard"
TABLE = "acefone_calls"

TABLE_ID = f"{PROJECT_ID}.{DATASET}.{TABLE}"

ACE_API_KEY = os.getenv("ACEFONE_API_KEY")
ACCOUNT_ID = os.getenv("ACEFONE_ACCOUNT_ID")
ACE_URL = "https://api.acefone.in/v1/call/records"

OZO_API_KEY = os.getenv("OZONETEL_API_KEY")
OZO_USERNAME = os.getenv("OZONETEL_USERNAME")
OZO_URL = "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails"

TEAM_SHEET_URL = os.getenv("TEAM_SHEET_URL")

LIMIT = 1000
india = pytz.timezone("Asia/Kolkata")

client = bigquery.Client()

# ---------------- LAST TIME ----------------

def get_last_call_time_by_source(source):

    try:
        query = f"""
        SELECT MAX(call_datetime) last_time, COUNT(1) total_rows
        FROM `{TABLE_ID}`
        WHERE source = @source
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source", "STRING", source)
            ]
        )

        df = client.query(query, job_config=job_config).to_dataframe()

        last_time = df.iloc[0]["last_time"]
        total_rows = df.iloc[0]["total_rows"]

        if total_rows == 0 or pd.isna(last_time):
            print(f"⚠️ {source} NEW → fetching 10 days")
            return datetime.utcnow() - timedelta(days=10)

        print(f"✅ {source} incremental from:", last_time)
        return last_time

    except Exception as e:
        print("Last time fetch error:", str(e))
        return datetime.utcnow() - timedelta(days=2)


# ---------------- TEAM ----------------

def get_team_members():

    try:
        df = pd.read_csv(TEAM_SHEET_URL)

        team = (
            df.iloc[:, 2]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )

        print("Team Loaded:", len(team))
        return team

    except Exception as e:
        print("Team load failed:", str(e))
        return []


# ---------------- SAFE DURATION ----------------

def safe_duration(x):

    try:
        if pd.isna(x):
            return 0

        if isinstance(x, str):
            if ":" in x:
                h, m, s = map(int, x.split(":"))
                return h*3600 + m*60 + s
            return int(x)

        return int(x)

    except:
        return 0


# ---------------- ACEFONE ----------------

def fetch_acefone(last_time):

    fetch_start = last_time - timedelta(hours=2)

    from_date = fetch_start.strftime("%Y-%m-%d %H:%M:%S")
    to_date = datetime.now(india).strftime("%Y-%m-%d %H:%M:%S")

    headers = {
        "Authorization": f"Bearer {ACE_API_KEY}",
        "accountid": ACCOUNT_ID,
        "accept": "application/json"
    }

    rows = []
    page = 1

    while True:

        params = {
            "from_date": from_date,
            "to_date": to_date,
            "page": page,
            "limit": LIMIT
        }

        try:
            response = requests.get(ACE_URL, headers=headers, params=params)

            if response.status_code != 200:
                print("Acefone API error:", response.text)
                break

            results = response.json().get("results", [])

            if not results:
                break

            for call in results:

                try:
                    if not call or not isinstance(call, dict):
                        continue

                    call_id = call.get("call_id")
                    if not call_id:
                        continue

                    dialer_details = call.get("dialer_call_details") or {}

                    # ✅ FIXED DATETIME HERE (UTC)
                    call_dt = pd.to_datetime(
                        call.get("end_stamp"),
                        errors="coerce",
                        utc=True
                    )

                    rows.append({

                        "client_number": str(call.get("client_number", "")).replace("+91","")[-10:],

                        "call_datetime": call_dt,

                        "call_owner": call.get("agent_name"),

                        "call_duration": safe_duration(call.get("call_duration")),

                        "status": "Answered" if call.get("status") == "answered" else "Missed",

                        "call_id": call_id,

                        "direction": call.get("direction"),

                        "service": call.get("service"),

                        "reason": call.get("reason"),

                        "call_type": dialer_details.get("call_type"),

                        "source": "Acefone",

                        "updated_at": datetime.utcnow(),

                        "updated_at_ampm": datetime.now(india).strftime("%d/%m/%Y %I:%M %p")

                    })

                except Exception as e:
                    print("Acefone row error:", str(e))
                    continue

            page += 1
            time.sleep(0.2)

        except Exception as e:
            print("Acefone fetch error:", str(e))
            break

    print("Acefone rows:", len(rows))

    return pd.DataFrame(rows)


# ---------------- OZONETEL ----------------

def fetch_ozonetel(last_time, team):

    rows = []
    failed_days = []

    start_date = (last_time - timedelta(days=2)).date()
    end_date = datetime.now().date()

    team_clean = set([x.strip().lower() for x in team])

    current_date = start_date

    while current_date <= end_date:

        from_date = f"{current_date} 00:00:00"
        to_date   = f"{current_date} 23:59:59"

        print(f"\n📅 Fetching Ozonetel for: {from_date}")

        success = False
        retries = 0

        while not success and retries < 3:

            try:
                response = requests.get(
                    OZO_URL,
                    headers={
                        "Content-Type": "application/json",
                        "apiKey": OZO_API_KEY
                    },
                    json={
                        "fromDate": from_date,
                        "toDate": to_date,
                        "userName": OZO_USERNAME
                    },
                    timeout=30
                )

                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")

                data = response.json()

                if data.get("status") == "false":
                    raise Exception(data.get("message"))

                details = data.get("details", [])

                print(f"📊 Raw records: {len(details)}")

                for call in details:

                    try:
                        if not call or not isinstance(call, dict):
                            continue

                        agent = str(call.get("AgentName", "")).strip().lower()

                        # ✅ TEAM FILTER
                        if team_clean and agent not in team_clean:
                            continue

                        call_id = call.get("CallID")
                        if not call_id:
                            continue

                        # ✅ DATETIME STANDARDIZATION
                        call_dt = pd.to_datetime(
                            f"{call.get('CallDate')} {call.get('StartTime')}",
                            format="%d/%m/%Y %H:%M:%S",
                            errors="coerce"
                        )

                        if pd.notna(call_dt):
                            call_dt = india.localize(call_dt).astimezone(pytz.UTC)

                        # ✅ DURATION SAFE
                        def time_to_seconds(t):
                            try:
                                h, m, s = map(int, str(t).split(":"))
                                return h*3600 + m*60 + s
                            except:
                                return 0

                        rows.append({
                            "client_number": str(call.get("E164",""))[-10:],
                            "call_datetime": call_dt,
                            "call_owner": call.get("AgentName"),
                            "call_duration": time_to_seconds(call.get("Duration")),
                            "status": call.get("Status") or call.get("DialStatus"),
                            "call_id": call_id,
                            "direction": call.get("Type"),
                            "service": call.get("Type"),
                            "reason": call.get("DialStatus"),
                            "call_type": call.get("Type"),
                            "source": "Ozonetel",
                            "updated_at": datetime.utcnow(),
                            "updated_at_ampm": datetime.now(india).strftime("%d/%m/%Y %I:%M %p")
                        })

                    except Exception as row_err:
                        print("⚠️ Row error:", str(row_err))
                        continue

                success = True

            except Exception as e:
                retries += 1
                print(f"❌ Retry {retries}/3 failed:", str(e))
                time.sleep(2)

        if not success:
            print(f"🚨 FAILED DAY: {current_date}")
            failed_days.append(str(current_date))

        current_date += timedelta(days=1)
        time.sleep(0.3)

    print("\n✅ Ozonetel FINAL rows:", len(rows))

    if failed_days:
        print("\n🚨 Missing Days:", failed_days)

    return pd.DataFrame(rows)


# ---------------- DEDUP ----------------

def remove_existing(df, source):

    if df.empty:
        return df

    try:
        call_ids = df["call_id"].tolist()

        query = f"""
        SELECT call_id
        FROM `{TABLE_ID}`
        WHERE source=@source
        AND call_id IN UNNEST(@ids)
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("source","STRING",source),
                bigquery.ArrayQueryParameter("ids","STRING",call_ids)
            ]
        )

        existing = client.query(query, job_config=job_config).to_dataframe()

        existing_ids = set(existing["call_id"].tolist())

        return df[~df["call_id"].isin(existing_ids)]

    except Exception as e:
        print("Dedup error:", str(e))
        return df


# ---------------- LOAD ----------------

def load_to_bigquery(df):

    if df.empty:
        print("No rows to insert")
        return

    # ✅ UNIFIED DATETIME (NO DATA LOSS)
    df["call_datetime"] = pd.to_datetime(df["call_datetime"], errors="coerce", utc=True)

    # ✅ FILL NULL INSTEAD OF DROPPING
    df["call_datetime"] = df["call_datetime"].fillna(pd.Timestamp.utcnow())

    df["call_duration"] = df["call_duration"].apply(safe_duration)

    job = client.load_table_from_dataframe(
        df,
        TABLE_ID,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    )

    job.result()

    print("Inserted rows:", len(df))


# ---------------- MAIN ----------------

def run_pipeline():

    team = get_team_members()

    ace_last = get_last_call_time_by_source("Acefone")
    ozo_last = get_last_call_time_by_source("Ozonetel")

    ace_df = fetch_acefone(ace_last)
    ozo_df = fetch_ozonetel(ozo_last, team)

    ace_df = remove_existing(ace_df, "Acefone")
    ozo_df = remove_existing(ozo_df, "Ozonetel")

    final_df = pd.concat([ace_df, ozo_df], ignore_index=True)

    load_to_bigquery(final_df)

    print("✅ Pipeline completed")


if __name__ == "__main__":
    run_pipeline()
