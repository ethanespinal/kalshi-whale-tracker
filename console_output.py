"""Human-readable display of recorded decisions; no matching or trading calls."""
import json
from decimal import Decimal, InvalidOperation

from alert_parser import parse_alert


def text(value):
    # Keep externally supplied names on one console line.
    return ' '.join(str(value).split()) if value is not None else 'Unavailable'


def decimal(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def money(value, places=2, signed=False):
    value = decimal(value)
    if value is None:
        return 'Unavailable'
    sign = ('+' if value >= 0 else '-') if signed else ('-' if value < 0 else '')
    return f'{sign}${abs(value):,.{places}f}'


def contracts(value):
    value = decimal(value)
    if value is None:
        return '0'
    return f'{value:.4f}'.rstrip('0').rstrip('.')


def format_summary(row, trade=None):
    details = json.loads(row.get('details') or '{}')
    alert = details.get('alert')
    if not alert:
        # Stale messages are skipped before parsing by the trader. Parse only
        # for display, without changing the saved decision or triggering lookup.
        try:
            alert = parse_alert(row['raw_text'])
        except (ValueError, TypeError):
            alert = None
    alert = alert or {}
    match = details.get('match') or {}
    market = match.get('market') or {}
    quote = details.get('quote') or {}
    side = trade['side'] if trade else match.get('side')
    live_price = decimal(quote.get(f'{side}_ask_dollars')) if side else None
    whale_price = decimal(alert.get('price'))
    slippage = live_price - whale_price if live_price is not None and whale_price is not None else None
    close_hours = decimal(details.get('hours_to_close'))
    timing = details.get('resolution_timing') or {}
    original = next((line.strip().removeprefix('📌').strip()
                     for line in row['raw_text'].splitlines() if line.strip()), 'Unavailable')
    status = {
        'traded': 'PAPER FILLED', 'skipped': 'SKIPPED',
        'shadow_only': 'SHADOW_ONLY',
        'unmatched': 'SKIPPED (no match)', 'ambiguous': 'SKIPPED (ambiguous match)',
        'unsupported': 'SKIPPED (unsupported market)', 'invalid': 'SKIPPED (invalid alert)',
        'line_not_found': 'SKIPPED (line not found)',
        'unsupported_series': 'UNSUPPORTED_SERIES',
        'event_not_found': 'EVENT_NOT_FOUND',
        'contract_not_found': 'CONTRACT_NOT_FOUND',
        'ambiguous_match': 'AMBIGUOUS_MATCH',
        'name_alias_miss': 'NAME_ALIAS_MISS',
        'stale': 'SKIPPED (stale/closed market)',
        'retryable': 'DEFERRED (retry pending)', 'error': 'ERROR (no paper fill)',
    }.get(row['status'], row['status'].upper())
    if row['status'] == 'skipped' and row.get('reason') == 'Market closes too far in the future':
        status = 'SKIPPED — market closes too far in the future'
    stake = Decimal(trade.get('stake', trade['quantity'] * trade['price'])) / 10000 if trade else Decimal(0)
    kind = {'game_winner': 'Game winner', 'spread': 'Spread', 'total': 'Total'}.get(
        alert.get('market_type'), alert.get('market_type', 'Unavailable'))
    lines = [
        '', f"--- WHALE ALERT {text(row['source_key'])} | PAPER ONLY ---",
        f"Market: {text(alert.get('market') or original)}",
        f"Trader: {text(alert.get('trader'))} | Type: {text(kind)}",
        f"Whale side: {text(alert.get('side'))} | Price: {money(whale_price, 4)} | Amount: {money(alert.get('amount'))}",
        f"Whale win rate: {text(str(alert.get('win_rate')) + '%' if alert.get('win_rate') is not None else None)} | Whale ROI: {text(str(alert.get('roi')) + '%' if alert.get('roi') is not None else None)}",
        f"Kalshi market: {text(market.get('title') or market.get('yes_sub_title'))}",
        f"Ticker: {text(trade['ticker'] if trade else market.get('ticker'))} | Direction: {side.upper() if side else 'Unavailable'}",
        f"Live ask: {money(live_price, 4)} | Slippage (live - whale): {money(slippage, 4, signed=True)}",
        f"Hours to resolution: {f'{close_hours:.2f}h' if close_hours is not None else 'Unavailable'} | Source: {text(timing.get('source'))}",
        f"Simulated stake: {money(stake, 4)} | Contracts: {contracts(trade['quantity']) if trade else 0}",
    ]
    if trade:
        lines.append(f"Estimated fee: {money(Decimal(trade['fee']) / 10000)} | Total paper cost: {money(Decimal(trade['cost']) / 10000, 4)}")
    lines.append(f'Trade status: {status}')
    if row['status'] != 'traded':
        label = ('Skip reason' if (status.startswith('SKIPPED') or row['status'] in {
                     'unsupported_series', 'event_not_found', 'contract_not_found',
                     'ambiguous_match', 'name_alias_miss'}) else
                 'Shadow reason' if row['status'] == 'shadow_only' else 'Reason')
        lines.append(f"{label}: {text(row.get('reason') or 'No reason recorded')}")
    lines.append('-' * 64)
    return '\n'.join(lines)


def print_summary(row, trade=None):
    # One write keeps each block together; flush for a live redirected console.
    print(format_summary(row, trade), flush=True)
