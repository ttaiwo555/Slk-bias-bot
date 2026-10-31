# SLK Bias Bot – GitHub Actions Run-Once Deployment Guide

This repository contains the SLK Bias Bot converted into an autonomous **run-once and exit** job scheduled via GitHub Actions. It runs every 15 minutes at off-peak minutes (`7, 22, 37, 52`), evaluates per-instrument candle grids with automatic daylight saving time (DST) handling, processes pending Telegram commands, and persists historical state on a dedicated single-commit orphan branch (`bot-data`).

---

## 1. Key Architectural Features

- **Run-Once Architecture (`run_once.py`)**:
  - Initializes state from local cache or orphan branch.
  - Clears Telegram webhooks via `deleteWebhook` and processes pending commands via `getUpdates` with a stored offset.
  - Evaluates whether a new candle closed on any instrument's grid.
  - **Fast No-Op Path**: If no new candle has closed and no command was issued, exits in under **2 seconds**.
  - **Incremental Sync**: Only fetches newly closed candles from OANDA and Coinbase.
  - **Deduplication**: Never duplicates alerts.
  - **Weekly Heartbeat**: Sends an automated status report every 7 days.
  - **Failure Alert**: Immediately alerts the owner on Telegram if a run fails.
- **Price Alerts Removed**:
  - `/pricealert`, `/pricealerts`, `/cancelalert`, and 48-hour timers have been completely removed.
  - Every other strategy, inspection, and preference command is retained: `/start`, `/help`, `/status`, `/active`, `/watching`, `/internal`, `/lowprob`, `/scan`, `/instruments`, `/verify`, `/alerts`, `/terms`, `/bias`.
- **Public Repository Security**:
  - Zero secrets in code, git commits, or state files.
  - `.gitignore` strictly ignores `.env`, `*.db`, and local logs.
  - Secrets are supplied strictly via GitHub Repository Secrets and environment variables.
- **State on Orphan Branch (`bot-data`)**:
  - `candles.db`, `state.json`, and `alerts_log.csv` are committed and force-pushed to an orphan branch (`bot-data`) on every run (`if: always()`).
  - Because it uses a single-commit force push (`git push --force origin bot-data`), the repository history never bloats.
  - Stored state file size: ~26.7 MB uncompressed (under ~5.5 MB compressed).
- **Keepalive Schedule (`keepalive.yml`)**:
  - Prevents GitHub from automatically suspending scheduled workflows after 60 days of inactivity.

---

## 2. GitHub Repository Setup Instructions

Follow these steps to deploy and run the bot on GitHub Actions:

### Step 1: Stop Local Bot Instance
> **CRITICAL**: Before initiating GitHub Actions runs, make sure no local instance of the bot is running on your computer. If two instances poll `getUpdates` simultaneously, Telegram returns `HTTP 409 Conflict`.
- On Windows: Close any terminal running `run_bot.bat` or `python -m slk_bot.bot.main`.
- In PowerShell/CMD, verify no lingering process:
  ```powershell
  Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
  ```

### Step 2: Create a Public GitHub Repository
1. Log into your GitHub account and click **New Repository**.
2. Name the repository (e.g. `slk-bias-bot`).
3. Set visibility to **Public** (Free GitHub Actions runners provide unlimited minutes on public repos).
4. Do **not** initialize with a README or license (you will push this codebase directly).

### Step 3: Configure GitHub Actions Workflow Permissions
To allow the workflow to commit and update state on the `bot-data` orphan branch:
1. Navigate to **Settings** $\to$ **Actions** $\to$ **General**.
2. Scroll to **Workflow permissions**.
3. Select **Read and write permissions**.
4. Check **Allow GitHub Actions to create and approve pull requests**.
5. Click **Save**.

### Step 4: Add GitHub Repository Secrets
Navigate to **Settings** $\to$ **Secrets and variables** $\to$ **Actions** $\to$ **New repository secret**, and create:

| Secret Name | Description | Example / Note |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Your Telegram Bot token from BotFather | `123456789:ABCdefGhI...` |
| `TELEGRAM_CHAT_ID` | Your Telegram numerical Chat ID | `5647421723` |
| `OANDA_API_KEY` | Your OANDA API key or token | Personal Access Token |
| `OANDA_ACCOUNT_ID` | Your OANDA account number | `101-004-40750747-001` |
| `OANDA_ENVIRONMENT` | `practice` or `live` | `practice` |
| `COINBASE_API_KEY` | *(Optional)* Coinbase API key | Optional |

### Step 5: Push the Code to GitHub
Initialize git and push `main`:
```bash
git init
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/slk-bias-bot.git
git add .
git commit -m "feat: initial commit for run-once SLK bias bot on GitHub Actions"
git push -u origin main
```

### Step 6: Silent First Start (Build State Without Flooding Alerts)
To seed initial history without sending dozens of past alerts to Telegram:
1. Go to your repository on GitHub.
2. Click the **Actions** tab.
3. Select the **SLK Bias Bot Runner** workflow on the left sidebar.
4. Click **Run workflow** (drop-down button on the right).
5. Check the **Silent first start (seed history without alerts)** checkbox.
6. Click **Run workflow**.

