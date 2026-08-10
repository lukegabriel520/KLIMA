# KLIMA Dashboard Layout Spec

Import theme: `klima-theme.json` via **View → Themes → Browse for themes**.

## Canvas (16:9)

### Top KPI bar (4 cards)

| Card | Measure / Field | Notes |
|------|-----------------|-------|
| National Avg Temp | `[Latest Temperature]` | Format 0.0 °C |
| Active Stations | `[Active Stations]` | Temperature Active status |
| Offline / Fault | `[Offline Stations]` | Includes Stale + NULL |
| Hottest Location | `[Hottest Location]` | Temperature only |

Add subtitle text box: `Max Observation (Manila):` + `[Max Observation Time Manila]`

### Main left — Map

- Visual: **Azure Map** or **Map**
- Latitude: `vw_powerbi_latest[latitude]`
- Longitude: `vw_powerbi_latest[longitude]`
- Legend / color: `station_status` or temperature `value`
- Filter pane default: `parameter = temperature`
- Tooltip: `site_name`, `value`, `observed_at_manila`, `station_status`

### Main right top — Trend

- Visual: **Line chart**
- X: `vw_powerbi_hourly[hour_start_manila]`
- Y: `avg_value`
- Legend: optional `parameter`
- Sync slicer: `site_name` (from latest or dim)

### Main right bottom — Table

Columns from `vw_powerbi_latest` (filter `parameter = temperature` or leave open with parameter slicer):

- `site_name`
- `observed_at_manila`
- `value`
- `unit`
- `station_status`

### Slicers

- `parameter` (multi or single)
- `site_name` (search enabled)
- `station_status`

## Field mapping cheat sheet

| Need | Source |
|------|--------|
| Station metadata | `vw_powerbi_latest` or `dim_station` |
| Current values | `vw_powerbi_latest` |
| Hourly history | `vw_powerbi_hourly` |
| Daily history | `vw_powerbi_daily` |

## Attribution footer

> Data source: PAGASA PANaHON Automatic Weather Stations (near real-time). Gaps, biases, and sensor faults may exist. KLIMA is an unofficial analytics pipeline.
