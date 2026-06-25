# Global Index Data Loader

The global index loader stores non-NIFTY market context for macro/risk-signal
research. It is DB-first for Render cron jobs, with local CSV output kept as a
best-effort developer artifact.

## Files

| File | Role |
|------|------|
| `src/data_manager/global_index_loader.py` | Fetches and normalizes OHLC rows from Yahoo Finance. |
| `scripts/Common/load_daily_index_data.py` | CLI/cron entrypoint that writes Supabase first and local CSV output when enabled. |
| `src/data_manager/db/migrations/007_create_global_index_ohlc.sql` | Schema migration for `GlobalIndexOhlc`. |
| `src/data_manager/db/supabase_client.py` | Runtime upsert and table-creation support. |

## Index Coverage

The default universe covers:

- NIFTY50, Sensex, India VIX
- S&P 500, NASDAQ Composite, Dow Jones, Russell 2000
- FTSE 100, DAX, CAC 40
- Hang Seng, Nikkei 225, Shanghai Composite, KOSPI, ASX 200

## Stored Data Shape

Rows are stored in Supabase table `GlobalIndexOhlc` with one row per index per
trade date.

Key columns:

- `index_code`
- `index_name`
- `yahoo_symbol`
- `region`
- `currency`
- `trade_date`
- `open_price`
- `high_price`
- `low_price`
- `close_price`
- `adj_close`
- `volume`
- `source`
- `fetched_at`

The primary key is `(index_code, trade_date, source)`.

## Local Output

When local output is enabled, the loader writes a partitioned CSV:

```text
output/intelligence/global_index_ohlc/DD-MM-YYYY/global_index_ohlc.csv
```

Local output is optional. Render runs should use `--no-local-output` unless a
local artifact is explicitly needed.

## Commands

Run the default recent refresh:

```powershell
python scripts/Common/load_daily_index_data.py
```

Run an explicit backfill window:

```powershell
python scripts/Common/load_daily_index_data.py --start 2025-01-01 --end 2026-06-25
```

Run from Render without local CSV output:

```bash
python scripts/Common/load_daily_index_data.py --no-local-output
```

## Recommended Cron Schedule (IST)

Run multiple times per day so each region has time to settle:

| Time (IST) | Markets mostly ready |
|------------|----------------------|
| 4:30 PM | India, Japan, China, Hong Kong |
| 10:30 PM | UK / Europe |
| 3:00 AM | US |

On Render, create separate cron jobs for those times if you want the freshest
regional data. For a simpler setup, one late-night/early-morning run is enough
for research and daily prediction features that only need completed prior-day
global market data.

## Validation Queries

Overall coverage:

```sql
SELECT
    count(*) AS row_count,
    min(trade_date) AS min_trade_date,
    max(trade_date) AS max_trade_date,
    count(DISTINCT index_code) AS index_count
FROM "GlobalIndexOhlc";
```

Coverage by index:

```sql
SELECT
    index_code,
    count(*) AS row_count,
    min(trade_date) AS min_trade_date,
    max(trade_date) AS max_trade_date
FROM "GlobalIndexOhlc"
GROUP BY index_code
ORDER BY index_code;
```

Latest row counts by trade date:

```sql
SELECT
    trade_date,
    count(*) AS row_count
FROM "GlobalIndexOhlc"
GROUP BY trade_date
ORDER BY trade_date DESC
LIMIT 10;
```
