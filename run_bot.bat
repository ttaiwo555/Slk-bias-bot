@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

echo ===================================================================
echo   SLK BIAS BOT - Windows Startup & Testing Script
echo ===================================================================
echo.

:: 1. Verify Python availability
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not available in PATH.
    pause
    exit /b 1
)

:: 2. Check for .env file
if not exist ".env" (
    echo [WARNING] .env file not found.
    if exist ".env.example" (
        echo Creating .env from .env.example...
        copy .env.example .env > nul
        echo Created .env. Please update it with your TELEGRAM_BOT_TOKEN and OANDA credentials.
    ) else (
        echo Please create a .env file with TELEGRAM_BOT_TOKEN and OANDA_API_KEY.
    )
)

:: 3. Ask run mode
echo Select mode:
echo   [1] Dry-Run Mode (Console testing, no Telegram push)
echo   [2] Live Mode (Telegram Bot Polling + Scheduled Scanning)
echo   [3] Run Full Unit Test Suite (pytest)
echo.
set /p MODE="Enter selection [1-3] (default 1): "

if "%MODE%"=="2" (
    echo.
    echo Starting SLK Bias Bot in LIVE MODE...
    python -m slk_bot.bot.main
) else if "%MODE%"=="3" (
    echo.
    echo Running pytest test suite...
    python -m pytest slk_bot/tests -v
) else (
    echo.
    echo Starting SLK Bias Bot in DRY-RUN MODE...
    python -m slk_bot.bot.main --dry-run
)

if errorlevel 1 (
    echo.
    echo [ERROR] Bot exited with error. Check output above.
)

echo.
pause
