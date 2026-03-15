# Revenue & Sales Intelligence Data Pipelines

This repository contains automated ETL pipelines that ingest operational data from multiple sources and load it into **Google BigQuery** to power analytics and dashboards. The pipelines ensure near-real-time availability of call data, lead data, and revenue data used for internal reporting and sales intelligence.

---

# Overview

The system collects data from three primary sources:

1. **Acefone Call Logs**
2. **LeadSquared (LSQ) Leads & Users**
3. **Revenue Sheet (Google Sheets / Revenue Source)**

Each source is processed by a dedicated pipeline and loaded into **BigQuery (`crm_dashboard` dataset)** where downstream dashboards (e.g., Looker Studio) read from curated tables.

---

# Architecture

Data flows into BigQuery through automated pipelines triggered using **GitHub Actions** on a schedule.

```
Data Sources
     │
     ├── Acefone Call API
     ├── LeadSquared API
     └── Revenue Sheet
          │
          ▼
Python ETL Pipelines
(Executed via GitHub Actions)
          │
          ▼
Google BigQuery
Dataset: crm_dashboard
          │
          ▼
Analytics Layer
(Looker Studio / BI Dashboards)
```

---

# Pipelines

## 1. Acefone Call Pipeline

**Script**

```
acefone_pipeline.py
```

**Purpose**

Fetches call records from the Acefone API and appends them to the BigQuery table:

```
crm_dashboard.acefone_calls
```

**Key Features**

* Incremental ingestion using `call_datetime`
* 90-minute overlap window to prevent missed calls
* Duplicate protection using `call_id`
* Automatic formatting of `updated_at_ampm`
* Handles API pagination
* Cleans phone numbers to standard 10-digit format

**Refresh Frequency**

```
Every 1 hour
```

**Data Loaded**

| Field           | Description                  |
| --------------- | ---------------------------- |
| client_number   | Customer phone number        |
| call_datetime   | Call timestamp               |
| call_owner      | Agent name                   |
| call_duration   | Call duration                |
| status          | Answered / Missed            |
| call_id         | Unique call identifier       |
| direction       | inbound / outbound           |
| service         | Call service type            |
| reason          | Call reason                  |
| call_type       | Dialer type                  |
| updated_at      | Pipeline execution timestamp |
| updated_at_ampm | Human readable refresh time  |

**Destination Table**

```
crm_dashboard.acefone_calls
```

---

## 2. LeadSquared (LSQ) Pipeline

**Script**

```
lsq_pipeline.py
```

**Purpose**

Fetches lead and user data from the **LeadSquared API** and loads it into BigQuery.

This pipeline supports lead-level operational metrics used in the **Sales Intelligence Dashboard**.

**Refresh Frequency**

```
Every 2 hours
```

**Data Sources**

* LeadSquared Leads API
* LeadSquared Users API

**Destination Tables**

```
crm_dashboard.lsq_leads
crm_dashboard.lsq_users
```

**Key Data Fields**

| Field                      | Description            |
| -------------------------- | ---------------------- |
| ProspectID                 | Unique lead identifier |
| OwnerId                    | Lead owner             |
| ProspectStage              | Current stage of lead  |
| Modified Date              | Last update timestamp  |
| mx_Last_Call_new           | Last call timestamp    |
| mx_Follow_up_date_and_time | Scheduled follow-up    |
| mx_Phone_call_counter      | Number of calls made   |

**Purpose in Analytics**

The LSQ pipeline powers metrics such as:

* Fresh leads
* Discovery calls
* Roadmaps completed
* Follow-ups pending
* Callback opportunities
* Counselling stages

---

## 3. Revenue Pipeline

**Script**

```
revenue_pipeline.py
```

**Purpose**

Ingests enrollment and revenue data and loads it into BigQuery for financial reporting and sales performance tracking.

**Refresh Frequency**

```
Every 1 hour
```

**Destination Table**

```
crm_dashboard.revenue_sheet
```

**Data Fields**

| Field                    | Description       |
| ------------------------ | ----------------- |
| Date                     | Transaction date  |
| Caller_name              | Sales agent       |
| Fee_paid                 | Revenue collected |
| Course_Price             | Course value      |
| Enrollment               | Enrollment type   |
| LawSikho_Skill_Arbitrage | Product category  |

**Analytics Supported**

* Revenue tracking
* Enrollment counts
* Product revenue split
* Pool value calculations
* Sales performance reporting

---

# BigQuery Dataset

All pipelines write into the dataset:

```
studious-apex-488820-c3.crm_dashboard
```

Key tables:

| Table         | Description                 |
| ------------- | --------------------------- |
| acefone_calls | Call activity data          |
| lsq_leads     | Lead lifecycle data         |
| lsq_users     | Lead owner mapping          |
| revenue_sheet | Enrollment and revenue data |

---

# Automation

Pipelines are executed automatically using **GitHub Actions**.

**Scheduling**

| Pipeline | Frequency     |
| -------- | ------------- |
| Acefone  | Every 1 hour  |
| LSQ      | Every 2 hours |
| Revenue  | Every 1 hours |

Workflows are defined under:

```
.github/workflows/
```

---

# Environment & Secrets

Sensitive credentials are stored securely in **GitHub Secrets**.

Required secrets:

| Secret Name        | Description                       |
| ------------------ | --------------------------------- |
| BQ_CREDENTIALS     | Google Cloud service account JSON |
| ACEFONE_API_KEY    | Acefone API key                   |
| ACEFONE_ACCOUNT_ID | Acefone account ID                |
| LSQ_ACCESS_KEY     | LeadSquared access key            |
| LSQ_SECRET_KEY     | LeadSquared secret key            |

These credentials authenticate pipelines for API access and BigQuery loading.

---

# Dependencies

All Python dependencies are managed through:

```
requirements.txt
```

Key libraries:

* pandas
* requests
* pytz
* google-cloud-bigquery
* google-api-python-client
* pyarrow

---

# Monitoring

Pipeline runs can be monitored via:

```
GitHub → Actions
```

Logs include:

* API fetch status
* Rows processed
* BigQuery load status
* Error traces

---

# Data Retention

The **Acefone calls table** is partitioned by date.

Older partitions may be automatically removed according to the configured **partition expiration policy**, ensuring efficient storage usage while keeping recent operational data available.

---

# Downstream Usage
The BigQuery tables produced by these pipelines feed:
* Sales Operations Dashboards
* Revenue Performance Tracking
* Calling Analytics
* Sales Intelligence Reports

Primary visualization layer:

```
Google Looker Studio
```

---

# Maintainer

**Amit Ray**
Sr. Data Analyst
Sales Intelligence & Data Automation

---

# License
Internal analytics pipeline for organizational reporting.
