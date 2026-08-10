#!/usr/bin/env python3
"""KLIMA ETL: extract via api.sh, validate, transform (Polars), upsert to Supabase Postgres."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional

import polars as pl
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("klima.etl")

ROOT = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Load local .env if present (never overrides existing env vars)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


_load_dotenv()

DEFAULT_PARAMETERS = (
    "rainfall",
    "temperature",
    "heat-index",
    "humidity",
    "pressure",
    "wind-speed",
    "wind-direction",
)

# Tropical PH: 0 °C / 0% RH / empty MSLP are sensor faults, not real weather.
FAULT_ZERO_PARAMETERS = frozenset({"temperature", "heat-index", "humidity", "currentTemp", "heat_index"})
# Absolute pressure near 0 hPa is impossible at PH stations.
FAULT_ZERO_PRESSURE = frozenset({"pressure"})

WIND_DEG_RE = re.compile(r"\(([+-]?\d+(?:\.\d+)?)")

# Map API aliases → canonical parameter codes used in dim_parameter / KLIMA_PARAMETERS
PARAMETER_ALIASES = {
    "accumulated_rain_1h": "rainfall",
    "rainfall": "rainfall",
    "currentTemp": "temperature",
    "temperature": "temperature",
    "heat_index": "heat-index",
    "heat-index": "heat-index",
    "humidity": "humidity",
    "currentPres": "pressure",
    "pressure": "pressure",
    "wind_speed": "wind-speed",
    "wind-speed": "wind-speed",
    "wind_direction": "wind-direction",
    "wind-direction": "wind-direction",
}

CANONICAL_UNITS = {
    "rainfall": "mm",
    "temperature": "°C",
    "heat-index": "°C",
    "humidity": "%",
    "pressure": "hPa",
    "wind-speed": "m/s",
    "wind-direction": "°",
}


def canonicalize_parameter(raw: str) -> str:
    return PARAMETER_ALIASES.get(raw, raw)


def sanitize_unit(unit: str, parameter: str) -> str:
    cleaned = (unit or "").replace("\ufffd", "°").strip()
    if not cleaned or cleaned.lower() in {"mslp"}:
        return CANONICAL_UNITS.get(parameter, cleaned)
    if "C" in cleaned and "°" not in cleaned:
        return "°C"
    return cleaned


class StationRaw(BaseModel):
    site_id: str | int
    site_name: str
    lat: float
    lon: float
    parameter: str
    readable_parameter: Optional[str] = None
    readable_unit: str = Field(default="")
    observed_at: str
    value: Any
    hr_value_24: Optional[Any] = Field(default=None, alias="24_hr_value")

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @field_validator("site_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


def resolve_bash() -> str:
    env_bash = os.getenv("KLIMA_BASH")
    if env_bash and Path(env_bash).exists():
        return env_bash
    for candidate in (
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("bash not found; install Git Bash or set KLIMA_BASH")


def resolve_api_sh() -> Path:
    configured = os.getenv("API_SH_PATH", "api.sh")
    path = Path(configured)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"api.sh not found at {path}")
    return path


def parse_parameters(raw: Optional[str] = None) -> list[str]:
    text_value = raw if raw is not None else os.getenv("KLIMA_PARAMETERS", "")
    if text_value.strip():
        return [p.strip() for p in text_value.split(",") if p.strip()]
    return list(DEFAULT_PARAMETERS)


def parse_numeric_value(raw: Any, parameter: str) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        return float(raw)
    s = str(raw).strip()
    if not s or s.upper() in {"NA", "N/A", "NULL", "-"}:
        return None
    if parameter in {"wind-direction", "wind_direction"}:
        match = WIND_DEG_RE.search(s)
        if match:
            return float(match.group(1))
        try:
            return float(s)
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def apply_fault_rule(parameter: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if parameter in FAULT_ZERO_PARAMETERS and value == 0.0:
        return None
    if parameter in FAULT_ZERO_PRESSURE and value <= 0.0:
        return None
    return value


def extract_parameter(parameter: str, timeout: int = 90) -> list[dict[str, Any]]:
    bash = resolve_bash()
    api_sh = resolve_api_sh()
    cmd = [bash, str(api_sh), parameter]
    logger.info("Extracting parameter=%s via %s", parameter, api_sh.name)
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=timeout,
        check=False,
    )
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        logger.error("api.sh failed for %s (rc=%s): %s", parameter, proc.returncode, proc.stderr.strip())
        raise RuntimeError(f"Extraction failed for parameter={parameter}")
    if not proc.stdout.strip():
        raise RuntimeError(f"Empty stdout for parameter={parameter}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON stdout for parameter={parameter}") from exc
    if not payload.get("success") or "data" not in payload:
        raise RuntimeError(f"Invalid API envelope for parameter={parameter}")
    items = payload["data"]
    if not isinstance(items, list):
        raise RuntimeError(f"API data not list for parameter={parameter}")
    logger.info("Extracted %s rows for %s in %.1fs", len(items), parameter, elapsed)
    return items


def validate_raw_items(items: Iterable[dict[str, Any]]) -> list[StationRaw]:
    validated: list[StationRaw] = []
    errors = 0
    for item in items:
        try:
            validated.append(StationRaw.model_validate(item))
        except Exception as exc:  # noqa: BLE001 — collect bad rows, fail if too many
            errors += 1
            logger.warning("Skip invalid row: %s (%s)", item, exc)
    if not validated:
        raise RuntimeError("No valid rows after Pydantic validation")
    if errors:
        logger.warning("Dropped %s invalid rows", errors)
    return validated


def transform_records(
    records: list[StationRaw],
    ingested_at: Optional[datetime] = None,
    forced_parameter: Optional[str] = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    ingested_at = ingested_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for rec in records:
        parameter = canonicalize_parameter(forced_parameter or rec.parameter)
        raw_value = parse_numeric_value(rec.value, parameter)
        value = apply_fault_rule(parameter, raw_value)
        # Treat observed_at as Asia/Manila wall time (API has no TZ), convert to UTC.
        observed_local = datetime.strptime(rec.observed_at, "%Y-%m-%d %H:%M:%S")
        rows.append(
            {
                "site_id": int(rec.site_id),
                "site_name": rec.site_name,
                "latitude": float(rec.lat),
                "longitude": float(rec.lon),
                "parameter": parameter,
                "unit": sanitize_unit(rec.readable_unit, parameter),
                "observed_at_local": observed_local.isoformat(sep=" "),
                "value": value,
                "ingested_at": ingested_at,
            }
        )

    df = pl.DataFrame(rows)
    df = df.with_columns(
        pl.col("observed_at_local")
        .str.to_datetime("%Y-%m-%d %H:%M:%S")
        .dt.replace_time_zone("Asia/Manila")
        .dt.convert_time_zone("UTC")
        .alias("observed_at")
    )

    dim_stations = (
        df.select(["site_id", "site_name", "latitude", "longitude"])
        .unique(subset=["site_id"], keep="last")
        .sort("site_id")
    )
    fact_telemetry = (
        df.select(["site_id", "observed_at", "parameter", "value", "unit", "ingested_at"])
        .unique(subset=["site_id", "observed_at", "parameter"], keep="last")
        .sort(["site_id", "observed_at", "parameter"])
    )
    return dim_stations, fact_telemetry


def get_engine(db_url: Optional[str] = None) -> Engine:
    url = db_url or os.getenv("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError("Missing SUPABASE_DB_URL")
    # Prefer SSL for pooler / direct connections when not already specified.
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)


def upsert_batch(engine: Engine, dim_stations: pl.DataFrame, fact_telemetry: pl.DataFrame) -> dict[str, int]:
    dim_rows = list(dim_stations.iter_rows(named=True))
    fact_rows = list(fact_telemetry.iter_rows(named=True))
    # Convert polars datetime / None for psycopg2
    for row in fact_rows:
        if row.get("value") is not None:
            row["value"] = float(row["value"])
        obs = row["observed_at"]
        if hasattr(obs, "to_pydatetime"):
            row["observed_at"] = obs.to_pydatetime()
        ing = row["ingested_at"]
        if hasattr(ing, "to_pydatetime"):
            row["ingested_at"] = ing.to_pydatetime()

    with engine.begin() as conn:
        run_id = conn.execute(
            text(
                """
                INSERT INTO klima.etl_run (started_at, status, notes)
                VALUES (NOW(), 'running', :notes)
                RETURNING run_id
                """
            ),
            {"notes": f"rows={len(fact_rows)} stations={len(dim_rows)}"},
        ).scalar_one()

        if dim_rows:
            conn.execute(
                text(
                    """
                    INSERT INTO klima.dim_station (site_id, site_name, latitude, longitude, updated_at)
                    VALUES (:site_id, :site_name, :latitude, :longitude, NOW())
                    ON CONFLICT (site_id) DO UPDATE SET
                        site_name = EXCLUDED.site_name,
                        latitude = EXCLUDED.latitude,
                        longitude = EXCLUDED.longitude,
                        updated_at = NOW()
                    """
                ),
                dim_rows,
            )

        inserted = 0
        if fact_rows:
            result = conn.execute(
                text(
                    """
                    INSERT INTO klima.fact_telemetry
                        (site_id, observed_at, parameter, value, unit, ingested_at)
                    VALUES
                        (:site_id, :observed_at, :parameter, :value, :unit, :ingested_at)
                    ON CONFLICT (site_id, observed_at, parameter) DO NOTHING
                    """
                ),
                fact_rows,
            )
            inserted = result.rowcount if result.rowcount is not None else 0

        # Refresh latest snapshot for loaded keys
        conn.execute(
            text(
                """
                INSERT INTO klima.fact_latest AS l
                    (site_id, parameter, observed_at, value, unit, ingested_at)
                SELECT DISTINCT ON (site_id, parameter)
                    site_id, parameter, observed_at, value, unit, ingested_at
                FROM klima.fact_telemetry
                WHERE ingested_at >= NOW() - INTERVAL '2 hours'
                ORDER BY site_id, parameter, observed_at DESC
                ON CONFLICT (site_id, parameter) DO UPDATE SET
                    observed_at = EXCLUDED.observed_at,
                    value = EXCLUDED.value,
                    unit = EXCLUDED.unit,
                    ingested_at = EXCLUDED.ingested_at
                WHERE l.observed_at IS DISTINCT FROM EXCLUDED.observed_at
                   OR l.value IS DISTINCT FROM EXCLUDED.value
                """
            )
        )

        # Hourly aggregates (UTC hour buckets)
        conn.execute(
            text(
                """
                INSERT INTO klima.agg_hourly
                    (site_id, parameter, hour_start, avg_value, min_value, max_value, sample_count)
                SELECT
                    site_id,
                    parameter,
                    date_trunc('hour', observed_at) AS hour_start,
                    AVG(value),
                    MIN(value),
                    MAX(value),
                    COUNT(*) FILTER (WHERE value IS NOT NULL)
                FROM klima.fact_telemetry
                WHERE observed_at >= NOW() - INTERVAL '48 hours'
                GROUP BY 1, 2, 3
                ON CONFLICT (site_id, parameter, hour_start) DO UPDATE SET
                    avg_value = EXCLUDED.avg_value,
                    min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    sample_count = EXCLUDED.sample_count
                """
            )
        )

        # Daily aggregates (Asia/Manila calendar day, stored as date)
        conn.execute(
            text(
                """
                INSERT INTO klima.agg_daily
                    (site_id, parameter, day_local, avg_value, min_value, max_value, sample_count)
                SELECT
                    site_id,
                    parameter,
                    ((observed_at AT TIME ZONE 'Asia/Manila')::date) AS day_local,
                    AVG(value),
                    MIN(value),
                    MAX(value),
                    COUNT(*) FILTER (WHERE value IS NOT NULL)
                FROM klima.fact_telemetry
                WHERE observed_at >= NOW() - INTERVAL '40 days'
                GROUP BY 1, 2, 3
                ON CONFLICT (site_id, parameter, day_local) DO UPDATE SET
                    avg_value = EXCLUDED.avg_value,
                    min_value = EXCLUDED.min_value,
                    max_value = EXCLUDED.max_value,
                    sample_count = EXCLUDED.sample_count
                """
            )
        )

        # Retention maintenance
        deleted_raw = conn.execute(
            text("DELETE FROM klima.fact_telemetry WHERE observed_at < NOW() - INTERVAL '48 hours'")
        ).rowcount
        deleted_hourly = conn.execute(
            text("DELETE FROM klima.agg_hourly WHERE hour_start < NOW() - INTERVAL '30 days'")
        ).rowcount
        deleted_daily = conn.execute(
            text("DELETE FROM klima.agg_daily WHERE day_local < (CURRENT_DATE - INTERVAL '365 days')")
        ).rowcount

        conn.execute(
            text(
                """
                UPDATE klima.etl_run
                SET finished_at = NOW(),
                    status = 'success',
                    rows_extracted = :extracted,
                    rows_inserted = :inserted,
                    stations_upserted = :stations,
                    notes = :notes
                WHERE run_id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "extracted": len(fact_rows),
                "inserted": max(inserted, 0),
                "stations": len(dim_rows),
                "notes": (
                    f"deleted_raw={deleted_raw} deleted_hourly={deleted_hourly} "
                    f"deleted_daily={deleted_daily}"
                ),
            },
        )

    return {
        "stations": len(dim_rows),
        "extracted": len(fact_rows),
        "inserted": max(inserted, 0),
    }


