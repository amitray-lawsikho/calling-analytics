# 🚀 Revenue BigQuery ELT Pipeline

This repository contains a **production-grade ELT pipeline system** designed to ingest CRM and telephony data into Google BigQuery. It enables real-time analytics and automated dashboarding by bridging the gap between third-party APIs and BigQuery.

---

## 🔌 Data Sources

The system orchestrates data from five primary sources:
* **Telephony:** Acefone & Ozonetel (Call logs/recordings)
* **CRM:** LeadSquared (Prospects & Activities)
* **Manual Inputs:** Google Sheets (Manual calls & Revenue targets)

---

## 🏗️ Architecture

```mermaid
graph TD
    A[APIs / Google Sheets] --> B{Data Extraction}
    B --> C[Data Transformation]
    C --> D[Deduplication Engine]
    D --> E[(BigQuery Tables)]
    E --> F[Dashboards / BI]
⚙️ Core Concepts🔄 Incremental ProcessingTo optimize performance and cost, we avoid full refreshes where possible:Acefone: Fetches based on MAX(call_datetime).Ozonetel: Date-wise incremental processing.LSQ: Uses a 59-day rolling window to capture updates to existing leads.⏱️ Timezone & Lag HandlingProcessing: All logic runs in IST (Asia/Kolkata).Storage: BigQuery tables are stored in UTC for global compatibility.Lag Buffer: A mandatory 5-minute delay is applied before fetching the latest data to ensure call durations are finalized by the provider.🔁 Deduplication StrategyPipelineLogic / Primary KeyAcefonecall_idOzonetelCallID + StartTime + EndTimeLSQProspectIDSheetsFull Refresh (WRITE_TRUNCATE)📋 Logging & ObservabilityAll pipeline executions are logged into a master audit table:crm_scheduled_results.MASTER_QUERY_REFRESHTracked Fields:run_date | pipeline_name | run_timestamp | rows_inserted📞 Pipeline Deep Dive1. Acefone PipelineLogic: Incremental with a 30-minute backward "overlap" buffer to catch edge cases.Filters: Includes all Outbound calls and Answered Inbound calls.2. LeadSquared (LSQ) PipelineLogic: 59-day rolling window using ModifiedOn.Features: Automated Owner ID-to-Name mapping and API retry logic.Load Type: WRITE_TRUNCATE (on the windowed subset).3. Ozonetel PipelineLogic: Iterates day-by-day; filters for specific team agents to reduce noise.⚡ Automation (GitHub Actions)The pipelines are fully automated. Every workflow follows a standardized execution path:Checkout ➔ Setup Python ➔ Auth GCP ➔ Run Script📂 Scheduled WorkflowsWorkflowSchedule (IST)PurposeMaster Pipeline11:00, 13:30, 17:30, 19:30 (Mon-Fri)Production Run: Syncs all data sources.Manual Calls07:30 DailySyncs manual logs from Google Sheets.Ad-hocManual TriggerIndividual pipelines (Acefone, LSQ, etc.) can be run manually.🔐 ConfigurationSecrets RequiredThe following secrets must be configured in GitHub Settings > Secrets:BQ_CREDENTIALS (JSON Service Account)ACEFONE_API_KEY, OZONETEL_API_KEY, LSQ_SECRET_KEYTEAM_SHEET_URL📦 InstallationBashpip install -r requirements.txt
⚠️ Best PracticesIST Consistency: Always perform date math in IST before converting to UTC for storage.Lag Buffer: Never reduce the 5-minute lag; doing so results in "0 second" call duration errors.Deduplication: Ensure the deduplication step runs before the final BigQuery load.Author: Amit Ray⭐ Star this repo if it helped your ETL workflow!
