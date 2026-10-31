import sys
sys.stdout.reconfigure(encoding='utf-8')
with open('reference_alerts.txt', 'r', encoding='utf-8') as f:
    text = f.read()

import re

# Split alerts by "🚨"
alerts = text.split("🚨")
print(f"Total alert blocks: {len(alerts) - 1}")

has_unfresh = []
has_fresh = []
has_lowprob = []

for idx, a in enumerate(alerts[1:], 1):
    lines = [l.strip() for l in a.split('\n') if l.strip()]
    header = lines[0] if lines else ""
    full_str = " ".join(lines)
    
    if "unfresh" in full_str.lower():
        has_unfresh.append((idx, header, full_str))
    if "fresh" in full_str.lower():
        has_fresh.append((idx, header, full_str))
    if "low" in full_str.lower() and "prob" in full_str.lower():
        has_lowprob.append((idx, header, full_str))

print(f"Alerts mentioning 'unfresh': {len(has_unfresh)}")
print(f"Alerts mentioning 'fresh': {len(has_fresh)}")
for idx, h, s in has_fresh:
    print(f"  #{idx} {h}:")
    for l in s.split(' · '):
        if 'fresh' in l.lower():
            print(f"    -> {l}")

print(f"Alerts mentioning 'low prob': {len(has_lowprob)}")
