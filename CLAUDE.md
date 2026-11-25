# Calling Analytics Dashboard

Calling metrics (Dynamic Dashboard of the old Streamlit app) for LawSikho / Skill Arbitrage callers: Acefone, MCube, Ozonetel,
manual calls and LSQ stage changes. Rules: [docs/definitions.md](docs/definitions.md) (never change without Amit's OK).

## How it runs
- `etl/refresh.py`: incremental reads of the dialler APIs (watermark = last stored call − 30 min, duplicates dropped by id),
  manual-calls and stage-change sheets, team sheet -> Supabase project "Sales Analytics Dashboard" (`ybkldfcilvibryoxcuvq`,
  shared with the old Streamlit sign-ins). Then it rebuilds `agent_day` (one row per caller per day) and `stage_day`.
  `--backfill true` copied the BigQuery history once; BigQuery is not used otherwise.
- `.github/workflows/refresh.yml`: workflow_dispatch only; Supabase pg_cron job `calling-refresh-2300` (once a day, 23:00 IST) and the Update button start it
  (Vault secret `github_token`). No Claude and no LLM call in runs.
- `web/`: static site (no build step), Elite Green theme; no sign-in page of its own: opened only through the Sales Intelligence Portal (one-time sign-in link; Amit, 18 Nov 2026), `is_allowed()` = access to `calling` in `portal.access`.
- Database: calls and manual_calls (60 days since 25 Nov 2026, ids = 64-bit hash; stage_changes 90 days; agent_day from 27 Aug 2026, the first day with dialler calls), manual_calls, stage_changes, team_map, load_batch, agent_day, stage_day,
  refresh_log, refresh_requests; RPCs ca_meta, ca_report, ca_unique_leads, ca_cdr, ca_stage_export (dashboard), ca_* helpers (pipeline).
  Change schema with a migration, keep RLS on. Free plan: keep the database under 500 MB.
- `legacy/`: the old BigQuery pipelines, kept for reference only.

## Rules
- Update button (Amit, 25 Nov 2026): only Calling admins (`portal.can_admin('calling')`; button shown via `ca_can_update`); `request_refresh` refuses while a refresh is running ('already_running') and within an hour
  of the last successful one ('recent'), so refreshes never pile up; pg_cron and the daily reports call `_dispatch_refresh` directly.
- Secrets only in GitHub secrets: SUPABASE_SERVICE_KEY, BQ_CREDENTIALS, ACEFONE_API_KEY, ACEFONE_ACCOUNT_ID, MCUBE_TOKEN,
  OZONETEL_API_KEY, OZONETEL_USERNAME, TEAM_SHEET_URL. Never print them.
- The database holds customer phone numbers, names and emails (needed for the downloads, approved by Amit): never dump them into chat.
- Token economy: inspect a few rows, aggregate in SQL.
- Run `python -m pytest -q` before every push; add a test with each rule change.
