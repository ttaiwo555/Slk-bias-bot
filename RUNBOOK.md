# SLK Bias Bot Operations & Deployment Runbook

This guide covers everything needed to operate, test, deploy, and maintain the SLK Bias Bot in plain language.

---

## 1. System Overview & Architecture

The SLK Bias Bot is an automated market bias alerting engine:
- **Higher Timeframe (HTF):** Monitors Daily (`D`) and Weekly (`W`) candles for Key Level rejections (Method 1), Liquidity Sweeps (Method 2), and combined rejections (Method 3).
- **Lower Timeframe (LTF):** Tracks 4-Hour (`H4`) or Daily (`D`) candles for structural breakouts (`External BO` or `Internal BO`).
- **Trend Filter:** Validates bias against the HTF market structure trend (broken A-shape for uptrend, broken V-shape for downtrend).
- **Storage:** Local SQLite database at `slk_bot/storage/candles.db` using WAL (Write-Ahead Logging) mode for crash safety.
- **Delivery:** Sends formatted bias alerts and touch price alerts directly to your private Telegram chat.

### 1.1 Date & Display Conventions
Alerts use actual chart dates rather than raw UTC candle-open times:
- **Daily Candles:** Named by the date TradingView shows under the candle on the daily chart (the date of the candle's close in New York time; e.g., a candle opening Thursday 21:00 UTC closes Friday 17:00 NY and is named `Fri`).
- **Weekly Candles:** Named by the Monday of its trading week (e.g. `Mon 12 Oct`).
- **H4 Candles:** Named by actual open date and time in West Africa Time (`WAT` = UTC+1, e.g. `Mon 12 Oct 06:00 WAT`).
- **Alert Creation Time:** Displayed in `WAT` (e.g. `Alert Created: Mon 12 Oct 10:00 WAT`).
- **Formation Candles:** Display the exact dates and colours of the two candles that formed the key level on its timeframe (e.g., `formed by Tue 6 Oct 2026 (bullish) and Wed 7 Oct 2026 (bearish)`).
- **Matching Integrity:** `alerts_log.csv` preserves internal UTC candle timestamps for matching and verification.

### 1.2 Strategy Rules & Profiles
The bot supports modular strategy profiles in `settings.yaml`:
- **Default Profile (`forex_com`):**
  - **Freshness Rule:** Unfresh key levels do not count. Setups strictly require fresh levels that have not been tested since formation.
  - **Disrespect Caps:** A-shape and V-shape levels are retired after more than 3 disrespects (`max_disrespects: 3`). Open-Close levels are retired after more than 1 disrespect (`ocl_max_disrespects: 1`; count 1 is flipped OCL, count 2 is retired).
  - **Age Limits:** Daily setups (`D1→H4`) exclude key levels older than 6 months (measured at the rejection candle). A level with exactly 3 disrespects is valid only if 4 months old or less. Weekly setups (`W1→D1`) have no age limit. The timeframes to which age limits apply are configurable via `age_rules.timeframes`.
- **Reference Parity Profile (`reference_parity`):**
  - Retains uncapped disrespects and unconstrained age limits for baseline comparison against historical reference bot alerts.

## 2. Installation & Prerequisites

### Prerequisites
- **Python:** 3.10, 3.11, or 3.12 installed.
- **Docker & Docker Compose:** (For 24/7 Linux server deployment only).
- **OANDA Practice/Live Account:** API Key and Account ID (for forex, metals, indices, and USOIL).
- **Telegram Bot:** A bot token from [@BotFather](https://t.me/BotFather) and your personal Telegram Chat ID.

### Local Installation (Windows / Linux / macOS)
1. Clone or copy the project into a folder.
2. Open a terminal in the project directory:
   ```bash
   pip install --upgrade pip
   pip install -e .
   ```
3. Verify tests pass:
   ```bash
   python -m pytest slk_bot/tests -v
   ```

---

## 3. Configuration (.env Setup)

Copy `.env.example` to `.env` in the root folder:
```bash
cp .env.example .env
```

Edit `.env` with your actual credentials:
```ini
# Telegram Configuration
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=987654321

# OANDA API Credentials
OANDA_API_KEY=your_oanda_v20_personal_access_token
OANDA_ACCOUNT_ID=001-001-1234567-001
OANDA_ENVIRONMENT=practice   # Change to "live" when trading live account

# Coinbase API Configuration (Active Crypto Provider)
COINBASE_API_KEY=your_coinbase_api_key_uuid
```

> **Note on Crypto Feeds:** Coinbase is the active crypto provider (`crypto_provider: coinbase` in `settings.yaml`). Public market candles (BTC-USD, ETH-USD) do not require private authorization, while `COINBASE_API_KEY` is loaded from `.env` for header attribution.

> **Security Note:** Never commit your `.env` file to version control. The `.gitignore` file already excludes `.env`.

---

## 4. Testing & Dry-Run Mode

### On Windows
Double-click `run_bot.bat` or run from terminal:
```cmd
run_bot.bat
```
Select option `[1]` for Dry-Run mode.

### On Linux / macOS
Run with the `--dry-run` flag:
```bash
python -m slk_bot.bot.main --dry-run
```

### What Happens in Dry-Run:
- The bot seeds candle data if the local database is fresh.
- It scans the watchlist for all active confirmed setups.
- It prints alerts directly to the console instead of pushing them to Telegram.
- You can interact via console input or test Telegram commands without sending outbound broadcast messages.

---

## 5. One-Week Parallel Test on Windows PC

Run a side-by-side parallel test on your Windows computer to compare live bot alerts with the reference bot over a full trading week.

### 5.1 What to Install
1. **Python 3.10+**: Download from python.org. Check **"Add Python to PATH"** during installation.
2. **Repository Setup**:
   Open PowerShell in the project directory:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install --upgrade pip
   pip install -e .
   ```
3. **Environment (.env)**:
   Ensure `.env` exists with valid credentials:
   ```ini
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   OANDA_API_KEY=your_oanda_token
   OANDA_ACCOUNT_ID=your_oanda_account_id
   OANDA_ENVIRONMENT=practice
   ```

### 5.2 How to Start
Start the bot runner in regular mode:
```powershell
python -m slk_bot.bot.main
```
or double-click `run_bot.bat` and select `[2] Run Bot (Live Scanning)`.

**What the bot does during the test:**
- Every 5 minutes, fetches closed candles and evaluates both `forex_com` (default) and `reference_parity` profiles.
- Every alert generated under **both profiles** is automatically logged to `alerts_log.csv` with full metadata (`id, symbol, dir, pair, kl_kind, kl_price, bo_price, rej_open_utc, bo_open_utc, next_day, swept_level, kl_formed_wat, alert_time_utc, method, profile`).
- Real-time Telegram messages are sent to your phone for the default `forex_com` profile.

### 5.3 What Must Stay Running
- **PowerShell Window**: Keep the PowerShell terminal running the bot open.
- **Windows Sleep Settings**: Go to *Settings > System > Power & sleep* and set *Sleep* to **Never** when plugged in, so scans trigger reliably every 5 minutes from Sunday 21:00 UTC through Friday 21:00 UTC.
- **Internet Connection**: Active network connection required for OANDA and Telegram API calls.

### 5.4 How to Match and Review Results Daily or at Week's End
1. Paste the reference bot's Telegram messages into `reference_alerts.txt`.
2. Convert them into UTC reference rows:
   ```powershell
   python analysis/parse_reference_text.py --input reference_alerts.txt --output analysis/output/reference_live.csv
   ```
3. Run the automated live match:
   ```powershell
   python analysis/match_live.py --alerts alerts_log.csv --reference analysis/output/reference_live.csv
   ```
   This prints:
   - Status summary: `MATCH`, `PARTIAL`, `MISSING` counts.
   - Same-breakout-candle count and percentage.
   - Any extra setups alerted by the bot not present in the reference feed.

---

## 6. Going Live (24/7 Server Deployment)

For 24/7 continuous monitoring, use the provided Docker Compose configuration with automatic restarts.

### Step 1: Transfer Files to Your Linux Server
Copy the project folder to your server (e.g., `/opt/slk_bot`).

### Step 2: Configure Environment
Ensure your `.env` file is present in the project directory with valid credentials:
```bash
ls -la .env
```

### Step 3: Launch with Docker Compose
Start the container in detached mode with automatic restart policy enabled (`restart: unless-stopped`):
```bash
docker compose up -d --build
```

### Step 4: Verify Running Container
```bash
docker compose ps
docker compose logs -f
```

You should see:
```text
INFO: Seeded database with historical candles.
INFO: Starting SLK Bias Bot scheduler (scan interval: 5m)...
INFO: Telegram Bot polling started.
```

### Useful Management Commands
- **View live logs:**
  ```bash
  docker compose logs -f --tail=100
  ```
- **Stop the bot:**
  ```bash
  docker compose down
  ```
- **Restart the bot:**
  ```bash
  docker compose restart
  ```
- **Update code / configuration:**
  ```bash
  docker compose down
  docker compose up -d --build
  ```

---

## 7. Telegram Commands Quick Reference

All commands are restricted strictly to your `TELEGRAM_CHAT_ID`:

| Command | Action |
| :--- | :--- |
| `/alerts` | View or toggle alert preferences (External BO, Internal BO, Trend alignment). |
| `/alerts external on\|off` | Turn External breakout confirmation push alerts On or Off. |
| `/alerts internal on\|off` | Turn Internal breakout confirmation push alerts On or Off. |
| `/alerts trend required\|unrequired` | Require setup direction to match HTF market structure trend. |
| `/terms` | View plain-language explanation of all alert terms and indicators. |
| `/active` | View all currently active confirmed setups across the watchlist. |
| `/internal` | View setups that have confirmed internal breakouts awaiting external. |
| `/watching` | View setups that rejected a level and are waiting for LTF breakout. |
| `/lowprob` | View low-probability triggers and unfresh Method 1 setups. |
| `/pricealert SYMBOL PRICE` | Set a 48h market-hours touch price alert (e.g. `/pricealert EURUSD 1.1300`). |
| `/pricealerts` | List all pending price alerts. |
| `/cancelalert ID` | Cancel a pending price alert. |
| `/status` | View system health, database candle counts, and scheduler status. |
| `/scan` | Trigger an immediate manual scan across all instruments. |

---

## 7. Adding or Removing Instruments

Watchlist instruments are defined in `slk_bot/config/instruments.yaml`.

### To Add an Instrument:
1. Open `slk_bot/config/instruments.yaml`.
2. Add the symbol entry under `instruments`:
   ```yaml
   - name: "NZDCAD"
     asset_class: "forex"
     alignment: "forex"
     display_scale: 1.0
     exclude_from_triggers: false
   ```
3. Fetch historical candles for the new instrument:
   ```bash
   python analysis/fetch_deep_history.py
   ```
4. Restart the bot or trigger `/scan`.

### To Disable an Instrument:
Set `exclude_from_triggers: true` on the instrument in `instruments.yaml`.

---

## 8. Crash Recovery & Maintenance

### SQLite WAL Mode Safety
The database at `slk_bot/storage/candles.db` operates in SQLite **WAL (Write-Ahead Logging)** mode. Even if the server suffers an abrupt power loss or reboot:
- The database automatically recovers upon startup.
- Deduplication keys for sent alerts are preserved in `sent_alerts` table, preventing repeated alert spam.
- Active price alerts remain stored in `price_alerts` table and resume monitoring immediately.

### Silent First-Start Seeding
When started on an empty database, the bot seeds candles and records existing setups quietly without flooding your Telegram with historical alerts.

### Backup Database
To back up all cached candles, setups, and price alerts:
```bash
sqlite3 slk_bot/storage/candles.db ".backup 'slk_bot/storage/backup_$(date +%Y%m%d).db'"
```
