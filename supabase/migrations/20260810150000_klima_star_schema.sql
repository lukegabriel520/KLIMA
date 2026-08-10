-- KLIMA star schema + retention-friendly analytics objects
-- Schema: klima (private; not exposed via PostgREST Data API by default)

CREATE SCHEMA IF NOT EXISTS klima;

-- Dimension: stations
CREATE TABLE IF NOT EXISTS klima.dim_station (
    site_id     INTEGER PRIMARY KEY,
    site_name   VARCHAR(255) NOT NULL,
    latitude    DOUBLE PRECISION NOT NULL,
    longitude   DOUBLE PRECISION NOT NULL,
    region      VARCHAR(100) NOT NULL DEFAULT 'Unknown',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_dim_station_lat CHECK (latitude BETWEEN -90 AND 90),
    CONSTRAINT ck_dim_station_lon CHECK (longitude BETWEEN -180 AND 180)
);

CREATE INDEX IF NOT EXISTS idx_dim_station_coords
    ON klima.dim_station (latitude, longitude);

-- Dimension: parameters (seeded)
CREATE TABLE IF NOT EXISTS klima.dim_parameter (
    parameter_code  VARCHAR(50) PRIMARY KEY,
    display_name    VARCHAR(100) NOT NULL,
    default_unit    VARCHAR(20) NOT NULL,
    zero_is_fault   BOOLEAN NOT NULL DEFAULT FALSE
);

INSERT INTO klima.dim_parameter (parameter_code, display_name, default_unit, zero_is_fault)
VALUES
    ('rainfall',       'Hourly Rainfall', 'mm',  FALSE),
    ('temperature',    'Temperature',     '°C',  TRUE),
    ('heat-index',     'Heat Index',      '°C',  TRUE),
    ('humidity',       'Humidity',        '%',   TRUE),
    ('pressure',       'Pressure (MSLP)', 'hPa', TRUE),
    ('wind-speed',     'Wind Speed',      'm/s', FALSE),
    ('wind-direction', 'Wind Direction',  '°',   FALSE)
ON CONFLICT (parameter_code) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    default_unit = EXCLUDED.default_unit,
    zero_is_fault = EXCLUDED.zero_is_fault;

-- Fact: raw telemetry (48h retention enforced by ETL)
CREATE TABLE IF NOT EXISTS klima.fact_telemetry (
    reading_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    site_id     INTEGER NOT NULL REFERENCES klima.dim_station (site_id) ON DELETE CASCADE,
    observed_at TIMESTAMPTZ NOT NULL,
    parameter   VARCHAR(50) NOT NULL REFERENCES klima.dim_parameter (parameter_code),
    value       NUMERIC(10, 3) NULL,
    unit        VARCHAR(20) NOT NULL DEFAULT '',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fact_telemetry_site_obs_param UNIQUE (site_id, observed_at, parameter)
);

