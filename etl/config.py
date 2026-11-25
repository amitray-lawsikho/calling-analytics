"""Settings. Keys come from environment variables (GitHub secrets) only."""
from __future__ import annotations

import os

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip() or "https://ybkldfcilvibryoxcuvq.supabase.co"

# Team sheet used by the Streamlit calling dashboard for caller -> team mapping (Caller Name, Team Name, Vertical, Analyst)
TEAM_CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vRT73ztvPNZSvIu5WLxo-3WQ76JMAnt4P9dITd4EAbjSvuDytfgvdfri1WPXotCjm_"
                "Etnb80_Q7S-wf/pub?gid=0&single=true&output=csv")
# Team sheet used by the Ozonetel pipeline to keep only our agents (3rd column)
OZONETEL_TEAM_URL = os.environ.get("TEAM_SHEET_URL", "").strip() or TEAM_CSV_URL

MANUAL_CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vTN5y1tWu18GfvHx3l5wUEgW_b1CpsweZcoAOzK6Erqe2ATvTNhdFFvkc0b32QNGO3"
                  "pk1d3ACiAoSS6/pub?gid=1479782046&single=true&output=csv")
STAGE_CSV_URL = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vS_DlibGh09XYxLh8SOexTLstUmNPxdLc_2LuX-qUqh6ZbvmAbRxGtm8TaZwz6Zuyz5-04tXp55PLWD"
                 "/pub?gid=0&single=true&output=csv")

ACEFONE_URL = "https://api.acefone.in/v1/call/records"
MCUBE_URL = "https://config.mcube.com/Restmcube-api/callsdatabydate"
OZONETEL_URL = "https://in1-ccaas-api.ozonetel.com/ca_reports/fetchCDRDetails"

KEEP_DAYS = 90          # stage changes / manual calls kept this long; daily summaries kept forever
CALL_KEEP_DAYS = 60     # individual dialler calls kept this long (Amit, 25 Nov 2026: 90 days outgrew the free database);
                        # agent_day / stage_day for older days are never rebuilt or deleted
REWIND_MIN = 30         # re-read this much before the last stored call (duplicates are dropped by id)
LAG_MIN = 5             # don't read the last 5 minutes (durations still settling)

BQ_PROJECT = "studious-apex-488820-c3"   # only for the one-time copy of history
BQ_DATASET = "crm_dashboard"
