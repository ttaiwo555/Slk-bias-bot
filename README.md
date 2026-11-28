# SLK Bot State Store (Orphan Branch)

This dedicated orphan branch (`bot-data`) stores state files across GitHub Actions runs:
- `state/candles.db`: Local SQLite candle cache & dedupe history
- `state/alerts_log.csv`: Historical alerts CSV
- `state/state.json`: Last run timestamps, candle close times & Telegram update offset

This branch is overwritten on every run via single commit force-push to prevent repository history bloat.