Once this first run finishes:
- `candles.db`, `state.json`, and `alerts_log.csv` are seeded and committed to the `bot-data` orphan branch.
- The 15-minute cron will automatically monitor the markets and only alert on **newly confirmed live setups** going forward.

---

## 3. Telegram Commands Reference

All commands are restricted to your configured `TELEGRAM_CHAT_ID`. Messages from any other user are dropped.

| Command | Description |
|---|---|
| `/status` | Displays last run time, state age, last new-candle times per grid, cached candle depth, and alert count |
| `/active` | Lists all active confirmed setups (both External and Internal) |
| `/internal` | Lists setups with confirmed internal breakouts currently awaiting external confirmation |
| `/watching` | Lists setups currently monitoring for LTF breakout |
| `/lowprob` | Lists low-probability triggers and unfresh Method 1 setups |
| `/bias SYMBOL` | Displays current HTF bias, latest daily close, and active setups for a symbol (e.g. `/bias EURUSD`) |
| `/alerts` | Displays or changes alert preferences (`/alerts external on/off`, `/alerts internal on/off`, `/alerts trend required/unrequired`) |
| `/scan` | Triggers an immediate scan across all 37 watchlist instruments on the current run |
| `/instruments` | Lists all 37 active instruments and their alignment profiles |
| `/verify SYMBOL TF` | Inspects candle timestamps and active levels for a symbol and timeframe |
| `/terms` | Plain-language glossary of strategy terms, candle naming, disrespect caps, and freshness rules |

---

## 4. Candle Grids & Automatic Daylight Saving Time (DST)

The bot evaluates candles according to each asset class's natural session close:
- **Forex & Indices (33 pairs)**: Anchor `17:00 America/New_York` (H4 candles at 21:00, 01:00, 05:00, 09:00, 13:00, 17:00 NY). Closes Friday 17:00 NY until Sunday 17:00 NY.
- **Metals (`metal_ny18` - XAUUSD, XAGUSD)**: Anchor `18:00 America/New_York` (H4 candles at 22:00, 02:00, 06:00, 10:00, 14:00, 18:00 NY).
- **Crypto (BTCUSD, ETHUSD)**: Anchor `00:00 UTC` (H4 candles at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC). Runs 24/7.
- **USOIL**: Excluded from strategy triggers per specifications.

> **DST Note**: Close times are calculated using `zoneinfo.ZoneInfo("America/New_York")`. When New York clocks shift between EDT (UTC-4) and EST (UTC-5), the UTC close times automatically shift by 1 hour. No manual intervention is ever required.

---

## 5. Performance, Runtime & Actions Usage Estimates

### Execution Time Breakdown
- **Fast No-Op Path**: Runs when no new candle has closed on any active grid (e.g., at :22, :37, :52 of intermediate hours, or during weekend market close).
  - Runtime: **1.5 – 3 seconds**.
  - Actions compute consumed: ~2–3 seconds.
- **Active Candle Close Path**: Runs shortly after a candle closes (e.g. :07 after an H4 close).
  - Runtime: **20 – 35 seconds** (fetches latest 5 candles for relevant instruments, updates SQLite, detects setups, and dispatches alerts).

### Monthly Actions Usage & Cost
- Frequency: Every 15 minutes = 96 runs/day = ~2,880 runs/month.
- **Daily Compute**: ~90 no-op runs $\times$ 2.5s (~225s) + 6 active runs $\times$ 25s (~150s) $\approx$ **6.25 minutes of runner time per day**.
- **Monthly Compute**: ~188 minutes of runner execution per month.
- **Cost**: **$0.00 / Free**. Public GitHub repositories receive **unlimited free GitHub Actions minutes**.

---

## 6. Verification & Parity Report

| Verification Item | Status | Result |
|---|---|---|
| Strategy Test Suite | **VERIFIED** | 65 / 65 pytest test cases passed in 2.61s |
| Golden Alert: AUDCAD Weekly $\to$ Daily External | **VERIFIED** | Passed with 100% TradingView parity (target 0.98535 diff 0.004%) |
| Golden Alert: XAGUSD Daily $\to$ H4 External | **VERIFIED** | Passed with 100% TradingView parity (target 61.366 diff 0.027%) |
| Coinbase 4-Hour Granularity | **CONFIRMED** | Native 14400 returns `HTTP 400 Unsupported granularity`. Resampling H4 from 1h candles on the 00:00 UTC grid is required and confirmed. |
| OANDA Practice Demo Connection | **VERIFIED** | `HTTP 200` on account summary and live candle retrieval. |
| Fast No-Op Path | **VERIFIED** | Successfully executed in under 2 seconds when no new candle has closed. |
| Price Alert Removal | **VERIFIED** | `/pricealert`, `/pricealerts`, `/cancelalert` removed; all 11 other commands functional. |
| Public Repo Security | **VERIFIED** | No secrets in codebase, state files, or commit logs. |
