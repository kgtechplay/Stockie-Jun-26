# Legacy Scripts

These scripts are outside the active NIFTY production cron pipeline. Keep them
only for setup, migration, or historical comparison work.

Active production jobs live in `scripts/daily_NIFTY/`, `scripts/backfill_NIFTY/`,
and `scripts/Common/`; see `scripts/README.md`.

Legacy utilities currently parked here include broader-universe loaders,
calendar helpers, and old watched-instrument population flows. Review each script
before using it against production data.