def health_check(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    pg_size_pretty(pg_total_relation_size('klima.fact_telemetry')) AS fact_size,
                    (SELECT COUNT(*) FROM klima.fact_telemetry) AS fact_rows,
                    (SELECT COUNT(*) FROM klima.dim_station) AS stations,
                    pg_size_pretty(pg_database_size(current_database())) AS db_size
                """
            )
        ).mappings().one()
        logger.info(
            "Health: db=%s fact=%s (%s rows) stations=%s",
            row["db_size"],
            row["fact_size"],
            row["fact_rows"],
            row["stations"],
        )
        # Soft warning near free-tier 500 MB
        size_bytes = conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        if size_bytes > 400 * 1024 * 1024:
            logger.warning("Database size approaching Free plan 500 MB limit: %s bytes", size_bytes)


def run_etl(dry_run: bool = False) -> int:
    parameters = parse_parameters()
    logger.info("Starting ETL for parameters=%s dry_run=%s", parameters, dry_run)

    all_records: list[tuple[str, StationRaw]] = []
    failures: list[str] = []

    for parameter in parameters:
        try:
            raw_items = extract_parameter(parameter)
            validated = validate_raw_items(raw_items)
            all_records.extend((parameter, rec) for rec in validated)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Parameter failed: %s", parameter)
            failures.append(f"{parameter}: {exc}")

    if failures:
        logger.error("Aborting — parameter failures: %s", "; ".join(failures))
        return 1

    # Transform per requested parameter so API aliases map to canonical codes
    dim_frames: list[pl.DataFrame] = []
    fact_frames: list[pl.DataFrame] = []
    ingested_at = datetime.now(timezone.utc)
    by_param: dict[str, list[StationRaw]] = {}
    for requested, rec in all_records:
        by_param.setdefault(requested, []).append(rec)
    for requested, recs in by_param.items():
        dim_part, fact_part = transform_records(recs, ingested_at=ingested_at, forced_parameter=requested)
        dim_frames.append(dim_part)
        fact_frames.append(fact_part)

    dim_stations = pl.concat(dim_frames).unique(subset=["site_id"], keep="last").sort("site_id")
    fact_telemetry = (
        pl.concat(fact_frames)
        .unique(subset=["site_id", "observed_at", "parameter"], keep="last")
        .sort(["site_id", "observed_at", "parameter"])
    )
    logger.info(
        "Transformed stations=%s facts=%s null_values=%s",
        dim_stations.height,
        fact_telemetry.height,
        fact_telemetry.filter(pl.col("value").is_null()).height,
    )

    if dry_run:
        logger.info("DRY_RUN=1 — skip database load")
        print(fact_telemetry.head(5))
        return 0

    engine = get_engine()
    try:
        stats = upsert_batch(engine, dim_stations, fact_telemetry)
        health_check(engine)
        logger.info("ETL success: %s", stats)
        return 0
    except Exception:
        logger.exception("Database load failed")
        return 2
    finally:
        engine.dispose()


def main() -> None:
    dry = os.getenv("DRY_RUN", "0").strip() in {"1", "true", "TRUE", "yes"}
    code = run_etl(dry_run=dry)
    sys.exit(code)


if __name__ == "__main__":
    main()
