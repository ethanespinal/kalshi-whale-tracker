"""Human-readable and CSV reporting for the persistent paper ledger."""
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json


CSV_FIELDS = [
    'timestamp', 'session_id', 'strategy_version', 'strategy_mode', 'code_version', 'backend_run_id',
    'trade_mode', 'shadow_reason', 'whale_win_rate', 'whale_roi', 'hours_to_close',
    'resolution_time_source', 'effective_resolution_time', 'can_close_early',
    'is_recovered', 'recovery_original_alert_id',
    'event_ticker', 'winner_contract_key', 'conflict_existing', 'conflict_new',
    'conflict_action', 'conflict_decision', 'existing_whale_score', 'new_whale_score',
    'telegram_alert_id', 'trader', 'original_market', 'market_type',
    'whale_side', 'whale_entry', 'simulated_live_entry', 'slippage',
    'kalshi_ticker', 'yes_no_direction', 'simulated_stake', 'contracts',
    'estimated_fee', 'trade_status', 'settlement_result', 'settlement_value',
    'gross_p_l', 'net_p_l', 'roi', 'portfolio_status', 'portfolio_exit_price',
    'portfolio_exit_fee', 'portfolio_exit_proceeds', 'portfolio_realized_p_l',
    'portfolio_close_reason',
]


