# Option DB Diagnostics

## Gist

Use this SQL to clean expired option contracts that have no snapshots, then summarize active/expired contracts by underlying and option type.

```sql
DELETE FROM dbo.OptionInstrument
WHERE expiry < CAST(GETDATE() AS DATE)
  AND id NOT IN (SELECT DISTINCT option_instrument_id FROM dbo.OptionSnapshot);

SELECT
    underlying,
    instrument_type,
    COUNT(*)                                                        AS total_contracts,
    COUNT(CASE WHEN expiry >= CAST(GETDATE() AS DATE) THEN 1 END)  AS active_contracts,
    COUNT(CASE WHEN expiry <  CAST(GETDATE() AS DATE) THEN 1 END)  AS expired_contracts,
    MIN(expiry)                                                     AS earliest_expiry,
    MAX(expiry)                                                     AS latest_expiry,
    MIN(fetch_date)                                                 AS first_loaded
FROM dbo.OptionInstrument
GROUP BY underlying, instrument_type
ORDER BY underlying, instrument_type;

```

