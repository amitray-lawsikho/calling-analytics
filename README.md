# Calling Analytics Dashboard

Calling metrics for LawSikho / Skill Arbitrage: per-caller calls, talk time, breaks, productive hours, remarks and LSQ stage changes,
with the CDR and stage-change downloads. See [CLAUDE.md](CLAUDE.md) for how it runs and [docs/definitions.md](docs/definitions.md) for the rules.

- Site: `web/` (Cloudflare, deploys on push to `main`).
- Data: `python -m etl.refresh` (GitHub Actions, started by Supabase on a schedule or by the Update button).
- Tests: `python -m pytest -q`.
