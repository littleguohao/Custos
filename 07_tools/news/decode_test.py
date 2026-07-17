# -*- coding: utf-8 -*-
"""Try to decode mojibake from git history."""
import subprocess, json

# Get old registry from git
result = subprocess.run(
    ['git', 'show', '9828fcc:00_governance/RSS_SOURCE_REGISTRY.json'],
    capture_output=True,
    cwd=r'C:\Users\gh\.openclaw-tdxclaw\workspace\strategy_team'
)
raw_bytes = result.stdout

# Try different decodings
for enc in ['utf-8', 'gb18030', 'gbk', 'latin-1', 'cp1252']:
    try:
        text = raw_bytes.decode(enc)
        # Try to parse JSON
        try:
            d = json.loads(text, strict=False)
            print(f'\n=== {enc} JSON parse OK ===')
            print(f'Sources: {len(d.get("sources", []))}')
            for s in d.get('sources', []):
                print(f'  {s["id"]}: {s["name"]}')
            break
        except json.JSONDecodeError as e:
            # Maybe the encoding is right but JSON has issues, try to find source names
            if 'sources' in text:
                import re
                matches = re.findall(r'"id"\s*:\s*"([^"]+)".*?"name"\s*:\s*"([^"]+)"', text, re.S)
                if matches:
                    print(f'\n=== {enc} (regex extract) ===')
                    for sid, sname in matches:
                        print(f'  {sid}: {sname}')
                    break
    except Exception as e:
        print(f'{enc}: decode error {e}')

# Also try: raw bytes might be utf-8 that was double-encoded
# Read as utf-8, then fix mojibake
try:
    text = raw_bytes.decode('utf-8')
    # Now text has garbled chars, try to fix
    fixed = text.encode('latin-1').decode('utf-8')
    d = json.loads(fixed, strict=False)
    print(f'\n=== utf-8 -> latin-1 -> utf-8 (mojibake fix) ===')
    print(f'Sources: {len(d.get("sources", []))}')
    for s in d.get('sources', []):
        print(f'  {s["id"]}: {s["name"]}')
except Exception as e:
    print(f'Mojibake fix failed: {e}')
