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
  `ca_watermarks` reads only the newest call per dialler through the (src, call_datetime) index (Amit, 25 Nov 2026: its old
  count(*) read the whole calls table every run, and the pipeline's retries stacked copies of it). No full-table reads of `calls` in a refresh.
  Change schema with a migration, keep RLS on. Free plan: keep the database under 500 MB.
- `legacy/`: the old BigQuery pipelines, kept for reference only.
- Suspicious Call Analysis (Amit, 26 Nov 2026): `etl/suspicious.py` keeps who ended each MCube / Acefone call, talk time, customer
  ring time and the recording link from the raw records the refresh already reads (`SUS.keep` in `etl/sources.py`), then step 5 of
  the refresh calls `sus_ingest` in its own try/except, so it can never stop the Calling data. Tables `sus_day` (caller x day x
  dialler counts), `sus_calls` (answered calls the agent ended with talk <= 180 s, with recording), `sus_seen` (ids already
  counted, 3 days), `sus_state`; 60 days like `calls`. Schema: `supabase/migrations/20261126_suspicious_calls.sql`.
  Access `sus_allowed()` = `suspicious` in `portal.access` (its own dashboard in the portal; day one every Calling admin got it as admin). One-time history: workflow
  `suspicious_backfill.yml` (`etl/sus_backfill.py`, resumable, own concurrency group, restarts itself until the last day is in). Page: `web/suspicious/`.
  Ozonetel is not included (Amit, 26 Nov 2026). Two sections (Amit, 26 Nov 2026): Suspicious calls (report, heatmap, list) and
  All Call Recordings: `call_rec` (every answered call with a recording link, talk time, who ended it, agent, number and time;
  60 days; Amit, 27 Nov 2026: once the database passes 460 MB (decimal MB, as the Usage page counts) `sus_trim` switches it to
  the last 30 days for good, kept in `sus_state` 'rec_days' (back to 60 only by deleting that row; migration
  `20261127_recordings_30_day_fallback.sql`; the page says so under its title), RPC `all_list` (Amit, 27 Nov 2026: reads only `call_rec`, never `calls`;
  plpgsql that builds the search from the filters in use, so each search gets its own plan; a month takes under 1 s,
  migration `20261127_call_rec_standalone.sql`). Suspicious talk filter = bands (Amit, 26 Nov 2026):
  Any length (<= 3 min), <= 5 s, 5-10 s, 10-20 s, 20 s-1 min, 1-3 min (`p_lo` exclusive, `p_max` inclusive; migration `20261126_talk_bands.sql`). No "Related" links between Calling and Suspicious.
  Recording links are stored short (Amit, 27 Nov 2026: room for 60 days): the common start of every link is kept once in `rec_fmt`
  (filled from the links themselves, never in the repo), triggers on `call_rec` / `sus_calls` shorten each link only when `_rec_url()`
  rebuilds the same text, and `all_list` / `sus_list` rebuild links only for the rows a page shows (migration `20261127_short_recording_links.sql`).
  Suspicious Calls Excel download (Amit, 27 Nov 2026): button at the right of the list toolbar; RPC `sus_export` (same filters and order
  as `sus_list`, 10,000 rows a call, with team, vertical, dialler call ID and the full recording link, clickable); the Excel writer
  (SheetJS from cdn.sheetjs.com, jsdelivr as second host) loads only when the button is pressed. Second sheet lists the filters.
  Glossary page (`#glossary`, Amit, 27 Nov 2026): the counting and flag rules in words; keep it in step with `flagOf()` and
  `etl/suspicious.py` when a rule changes. Phone numbers in both call lists have a copy button.

## Rules
- Update button (Amit, 25 Nov 2026): only Calling admins (`portal.can_admin('calling')`; button shown via `ca_can_update`); `request_refresh` refuses while a refresh is running ('already_running') and within an hour
  of the last successful one ('recent'), so refreshes never pile up; pg_cron and the daily reports call `_dispatch_refresh` directly.
- Secrets only in GitHub secrets: SUPABASE_SERVICE_KEY, BQ_CREDENTIALS, ACEFONE_API_KEY, ACEFONE_ACCOUNT_ID, MCUBE_TOKEN,
  OZONETEL_API_KEY, OZONETEL_USERNAME, TEAM_SHEET_URL. Never print them.
- The database holds customer phone numbers, names and emails (needed for the downloads, approved by Amit): never dump them into chat.
- Token economy: inspect a few rows, aggregate in SQL.
- Run `python -m pytest -q` before every push; add a test with each rule change.
