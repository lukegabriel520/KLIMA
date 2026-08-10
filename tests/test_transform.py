"""Unit tests for KLIMA transform / validation logic (no live network)."""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from etl_pipeline import (
    apply_fault_rule,
    canonicalize_parameter,
    parse_numeric_value,
    parse_parameters,
    sanitize_unit,
    transform_records,
    validate_raw_items,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["data"]


def test_parse_parameters_default():
    assert "temperature" in parse_parameters("")
    assert parse_parameters("rainfall,temperature") == ["rainfall", "temperature"]


def test_temperature_zero_becomes_null():
    records = validate_raw_items(load_fixture("temperature_sample.json"))
    _, fact = transform_records(records)
    by_site = {row["site_id"]: row["value"] for row in fact.iter_rows(named=True)}
    assert by_site[98] == 26.12
    assert by_site[5001] is None
    assert by_site[5003] is None


def test_rainfall_zero_preserved():
    records = validate_raw_items(load_fixture("rainfall_sample.json"))
    _, fact = transform_records(records)
    zeros = fact.filter((pl.col("site_id") == 98) & (pl.col("value") == 0.0))
    assert zeros.height == 1


def test_wind_direction_parses_degrees():
    assert parse_numeric_value("WNW (303.1°)", "wind-direction") == 303.1
    assert parse_numeric_value("N (0°)", "wind-direction") == 0.0
    assert apply_fault_rule("wind-direction", 0.0) == 0.0

    records = validate_raw_items(load_fixture("wind_direction_sample.json"))
    _, fact = transform_records(records)
    values = {row["site_id"]: row["value"] for row in fact.iter_rows(named=True)}
    assert values[98] == 303.1
    assert values[5003] == 0.0


def test_dedupe_keeps_last():
    raw = load_fixture("temperature_sample.json")
    # Duplicate site/time/parameter with different value
    dup = dict(raw[0])
    dup["value"] = "99.9"
    records = validate_raw_items(raw + [dup])
    _, fact = transform_records(records)
    matched = fact.filter(
        (pl.col("site_id") == 98)
        & (pl.col("parameter") == "temperature")
    )
    assert matched.height == 1
    assert matched["value"][0] == 99.9


def test_observed_at_converted_to_utc():
    records = validate_raw_items(load_fixture("rainfall_sample.json"))
    _, fact = transform_records(records)
    # 21:20 Asia/Manila = 13:20 UTC
    obs = fact.filter(pl.col("site_id") == 98)["observed_at"][0]
    assert str(obs).startswith("2026-08-10 13:20:00")


def test_site_name_stripped():
    records = validate_raw_items(load_fixture("temperature_sample.json"))
    dim, _ = transform_records(records)
    name = dim.filter(pl.col("site_id") == 5003)["site_name"][0]
    assert name == "Romblon Synop. Station"


def test_parameter_aliases_and_units():
    assert canonicalize_parameter("currentPres") == "pressure"
    assert canonicalize_parameter("currentTemp") == "temperature"
    assert sanitize_unit("mslp", "pressure") == "hPa"
    assert sanitize_unit("\ufffdC", "temperature") == "°C"

    raw = load_fixture("temperature_sample.json")
    for row in raw:
        row["parameter"] = "currentTemp"
    records = validate_raw_items(raw)
    _, fact = transform_records(records, forced_parameter="temperature")
    assert set(fact["parameter"].to_list()) == {"temperature"}