CREATE INDEX IF NOT EXISTS idx_fact_telemetry_observed
    ON klima.fact_telemetry (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fact_telemetry_site_time
    ON klima.fact_telemetry (site_id, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fact_telemetry_param_time
    ON klima.fact_telemetry (parameter, observed_at DESC);

-- Latest snapshot per station + parameter
CREATE TABLE IF NOT EXISTS klima.fact_latest (
    site_id     INTEGER NOT NULL REFERENCES klima.dim_station (site_id) ON DELETE CASCADE,
    parameter   VARCHAR(50) NOT NULL REFERENCES klima.dim_parameter (parameter_code),
    observed_at TIMESTAMPTZ NOT NULL,
    value       NUMERIC(10, 3) NULL,
    unit        VARCHAR(20) NOT NULL DEFAULT '',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (site_id, parameter)
);

CREATE INDEX IF NOT EXISTS idx_fact_latest_observed
    ON klima.fact_latest (observed_at DESC);

-- Hourly aggregates (30d retention)
CREATE TABLE IF NOT EXISTS klima.agg_hourly (
    site_id      INTEGER NOT NULL REFERENCES klima.dim_station (site_id) ON DELETE CASCADE,
    parameter    VARCHAR(50) NOT NULL REFERENCES klima.dim_parameter (parameter_code),
    hour_start   TIMESTAMPTZ NOT NULL,
    avg_value    NUMERIC(10, 3),
    min_value    NUMERIC(10, 3),
    max_value    NUMERIC(10, 3),
    sample_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (site_id, parameter, hour_start)
);

CREATE INDEX IF NOT EXISTS idx_agg_hourly_hour
    ON klima.agg_hourly (hour_start DESC);

-- Daily aggregates (1y retention); day_local = Asia/Manila calendar date
CREATE TABLE IF NOT EXISTS klima.agg_daily (
    site_id      INTEGER NOT NULL REFERENCES klima.dim_station (site_id) ON DELETE CASCADE,
    parameter    VARCHAR(50) NOT NULL REFERENCES klima.dim_parameter (parameter_code),
    day_local    DATE NOT NULL,
    avg_value    NUMERIC(10, 3),
    min_value    NUMERIC(10, 3),
    max_value    NUMERIC(10, 3),
    sample_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (site_id, parameter, day_local)
);

CREATE INDEX IF NOT EXISTS idx_agg_daily_day
    ON klima.agg_daily (day_local DESC);

-- ETL run audit
CREATE TABLE IF NOT EXISTS klima.etl_run (
    run_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at      TIMESTAMPTZ,
    status           VARCHAR(20) NOT NULL DEFAULT 'running',
    rows_extracted   INTEGER,
    rows_inserted    INTEGER,
    stations_upserted INTEGER,
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_etl_run_started
    ON klima.etl_run (started_at DESC);

-- Power BI friendly views (security_invoker where supported)
CREATE OR REPLACE VIEW klima.vw_powerbi_latest
WITH (security_invoker = true) AS
SELECT
    s.site_id,
    s.site_name,
    s.latitude,
    s.longitude,
    s.region,
    p.parameter_code AS parameter,
    p.display_name AS parameter_name,
    l.observed_at,
    (l.observed_at AT TIME ZONE 'Asia/Manila') AS observed_at_manila,
    l.value,
    COALESCE(NULLIF(l.unit, ''), p.default_unit) AS unit,
    CASE
        WHEN l.value IS NULL THEN 'Offline / Fault'
        WHEN l.observed_at < NOW() - INTERVAL '90 minutes' THEN 'Stale'
        ELSE 'Active'
    END AS station_status,
    l.ingested_at
FROM klima.dim_station s
CROSS JOIN klima.dim_parameter p
LEFT JOIN klima.fact_latest l
    ON l.site_id = s.site_id
   AND l.parameter = p.parameter_code;

CREATE OR REPLACE VIEW klima.vw_powerbi_hourly
WITH (security_invoker = true) AS
SELECT
    s.site_id,
    s.site_name,
    s.latitude,
    s.longitude,
    h.parameter,
    p.display_name AS parameter_name,
    h.hour_start,
    (h.hour_start AT TIME ZONE 'Asia/Manila') AS hour_start_manila,
    h.avg_value,
    h.min_value,
    h.max_value,
    h.sample_count,
    COALESCE(p.default_unit, '') AS unit
FROM klima.agg_hourly h
JOIN klima.dim_station s ON s.site_id = h.site_id
JOIN klima.dim_parameter p ON p.parameter_code = h.parameter;

CREATE OR REPLACE VIEW klima.vw_powerbi_daily
WITH (security_invoker = true) AS
SELECT
    s.site_id,
    s.site_name,
    s.latitude,
    s.longitude,
    d.parameter,
    p.display_name AS parameter_name,
    d.day_local,
    d.avg_value,
    d.min_value,
    d.max_value,
    d.sample_count,
    COALESCE(p.default_unit, '') AS unit
FROM klima.agg_daily d
JOIN klima.dim_station s ON s.site_id = d.site_id
JOIN klima.dim_parameter p ON p.parameter_code = d.parameter;

CREATE OR REPLACE VIEW klima.vw_health
WITH (security_invoker = true) AS
SELECT
    (SELECT COUNT(*) FROM klima.dim_station) AS station_count,
    (SELECT COUNT(*) FROM klima.fact_telemetry) AS telemetry_rows,
    (SELECT COUNT(*) FROM klima.fact_latest) AS latest_rows,
    (SELECT MAX(observed_at) FROM klima.fact_telemetry) AS max_observed_at,
    (SELECT MAX(finished_at) FROM klima.etl_run WHERE status = 'success') AS last_success_at,
    pg_size_pretty(pg_database_size(current_database())) AS database_size,
    pg_database_size(current_database()) AS database_bytes;

-- Least privilege: revoke public access; grant read to authenticated/anon only if needed later.
REVOKE ALL ON SCHEMA klima FROM PUBLIC;
GRANT USAGE ON SCHEMA klima TO postgres;

REVOKE ALL ON ALL TABLES IN SCHEMA klima FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA klima TO postgres;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA klima TO postgres;

ALTER DEFAULT PRIVILEGES IN SCHEMA klima
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO postgres;
ALTER DEFAULT PRIVILEGES IN SCHEMA klima
    GRANT USAGE, SELECT ON SEQUENCES TO postgres;

COMMENT ON SCHEMA klima IS 'Kloud-Linked Integrated Meteorological Analytics — PAGASA AWS telemetry';
COMMENT ON TABLE klima.fact_telemetry IS 'Raw AWS readings retained 48 hours';
COMMENT ON TABLE klima.agg_hourly IS 'Hourly rollups retained 30 days';
COMMENT ON TABLE klima.agg_daily IS 'Daily rollups retained 365 days (Asia/Manila day)';

-- Dedicated read-only login for Power BI Desktop (set password in Dashboard SQL Editor)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'klima_readonly') THEN
    CREATE ROLE klima_readonly NOINHERIT LOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA klima TO klima_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA klima TO klima_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA klima TO klima_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA klima GRANT SELECT ON TABLES TO klima_readonly;

COMMENT ON ROLE klima_readonly IS 'Read-only Power BI / analytics role for KLIMA schema';
