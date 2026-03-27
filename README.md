🚀 Overview

This repository contains a production-grade ELT pipeline system that ingests CRM and call data from multiple sources into Google BigQuery for analytics and dashboarding.

🔌 Data Sources
📞 Acefone (Call Data)
📲 Ozonetel (Call Data)
👥 LeadSquared (Leads CRM)
📝 Google Sheets (Manual Calls)
💰 Google Sheets (Revenue Data)
🏗️ Architecture
        APIs / Sheets
             │
        (Extract)
             │
        Data Cleaning
        Transformation
             │
        Deduplication
             │
        BigQuery Tables
             │
        Dashboard / BI
⚙️ Core Features
✅ Incremental Processing
Fetches only new data
Uses:
MAX(timestamp) (Acefone)
Rolling window (59 days)
⏱️ Timezone Handling
Input: IST (Asia/Kolkata)
Storage: UTC (BigQuery)
Ensures dashboard consistency
⏳ Lag Buffer (Critical)
5-minute delay before fetching latest data
Prevents:
Partial call records
Incorrect durations
🔁 Deduplication Strategy
Pipeline	Deduplication Logic
Acefone	call_id
Ozonetel	CallID + StartTime + EndTime
LeadSquared	ProspectID
Sheets	Full refresh
📋 Central Logging

All pipelines log into:

crm_scheduled_results.MASTER_QUERY_REFRESH
Field	Description
run_date	Execution date
pipeline_name	Source name
run_timestamp	IST timestamp
rows_inserted	Rows processed
📞 Pipelines
1️⃣ Acefone Pipeline

Purpose: Fetch call records from Acefone API

🔹 Key Logic

Incremental load using:

MAX(call_datetime)
30-minute backward buffer
5-minute lag buffer
🔹 Filters
✅ Outbound calls
✅ Inbound (answered only)
2️⃣ LeadSquared Pipeline

Purpose: Fetch leads & updates

🔹 Key Logic
Last 59 days data
Owner ID → Name mapping
Retry mechanism for API failures
🔹 Load Type
WRITE_TRUNCATE
3️⃣ Ozonetel Pipeline

Purpose: Fetch call data (day-wise)

🔹 Key Logic
Iterates day-by-day
Applies 5-min lag for current day
Filters team-specific agents
🔹 Deduplication (Strong)
CallID + StartTime + EndTime
🔹 Filters
❌ Removes inbound unanswered calls
4️⃣ Manual Calls Pipeline (Google Sheets)

Purpose: Capture manually logged calls

🔹 Key Logic
Smart date selection:
Manual Date (preferred)
Timestamp fallback
Converts minutes → seconds
Filters last 59 days
🔹 Load Type
WRITE_TRUNCATE
5️⃣ Revenue Pipeline (Google Sheets)

Purpose: Load revenue data

🔹 Transformations
Clean numeric values
Normalize phone numbers
Create:
Rev_Month
🔹 Filters
Last 59 days only
🔹 Load Type
WRITE_TRUNCATE
🧩 Common Utilities
🔹 Logging Function
log_pipeline(pipeline_name, rows)
🔹 Time Strategy
Business logic → IST
Storage → UTC
📦 Requirements
pandas
pytz
google-cloud-bigquery
google-api-python-client
google-auth
pyarrow
requests
db-dtypes
pandas-gbq
🔐 Environment Variables
Acefone
ACEFONE_API_KEY=
ACEFONE_ACCOUNT_ID=
LeadSquared
LSQ_ACCESS_KEY=
LSQ_SECRET_KEY=
BigQuery
BQ_CREDENTIALS=<JSON STRING>
▶️ Running Pipelines

Run any pipeline individually:

python acefone_pipeline.py
python lsq_pipeline.py
python ozonetel_pipeline.py
python manual_calls.py
python revenue_pipeline.py
📅 Scheduling

Recommended schedulers:

⏰ GitHub Actions
🕒 Cron Jobs
🌬️ Apache Airflow (for scaling)
📊 Data Freshness Strategy
Source	Strategy
Acefone	Incremental + Lag
Ozonetel	Incremental (daily loop)
LSQ	Rolling 59 days
Sheets	Full refresh
⚠️ Best Practices
Always maintain 5-minute lag
Never remove deduplication logic
Keep timezone handling consistent
Use WRITE_TRUNCATE only where intended
🏁 Summary

✔️ Reliable ingestion
✔️ No duplicate data
✔️ Optimized for dashboards
✔️ Scalable architecture

👨‍💻 Author

Amit Ray

⭐ If this helped, consider giving the repo a star!
