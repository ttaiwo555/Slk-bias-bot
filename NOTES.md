# NOTES.md – SLK Bias Bot

Every assumption, limitation and decision made during development is recorded here.
All items marked **[CONFIG]** can be changed in `config/settings.yaml` or `config/instruments.yaml`
without touching any code.

---

## DATA SOURCE DECISIONS

### Primary source: OANDA v20 REST API

The owner confirmed that OANDA is accessible. OANDA is used as the primary source for all non-crypto
instruments: forex pairs, indices, commodities, and gold.

- **API token and account ID** come from the `.env` file only. Never hardcoded.
- **Environment** defaults to `practice`. Change `OANDA_ENVIRONMENT=live` in `.env` when going live.
- **Base timeframe**: 1-hour (H1) candles fetched from OANDA and resampled into H4, Daily,
  and Weekly candles with session-aligned boundaries.
  Native OANDA D/W candles are NOT used until calibration confirms they match TradingView.

### Crypto source: Binance public market-data API (no key required)

- BTCUSDT stands in for BTCUSD.
- ETHUSDT stands in for ETHUSD.
  Binance does not offer USD-settled spot pairs; it uses USDT (Tether).
  Price difference is typically < 0.05% but can spike. Any marginal alerts on crypto should
  be verified on your TradingView chart.
- Bybit is an automatic fallback if Binance is unreachable.

---

## SECTION 13 ASSUMPTIONS (from the brief)

| # | Assumption | Default | Config key |
|---|-----------|---------|------------|
| 1 | Sweep reference candle R = immediately previous candle only | 1 | strategy.sweep_lookback_candles |
| 2 | Dojis (close == open) never form levels | Yes | (fixed rule) |
| 3 | Gap handling: A-shape = higher of close(i)/open(i+1); V-shape = lower | Yes | (fixed rule) |
| 4 | Level lookback windows | 104w / 250d / 300 H4 | strategy.lookback_* |
| 5 | Breakout alert only after HTF trigger candle closes | Yes | (fixed rule) |
| 6 | External level LX must still be intact at extreme candle e | Yes | (fixed rule) |
| 7 | OCL changes role once only | Yes | (fixed rule) |
| 8 | CONFIRMED by owner: 14 days = 14 TRADING days (not calendar weeks) | 14 trading days | strategy.expiry_weekly_trigger_candles |
| 9 | OWNER MUST CONFIRM: session alignment verified in Phase 0 calibration | Candidate A: 17:00 NY | alignment.* |
| 10 | BTCUSDT / ETHUSDT proxy for BTCUSD / ETHUSD | Yes | instruments.yaml |
| 11 | Monthly triggers excluded | Yes | no Monthly TF pair |
| 12 | At most one alert per breakout type per setup | Yes | storage dedupe |

---

## INSTRUMENT COVERAGE NOTES

*(Auto-filled when coverage_check.py runs)*

Symbols that may need verification:
- GER40: tried DE30_EUR. OANDA has historically used DE30_EUR and DE30_USD.
  Run coverage_check.py to see exact symbol name.
- USOIL: tried WTICO_USD. If missing, BCO_USD (Brent) is a fallback but a different benchmark.

---

## SESSION ALIGNMENT CANDIDATES

| Label | Daily open | Timezone | Week starts |
|-------|-----------|----------|-------------|
| A (default) | 17:00 | America/New_York (DST-aware) | Sunday 17:00 NY |
| B | 00:00 | UTC | Monday 00:00 UTC |

DST note: US clocks change 1 November 2026.
- Before: forex day opens 21:00 UTC (EDT, UTC-4)
- After: forex day opens 22:00 UTC (EST, UTC-5)
The resampler handles this automatically via pytz.

---

## PHASE 0 CALIBRATION STATUS

Status: COVERAGE COMPLETE - all 37 instruments have a data source.`n
Steps:
1. Open TradingView, FOREXCOM feed
2. For EURUSD, XAUUSD, NAS100, BTCUSD: read last 5 closed Daily and 5 closed H4 candles
3. Fill in TV_CANDLES dict in calibrate.py
4. Run: python calibrate.py
5. Report result here before Phase 1

---

## OPEN QUESTIONS FOR THE OWNER

1. EXPIRY (Assumption 8): Does "14 days" for a weekly trigger mean 14 trading days
   or 2 calendar weeks (10 trading days)?

2. GER40 SYMBOL: Run coverage_check.py – it will show exactly what OANDA calls this instrument.

3. INDEX ALIGNMENT: For NAS100/SPX500/US30, does your TradingView daily bar open at 17:00 NY
   (like forex) or at a different time? Calibration will confirm.

