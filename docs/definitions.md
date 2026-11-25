# Definitions (fixed rules)

Same numbers as the Streamlit calling dashboard (`final_dashboard.py`, Dynamic Dashboard). Change only with Amit's OK, together with
`etl/rules.py` and a test. `tests/test_calling.py` runs the original Streamlit code (`tests/streamlit_reference.py`) against ours.

| Term | Rule |
|---|---|
| Sources | Acefone, MCube, Ozonetel (dialler APIs, read incrementally), Manual calls sheet, LSQ stage-change sheet. Team sheet maps names |
| Acefone calls kept | Outbound, or inbound answered; answered 0-second calls dropped. Time stored = call end |
| MCube calls kept | Not CANCEL, not EXECUTIVE BUSY. Answered = dialstatus ANSWER. Time stored = call start; duration = end − start |
| Ozonetel calls kept | Agents in the team sheet only, phone present, no missed inbound. "unanswered" = missed, "manual" = outbound. Time = call start |
| Manual calls | Manual calls sheet: date = form Date, else Timestamp; duration minutes × 60; counted as answered outbound; reason = Approved By |
| Caller | Dialler name matched to the team sheet's Caller Name (case and outer spaces ignored); unmatched names stay as written, team "Others" |
| Calls > 3 mins | Duration ≥ 180 s. 15–20 mins = 900–1199 s. 20+ = ≥ 1200 s. Call duration > 3 mins = sum of those calls' durations |
| Pick up ratio | Answered ÷ total calls × 100, rounded (halves to even, as Python) |
| Breaks | Office 10:00–20:00 IST. Gaps of 5 minutes or more (times floored to the minute): office start → first call, between calls, last call → 20:00 (not today) |
| Productive hours | 10 h per active day − breaks. Today: the time from the last call to 20:00 also counts as break (Streamlit rule), as of the last refresh |
| Remarks | Late Check-In (first call after 10:15), Early Check-Out (last call before 20:00, not today), Low Calls (< 40 calls > 3 min), Low Duration (< 3h 15m), Excessive Breaks (> 2 breaks), Less Productive (< 5 h) — per day |
| Stage changes | Stage_Changed_By mapped to Caller Name; Discovery / Roadmap / Follow Up = New_Contact_Stage contains the word (any case). Callers with stage changes but no calls are listed with 0 calls |
| Unique leads | Unique numbers dialled each day (calls + manual) by callers in the current team sheet, added up over the days (Amit, 26–27 Oct 2026) |
| Cards and highlights | Only callers with a team in the current team sheet (Amit, 27 Oct 2026). Tables still list everyone; others show under team "Others" |
| Agent performance table | Callers with 0 calls or 0 duration > 3 mins are not listed (also not in its download) |
| Downloads | CDR_LOG.csv and Stage_Changed_Report.csv: same columns, order and text as Streamlit's files |
| Storage | Individual dialler calls: last 60 days (Amit, 25 Nov 2026). Manual calls and stage changes: last 90 days. Per caller per day summaries: kept forever (only days that still hold calls are rebuilt) |
| Refresh | 06:30 IST (full rebuild of the 90 days), 11:00, 13:00, 15:00, 17:00, 19:00, 21:00 IST via Supabase pg_cron, plus the Update button |