def _decimal(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _dollars(units):
    return None if units is None else Decimal(units) / 10000


def _source_id(source_key):
    tail = str(source_key).rsplit(':', 1)[-1]
    return tail if tail.isdigit() else str(source_key)


def _original_market(raw_text, alert):
    if alert.get('market'):
        return alert['market']
    for line in (raw_text or '').splitlines():
        if line.strip():
            return line.strip().removeprefix('📌').strip()
    return ''


def report_rows(history):
    """Normalize joined database rows into stable reporting records."""
    records = []
    for row in history:
        try:
            details = json.loads(row.get('details') or '{}')
        except (TypeError, ValueError):
            details = {}
        alert = details.get('alert') or {}
        match = details.get('match') or {}
        event = match.get('event') or {}
        timing = details.get('resolution_timing') or {}
        conflict = details.get('conflict') or {}
        try:
            close_details = json.loads(row.get('portfolio_close_details') or '{}')
        except (TypeError, ValueError):
            close_details = {}
        decision = conflict or close_details
        whale_entry = _decimal(alert.get('price'))
        live_entry = _dollars(row['price'])
        slippage = live_entry - whale_entry if whale_entry is not None else None
        stake_units = row.get('stake') or round(row['quantity'] * row['price'])
        settled = row.get('payout') is not None
        timestamp = datetime.fromtimestamp(row['opened_at'], timezone.utc).isoformat()
        records.append({
            'timestamp': timestamp,
            'session_id': row.get('session_id') or 'legacy',
            'strategy_version': row.get('strategy_version') or 'legacy',
            'strategy_mode': row.get('strategy_mode') or 'legacy',
            'code_version': row.get('code_version') or 'legacy',
            'backend_run_id': row.get('backend_run_id') or 'legacy',
            'trade_mode': row.get('trade_mode') or 'PAPER',
            'shadow_reason': row.get('shadow_reason') or '',
            'whale_win_rate': _decimal(row.get('whale_win_rate') if row.get('whale_win_rate') is not None else alert.get('win_rate')),
            'whale_roi': _decimal(row.get('whale_roi') if row.get('whale_roi') is not None else alert.get('roi')),
            'hours_to_close': _decimal(row.get('hours_to_close') if row.get('hours_to_close') is not None else details.get('hours_to_close')),
            'resolution_time_source': row.get('resolution_time_source') or timing.get('source') or 'Unknown',
            'effective_resolution_time': row.get('effective_resolution_time') or timing.get('timestamp') or '',
            'can_close_early': (bool(row['can_close_early']) if row.get('can_close_early') is not None
                                else timing.get('can_close_early')),
            'is_recovered': bool(row.get('is_recovered')),
            'recovery_original_alert_id': row.get('recovery_original_alert_id'),
            'event_ticker': row.get('event_ticker') or event.get('event_ticker') or '',
            'winner_contract_key': row.get('winner_contract_key') or '',
            'conflict_existing': ' '.join(filter(None, (
                str(decision.get('existing_contract') or '').strip(),
                str(decision.get('existing_direction') or '').upper().strip()))),
            'conflict_new': ' '.join(filter(None, (
                str(decision.get('new_contract') or '').strip(),
                str(decision.get('new_direction') or '').upper().strip()))),
            'conflict_action': decision.get('action') or '',
            'conflict_decision': decision.get('reason') or row.get('portfolio_close_reason') or '',
            'existing_whale_score': _decimal(decision.get('existing_whale_score')),
            'new_whale_score': _decimal(decision.get('new_whale_score')),
            'telegram_alert_id': _source_id(
                row.get('original_source_key') or row.get('source_key', '')),
            'trader': alert.get('trader') or 'Unknown',
            'original_market': _original_market(row.get('raw_text'), alert),
            'market_type': alert.get('market_type') or 'Unknown',
            'market_type_trade_mode': (f"{alert.get('market_type') or 'Unknown'} / "
                                       f"{row.get('trade_mode') or 'PAPER'}"),
            'whale_side': alert.get('side') or 'Unknown',
            'whale_entry': whale_entry,
            'simulated_live_entry': live_entry,
            'slippage': slippage,
            'kalshi_market_title': (match.get('market') or {}).get('title') or
                                   (match.get('market') or {}).get('yes_sub_title') or '',
            'kalshi_ticker': row['ticker'],
            'yes_no_direction': row['side'].upper(),
            'simulated_stake': _dollars(stake_units),
            'contracts': row['quantity'],
            'estimated_fee': _dollars(row['fee']),
            'total_paper_cost': _dollars(row.get('cost')),
            'trade_status': 'SETTLED' if settled else 'OPEN',
            'settlement_result': row.get('final_result') or '',
            'settlement_value': _dollars(row.get('settlement_value')),
            'gross_p_l': _dollars(row.get('gross_pnl')),
            'net_p_l': _dollars(row.get('net_pnl')),
            'roi': Decimal(str(row['roi'])) if row.get('roi') is not None else None,
            'portfolio_status': row.get('portfolio_status') or
                                ('SHADOW' if row.get('trade_mode') == 'SHADOW_ONLY'
                                 else 'SETTLED' if settled else 'OPEN'),
            'portfolio_exit_price': _dollars(row.get('portfolio_exit_price')),
            'portfolio_exit_fee': _dollars(row.get('portfolio_exit_fee')),
            'portfolio_exit_proceeds': _dollars(row.get('portfolio_exit_proceeds')),
            'portfolio_realized_p_l': _dollars(row.get('portfolio_realized_pnl')),
            'portfolio_close_reason': row.get('portfolio_close_reason') or '',
            'series': row.get('series_ticker') or event.get('series_ticker') or 'Unknown',
            '_stake_units': stake_units,
            '_fee_units': row['fee'],
            '_net_units': row.get('net_pnl'),
            '_slippage_units': int(slippage * 10000) if slippage is not None else None,
        })
    return records


def summarize(records):
    settled = [row for row in records if row['trade_status'] == 'SETTLED']
    wins = sum(row['settlement_result'] == 'WIN' for row in settled)
    losses = sum(row['settlement_result'] == 'LOSS' for row in settled)
    decided = wins + losses
    settled_stake = sum(row['_stake_units'] for row in settled)
    net_units = sum((row['_net_units'] or 0) for row in settled)
    whale_prices = [row['whale_entry'] for row in records if row['whale_entry'] is not None]
    live_prices = [row['simulated_live_entry'] for row in records]
    slippages = [row['slippage'] for row in records if row['slippage'] is not None]
    return {
        'total_trades': len(records),
        'settled_trades': len(settled),
        'open_trades': len(records) - len(settled),
        'wins': wins,
        'losses': losses,
        'pushes': sum(row['settlement_result'] == 'PUSH' for row in settled),
        'voids': sum(row['settlement_result'] == 'VOID' for row in settled),
        'win_rate': Decimal(wins) / decided if decided else None,
        'total_staked': _dollars(sum(row['_stake_units'] for row in records)),
        'settled_staked': _dollars(settled_stake),
        'total_fees': _dollars(sum(row['_fee_units'] for row in records)),
        'net_p_l': _dollars(net_units),
        'roi': Decimal(net_units) / settled_stake if settled_stake else None,
        'average_whale_entry': sum(whale_prices, Decimal(0)) / len(whale_prices) if whale_prices else None,
        'average_simulated_entry': sum(live_prices, Decimal(0)) / len(live_prices) if live_prices else None,
        'average_slippage': sum(slippages, Decimal(0)) / len(slippages) if slippages else None,
    }


def slippage_bucket(record):
    units = record['_slippage_units']
    if units is None or units < 200:
        return '0-1 cents'
    if units < 400:
        return '2-3 cents'
    if units < 600:
        return '4-5 cents'
    return '6+ cents'


def whale_win_rate_bucket(record):
    value = record.get('whale_win_rate')
    if value is None:
        return 'Unknown'
    if value < 50:
        return 'under 50%'
    if value < 60:
        return '50-59%'
    if value < 70:
        return '60-69%'
    if value < 80:
        return '70-79%'
    return '80%+'


def whale_roi_bucket(record):
    value = record.get('whale_roi')
    if value is None:
        return 'Unknown'
    if value < 50:
        return 'under 50%'
    if value < 100:
        return '50-99%'
    if value < 150:
        return '100-149%'
    if value < 200:
        return '150-199%'
    return '200%+'


def hours_to_close_bucket(record):
    value = record.get('hours_to_close')
    if value is None:
        return 'Unknown'
    if value < 3:
        return '0-3h'
    if value < 6:
        return '3-6h'
    if value < 12:
        return '6-12h'
    if value <= 24:
        return '12-24h'
    return '24h+'


def breakdown(records, key):
    groups = defaultdict(list)
    for row in records:
        bucket = {'slippage': slippage_bucket,
                  'whale_win_rate_bucket': whale_win_rate_bucket,
                  'whale_roi_bucket': whale_roi_bucket,
                  'hours_to_close_bucket': hours_to_close_bucket}.get(key)
        label = bucket(row) if bucket else row.get(key) or 'Unknown'
        groups[str(label)].append(row)
    return {label: summarize(rows) for label, rows in sorted(groups.items())}


def strategy_comparison(records):
    """Compare the strict portfolio with exact adverse-slippage research cohorts."""
    groups = {
        'Strict strategy trades (slippage <= 0¢)': [
            row for row in records
            if row.get('trade_mode') == 'PAPER' and
            row.get('_slippage_units') is not None and row['_slippage_units'] <= 0],
        '+1¢ signals': [row for row in records if row.get('_slippage_units') == 100],
        '+2¢ signals': [row for row in records if row.get('_slippage_units') == 200],
        '+3¢ signals': [row for row in records if row.get('_slippage_units') == 300],
        'Whale-quality shadow trades': [
            row for row in records
            if row.get('trade_mode') == 'SHADOW_ONLY' and
            'Whale' in (row.get('shadow_reason') or '')],
        'All qualifying shadow signals': [
            row for row in records if row.get('trade_mode') == 'SHADOW_ONLY'],
        'Strategy-mode excluded shadow signals': [
            row for row in records
            if row.get('trade_mode') == 'SHADOW_ONLY' and
            'strategy mode' in (row.get('shadow_reason') or '').lower()],
        'Conflicting-position shadow signals': [
            row for row in records
            if row.get('trade_mode') == 'SHADOW_ONLY' and
            bool(row.get('conflict_decision'))],
        'All qualifying signals': list(records),
    }
    return {label: summarize(rows) for label, rows in groups.items()}


def _money(value):
    return 'n/a' if value is None else f'${value:,.2f}'


def _percent(value):
    return 'n/a' if value is None else f'{value * 100:.1f}%'


def _price(value):
    return 'n/a' if value is None else f'{value * 100:.2f}¢'


def _table(title, groups):
    lines = ['', title, 'Group | Trades | Settled | W-L-P-V | Stake | Fees | Net P/L | ROI']
    lines.append('-' * 88)
    for label, values in groups.items():
        record = (f"{label} | {values['total_trades']} | {values['settled_trades']} | "
                  f"{values['wins']}-{values['losses']}-{values['pushes']}-{values['voids']} | "
                  f"{_money(values['total_staked'])} | {_money(values['total_fees'])} | "
                  f"{_money(values['net_p_l'])} | {_percent(values['roi'])}")
        lines.append(record)
    if not groups:
        lines.append('(no trades)')
    return lines


def whale_performance(records):
    groups = defaultdict(list)
    for row in records:
        groups[row.get('trader') or 'Unknown'].append(row)
    results = []
    for name, rows in groups.items():
        values = summarize(rows)
        # Leaderboard performance is realized performance, so displayed stake
        # and ROI use the same settled-trade population.
        values['total_staked'] = values['settled_staked']
        results.append((name, values))
    results.sort(key=lambda item: (-item[1]['net_p_l'], item[0]))
    return results


def _whale_leaderboard(records):
    results = whale_performance(records)
    lines = ['', 'WHALE PERFORMANCE',
             'Trader | Settled | W-L | Win rate | Stake | Net P/L | ROI | Avg slippage']
    lines.append('-' * 96)
    for name, values in results:
        lines.append(
            f"{name} | {values['settled_trades']} | {values['wins']}-{values['losses']} | "
            f"{_percent(values['win_rate'])} | {_money(values['total_staked'])} | "
            f"{_money(values['net_p_l'])} | {_percent(values['roi'])} | "
            f"{_price(values['average_slippage'])}")
    if not results:
        lines.append('(no trades)')
    return lines


def format_report(records, portfolio=None):
    paper = [row for row in records if row.get('trade_mode') != 'SHADOW_ONLY']
    totals = summarize(paper)
    portfolio_open = sum(row.get('portfolio_status') == 'OPEN' for row in paper)
    conflict_blocks = sum(
        row.get('trade_mode') == 'SHADOW_ONLY' and
        bool(row.get('conflict_decision'))
        for row in records)
    flips = sum(row.get('portfolio_status') == 'CLOSED' for row in records)
    reversal_exit_fees = sum(
        (row.get('portfolio_exit_fee') or Decimal(0))
        for row in paper if row.get('portfolio_status') == 'CLOSED')
    realized_rows = [row for row in paper
                     if row.get('portfolio_status') in ('CLOSED', 'SETTLED')]
    portfolio_realized = sum(
        (row.get('portfolio_realized_p_l') if row.get('portfolio_status') == 'CLOSED'
         else row.get('net_p_l')) or Decimal(0)
        for row in realized_rows)
    realized_stake = sum(row['_stake_units'] for row in realized_rows)
    portfolio_roi = (portfolio_realized / (Decimal(realized_stake) / 10000)
                     if realized_stake else None)
    portfolio = portfolio or {}
    lines = [
        'FILTERED PAPER PORTFOLIO',
        f"Starting bankroll: {_money(_dollars(portfolio.get('starting_bankroll')))}",
        f"Current realized bankroll: {_money(_dollars(portfolio.get('realized_bankroll')))}",
        f"Settled trades: {totals['settled_trades']}",
        f"Open trades: {portfolio_open}",
        f"Wins: {totals['wins']}",
        f"Losses: {totals['losses']}",
        f"Pushes: {totals['pushes']}",
        f"Voids: {totals['voids']}",
        f"Win rate: {_percent(totals['win_rate'])}",
        f"Total amount staked: {_money(totals['total_staked'])}",
        f"Settled amount staked: {_money(totals['settled_staked'])}",
        f"Total estimated entry fees: {_money(totals['total_fees'])}",
        f"Reversal exit fees: {_money(reversal_exit_fees)}",
        f"Net P/L (realized portfolio): {_money(portfolio_realized)}",
        f"Overall ROI (realized positions): {_percent(portfolio_roi)}",
        f"Average whale entry price: {_price(totals['average_whale_entry'])}",
        f"Average simulated entry price: {_price(totals['average_simulated_entry'])}",
        f"Average slippage: {_price(totals['average_slippage'])}",
        f"Signals blocked by conflicting open position: {conflict_blocks}",
        f"Opposite-side paper reversals: {flips}",
        f"Current open exposure: {_money(_dollars(portfolio.get('open_exposure')))}",
        f"Peak open exposure: {_money(_dollars(portfolio.get('peak_open_exposure')))}",
    ]
    lines += _table('STRATEGY COMPARISON', strategy_comparison(records))
    lines += _whale_leaderboard(paper)
    lines += _table('ALL SIGNALS BY STRATEGY MODE', breakdown(records, 'strategy_mode'))
    lines += _table('ALL SIGNALS BY MARKET TYPE', breakdown(records, 'market_type'))
    lines += _table('MARKET TYPE BY PAPER/SHADOW',
                    breakdown(records, 'market_type_trade_mode'))
    lines += _table('ALL SIGNALS BY SPORT/SERIES', breakdown(records, 'series'))
    bucket_groups = breakdown(records, 'slippage')
    ordered = {key: bucket_groups.get(key, summarize([]))
               for key in ('0-1 cents', '2-3 cents', '4-5 cents', '6+ cents')}
    lines += _table('ALL SIGNALS BY SLIPPAGE', ordered)
    for title, key, order in (
        ('ALL SIGNALS BY WHALE WIN RATE', 'whale_win_rate_bucket',
         ('under 50%', '50-59%', '60-69%', '70-79%', '80%+')),
        ('ALL SIGNALS BY WHALE ROI', 'whale_roi_bucket',
         ('under 50%', '50-99%', '100-149%', '150-199%', '200%+')),
        ('ALL SIGNALS BY HOURS TO RESOLUTION', 'hours_to_close_bucket',
         ('0-3h', '3-6h', '6-12h', '12-24h', '24h+')),
    ):
        groups = breakdown(records, key)
        ordered_groups = {label: groups.get(label, summarize([])) for label in order}
        if 'Unknown' in groups:
            ordered_groups['Unknown'] = groups['Unknown']
        lines += _table(title, ordered_groups)
    return '\n'.join(lines)


def export_csv(records, path):
    """Write typed decimal values with a BOM so Excel opens UTF-8 cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in records:
            writer.writerow({name: '' if row.get(name) is None else row.get(name)
                             for name in CSV_FIELDS})
    return path
