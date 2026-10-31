#!/usr/bin/env bash
set -e

echo "==================================================================="
echo "  SLK BIAS BOT - Linux/macOS Startup & Testing Script"
echo "==================================================================="
echo ""

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 is not installed or not in PATH."
    exit 1
fi

# 2. Check .env
if [ ! -f ".env" ]; then
    echo "[WARNING] .env file not found."
    if [ -f ".env.example" ]; then
        echo "Creating .env from .env.example..."
        cp .env.example .env
        echo "Created .env. Please configure your tokens in .env."
    fi
fi

# 3. Mode Selection
echo "Select mode:"
echo "  [1] Dry-Run Mode (Console testing, no Telegram push)"
echo "  [2] Live Mode (Telegram Bot Polling + Scheduled Scanning)"
echo "  [3] Run Full Unit Test Suite (pytest)"
echo ""
read -p "Enter selection [1-3] (default 1): " MODE
MODE=${MODE:-1}

if [ "$MODE" = "2" ]; then
    echo ""
    echo "Starting SLK Bias Bot in LIVE MODE..."
    python3 -m slk_bot.bot.main
elif [ "$MODE" = "3" ]; then
    echo ""
    echo "Running pytest test suite..."
    python3 -m pytest slk_bot/tests -v
else
    echo ""
    echo "Starting SLK Bias Bot in DRY-RUN MODE..."
    python3 -m slk_bot.bot.main --dry-run
fi
