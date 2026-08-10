# Power BI Desktop — KLIMA Connection Guide

Local dashboard only (Power BI Free). **Publish to web** needs Pro/PPU — out of zero-cost scope.

## 1. Set read-only password (once)

In Supabase SQL Editor (project **KLIMA** / `zzbjcfluxnulwofqkqht`):

```sql
ALTER ROLE klima_readonly WITH PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
```

## 2. Connect from Power BI Desktop

1. **Get data** → **PostgreSQL database**
2. Server (Supavisor **session** mode, IPv4-friendly):

   ```text
   aws-0-ap-southeast-1.pooler.supabase.com
   ```

   Port: `5432`  
   Database: `postgres`

3. Data Connectivity Mode: **Import** (recommended for free desktop responsiveness)
4. Credentials:
   - Username: `klima_readonly.zzbjcfluxnulwofqkqht`  
     (pooler format: `rolename.projectref`)
   - Password: the password set above
5. Advanced options → **Native SQL** optional, or navigate schema `klima`
6. Select these objects:
   - `klima.vw_powerbi_latest`
   - `klima.vw_powerbi_hourly`
   - `klima.vw_powerbi_daily`
   - `klima.dim_station` (optional)
   - `klima.dim_parameter` (optional)

Direct host `db.zzbjcfluxnulwofqkqht.supabase.co` is IPv6 by default on Free; prefer pooler.

## 3. Relationships

If loading base tables instead of views:

- `dim_station[site_id]` 1→* `fact_latest[site_id]`
- `dim_parameter[parameter_code]` 1→* `fact_latest[parameter]`

Views already denormalize station + parameter labels.

## 4. Manual refresh

On Free Desktop: **Home → Refresh**. No scheduled cloud refresh without Service + Pro.

## 5. Data caveats

- Source: near-real-time PAGASA PANaHON AWS; gaps/biases possible
- Temperature / heat-index / humidity `0` → stored as NULL (sensor fault rule)
- Rainfall `0` is valid (no rain)
- Wind direction stored as degrees parsed from strings like `WNW (303.1°)`
