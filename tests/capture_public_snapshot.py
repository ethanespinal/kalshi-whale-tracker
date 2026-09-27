"""Manual, bounded public-data snapshot used to verify API-shaped tests."""
import json
from pathlib import Path
import time
import requests

FIELDS = ('ticker', 'event_ticker', 'title', 'yes_sub_title', 'no_sub_title',
          'strike_type', 'floor_strike', 'cap_strike', 'status', 'close_time',
          'yes_ask_dollars', 'no_ask_dollars', 'yes_ask_size_fp',
          'no_ask_size_fp', 'yes_bid_size_fp', 'rules_primary', 'notional_value_dollars')
result = []
for series in ('KXNFLSPREAD', 'KXNFLTOTAL', 'KXATPGAME', 'KXCS2GAME'):
    response = requests.get('https://external-api.kalshi.com/trade-api/v2/events',
        params={'series_ticker': series, 'status': 'open',
                'with_nested_markets': 'true', 'limit': 1}, timeout=20)
    response.raise_for_status()
    for event in response.json()['events']:
        item = {k: event.get(k) for k in ('event_ticker', 'series_ticker', 'title', 'sub_title', 'mutually_exclusive')}
        item['markets'] = [{k: m[k] for k in FIELDS if k in m} for m in event['markets'][:2]]
        result.append(item)
        print(json.dumps(item))
    time.sleep(0.7)
Path('tests/fixtures/kalshi_public_snapshot.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
