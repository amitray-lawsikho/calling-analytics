# ================= MAIN PIPELINE =================
def run_pipeline():
    # Load Team Mapping
    team_df = pd.read_csv(TEAM_SHEET_URL)
    team_agents = team_df.iloc[:, 2].dropna().unique().tolist()
    team_agents_clean = [str(x).strip() for x in team_agents]

    start_date = get_last_date_from_bq()
    
    # --- LAG LOGIC START ---
    batch_now_utc = datetime.now(pytz.utc)
    # Define our data cutoff: 5 minutes ago to allow Ozonetel to finalize durations
    fetch_to_ist = datetime.now(india) - timedelta(minutes=5)
    today_date = fetch_to_ist.date()
    # --- LAG LOGIC END ---
    
    all_data = []
    curr = start_date
    headers = {"Content-Type": "application/json", "apiKey": API_KEY}

    while curr <= today_date:
        day_str = curr.strftime("%Y-%m-%d")
        
        # Determine the exact time range for today vs. previous days
        start_time_str = "00:00:00"
        # If we are fetching for the current day, cap the time at our 5-min lag point
        if curr == today_date:
            end_time_str = fetch_to_ist.strftime("%H:%M:%S")
        else:
            end_time_str = "23:59:59"

        print(f"Fetching Ozonetel data for: {day_str} {start_time_str} to {end_time_str}")
        
        payload = {
            "fromDate": f"{day_str} {start_time_str}", 
            "toDate": f"{day_str} {end_time_str}", 
            "userName": USERNAME
        }
        
        try:
            res = requests.get(API_URL, headers=headers, json=payload, timeout=60)
            details = res.json().get("details", [])
            if details:
                processed_df = process_day_data(details, team_agents_clean)
                if not processed_df.empty:
                    processed_df["CallDate"] = curr
                    all_data.append(processed_df)
        except Exception as e:
            print(f"Error on {day_str}: {e}")
        
        curr += timedelta(days=1)
        time.sleep(1)
    
    if not all_data:
        print("No valid data found in API for range.")
        return

    final_df = pd.concat(all_data, ignore_index=True)

    # Filter: Remove missed inbound calls
    initial_count = len(final_df)
    final_df = final_df[~((final_df["Type"].str.lower() == "inbound") & 
                          (final_df["Status"].str.lower() == "unanswered"))].copy()
    
    if final_df.empty:
        print("No records left after filtering.")
        return

    # 1. Standardize Timestamps (IST to UTC)
    final_df["CallDate_str"] = final_df["CallDate"].astype(str)
    for col in ["StartTime", "EndTime"]:
        final_df[col] = pd.to_datetime(final_df["CallDate_str"] + " " + final_df[col].astype(str), errors='coerce')
        final_df[col] = final_df[col].dt.tz_localize(india, ambiguous='infer').dt.tz_convert("UTC")

    final_df.drop(columns=["CallDate_str"], inplace=True)
    final_df["CallDate"] = pd.to_datetime(final_df["CallDate"]).dt.date

    # 2. Add Metadata & Type Enforcement
    # Use fetch_to_ist so the dashboard shows the 5-minute lag time
    final_df["updated_at_ampm"] = fetch_to_ist.strftime("%d/%m/%Y %I:%M %p")
    final_df["updated_at_utc"] = batch_now_utc # Helper for internal tracking
    
    final_df["CallID"] = final_df["CallID"].astype(str)
    final_df["duration_sec"] = pd.to_numeric(final_df["duration_sec"]).fillna(0).astype(int)
    final_df["talktime_sec"] = pd.to_numeric(final_df["talktime_sec"]).fillna(0).astype(int)

    # 3. INCREMENTAL LOGIC (Triple Key Deduplication)
    # We fetch existing records from start_date onwards to compare
    query = f"SELECT CAST(CallID AS STRING) as CallID, StartTime, EndTime FROM `{TABLE_ID}` WHERE CallDate >= '{start_date}'"
    existing_bq = client.query(query).to_dataframe()
    
    if not existing_bq.empty:
        existing_bq['StartTime'] = pd.to_datetime(existing_bq['StartTime'], utc=True)
        existing_bq['EndTime'] = pd.to_datetime(existing_bq['EndTime'], utc=True)
        final_df['StartTime'] = pd.to_datetime(final_df['StartTime'], utc=True)
        final_df['EndTime'] = pd.to_datetime(final_df['EndTime'], utc=True)
        
        merged = final_df.merge(existing_bq, on=["CallID", "StartTime", "EndTime"], how="left", indicator=True)
        to_push = merged[merged["_merge"] == "left_only"].drop(columns=["_merge"]).copy()
    else:
        to_push = final_df

    # 4. PUSH TO BIGQUERY
    if not to_push.empty:
        print(f"Pushing {len(to_push)} unique records to BigQuery.")
        job_config = bigquery.LoadJobConfig(
            write_disposition="WRITE_APPEND",
            schema=[
                bigquery.SchemaField("StartTime", "TIMESTAMP"),
                bigquery.SchemaField("EndTime", "TIMESTAMP"),
                bigquery.SchemaField("CallDate", "DATE"),
            ]
        )
        client.load_table_from_dataframe(to_push, TABLE_ID, job_config=job_config).result()
        print("✅ Ozonetel Pipeline successful.")
    else:
        print("No new unique records found.")
