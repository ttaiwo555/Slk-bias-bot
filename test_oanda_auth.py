import os, requests
from dotenv import load_dotenv
load_dotenv()

token = os.getenv('OANDA_API_TOKEN')
acct  = os.getenv('OANDA_ACCOUNT_ID')
env   = os.getenv('OANDA_ENVIRONMENT', 'practice')
base  = 'https://api-fxpractice.oanda.com' if env != 'live' else 'https://api-fxtrade.oanda.com'

print(f'Environment : {env}')
print(f'Base URL    : {base}')
print(f'Account ID  : {acct}')
print(f'Token length: {len(token) if token else 0}')
print()

# Test 1: account summary (checks if token+account combo is valid)
r = requests.get(
    f'{base}/v3/accounts/{acct}/summary',
    headers={'Authorization': f'Bearer {token}'},
    timeout=10
)
print(f'Account summary: HTTP {r.status_code}')
if r.status_code != 200:
    print('  Error body:', r.text[:400])
print()

# Test 2: candles (what calibrate.py calls)
r2 = requests.get(
    f'{base}/v3/instruments/EUR_USD/candles',
    params={'granularity': 'H1', 'count': '3', 'price': 'M'},
    headers={'Authorization': f'Bearer {token}'},
    timeout=10
)
print(f'Candles EUR_USD: HTTP {r2.status_code}')
if r2.status_code != 200:
    print('  Error body:', r2.text[:400])
else:
    data = r2.json()
    n = len(data.get('candles', []))
    print(f'  Got {n} candles — connection OK')
