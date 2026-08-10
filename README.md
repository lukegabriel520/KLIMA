# KLIMA

**Kloud-Linked Integrated Meteorological Analytics**

Zero-cost pipeline: PAGASA PANaHON Automatic Weather Station (AWS) telemetry → Supabase Postgres → Power BI Desktop.

```text
panahon.gov.ph AWS API
        │
        ▼  api.sh (CSRF + cookie extract) every ~15 min
GitHub Actions (public repo runners)
        │
        ▼  etl_pipeline.py (Pydantic + Polars + SQLAlchemy)
Supabase Postgres schema `klima`
        │
        ▼  Import mode
Power BI Desktop (local dashboard)
```

## What you get

| Layer | Object |
|-------|--------|
| Extract | [`api.sh`](api.sh) — only HTTP client |
| Transform/Load | [`etl_pipeline.py`](etl_pipeline.py) |
| Orchestration | [`.github/workflows/ingest_weather.yml`](.github/workflows/ingest_weather.yml) |
| Warehouse | `klima.dim_station`, `klima.fact_telemetry`, `klima.fact_latest`, `klima.agg_hourly`, `klima.agg_daily` |
| BI views | `klima.vw_powerbi_latest`, `klima.vw_powerbi_hourly`, `klima.vw_powerbi_daily` |
| Power BI pack | [`powerbi/`](powerbi/) |

Parameters ingested each run:

`rainfall`, `temperature`, `heat-index`, `humidity`, `pressure`, `wind-speed`, `wind-direction`

## Free-tier reality check

| Service | Constraint | KLIMA mitigation |
|---------|------------|------------------|
| Supabase Free | 500 MB DB; pause after 7 days idle | 48h raw / 30d hourly / 1y daily retention; Actions keep project warm |
| Supabase Free | No PITR backups | Accept risk; schema in git migrations |
| GitHub Free private | 2,000 Actions minutes/mo; schedules unreliable | **Public repo** → standard runners unlimited |
| GitHub schedule | May delay; disables after 60 days no repo activity | Off-peak cron + occasional commits / `workflow_dispatch` |
| Power BI Free | No Publish to web code creation | Local Desktop dashboard only |

This is production-*minded*, not SLA-backed.

## Quick start (local)

```bash
# Windows: use Git Bash
python -m venv .venv
source .venv/Scripts/activate   # or .venv\Scripts\activate
pip install -r requirements.txt

# Dry run (no DB)
DRY_RUN=1 python etl_pipeline.py

# Live load — copy .env.example → .env and set SUPABASE_DB_URL
# Prefer Supavisor transaction mode (6543) for GitHub/Actions:
# postgresql://postgres.zzbjcfluxnulwofqkqht:[PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?sslmode=require
export SUPABASE_DB_URL='...'
python etl_pipeline.py
```

Tests:

```bash
pytest tests/ -q
```

## Supabase setup

Project: **KLIMA** (`zzbjcfluxnulwofqkqht`, `ap-southeast-1`)

1. Migration already applied: [`supabase/migrations/20260810150000_klima_star_schema.sql`](supabase/migrations/20260810150000_klima_star_schema.sql)
2. Set Power BI role password:

```sql
ALTER ROLE klima_readonly WITH PASSWORD 'STRONG_PASSWORD';
```

3. Health check:

```sql
SELECT * FROM klima.vw_health;
```

## GitHub Actions secrets

Repo Settings → Secrets and variables → Actions:

| Secret | Value |
|--------|-------|
| `SUPABASE_DB_URL` | Transaction-mode pooler URL with DB password + `sslmode=require` |

Do **not** store `PAGASA_API_URL` — extractor builds URL inside `api.sh`.

Manual run: Actions → **AWS Weather Data Pipeline** → **Run workflow**.

## Power BI Desktop

See [`powerbi/CONNECTION.md`](powerbi/CONNECTION.md), [`powerbi/measures.dax`](powerbi/measures.dax), [`powerbi/DASHBOARD_LAYOUT.md`](powerbi/DASHBOARD_LAYOUT.md).

Theme: [`powerbi/klima-theme.json`](powerbi/klima-theme.json).

## Transform rules

- API wall clock treated as **Asia/Manila**, stored as **UTC**
- Temperature / heat-index / humidity `0` → `NULL` (sensor fault)
- Rainfall `0` kept (valid dry reading)
- Wind direction text `WNW (303.1°)` → numeric degrees
- Unique key: `(site_id, observed_at, parameter)` — duplicates skipped

## Data attribution & disclaimer

> Near-real-time values originate from **PAGASA PANaHON** Automatic Weather Stations ([panahon.gov.ph](https://panahon.gov.ph/)). Official hydromet archives may require formal request and have redistribution restrictions. KLIMA uses the public map API for personal/educational analytics; gaps, biases, and sensor faults can appear. Not an official PAGASA product. Do not treat as operational forecast guidance.

## Recovery

| Problem | Action |
|---------|--------|
| Free project paused | Restore in Supabase dashboard; re-run workflow |
| Schedule stopped (60d idle) | Push commit or re-enable workflow |
| DB near 500 MB | Confirm retention deletes; shorten raw window in ETL |
| API shape change | Fix `api.sh` / Pydantic models; tests in `tests/fixtures` |
| Failed parameter | Whole run aborts (no partial commit) |

## License / ethics

Code in this repo: use freely for learning. Respect PAGASA terms for redistribution of raw hydrometeorological data.
