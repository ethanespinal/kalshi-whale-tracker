"""Read-only SQLite queries and aggregations for the Streamlit dashboard."""
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3

from alert_parser import parse_alert
from reporting import breakdown, report_rows, strategy_comparison, summarize


@dataclass(frozen=True)
class DashboardFilters:
    strategy_version: str | None = None
    session_id: str | None = None
    start_ts: float | None = None
    end_ts: float | None = None
    trade_mode: str | None = None
    strategy_mode: str | None = None
    recovery: str | None = None


@contextmanager
def read_connection(path):
    """Open a bounded, query-only connection that can coexist with WAL writes."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA busy_timeout=5000')
        db.execute('PRAGMA query_only=ON')
        yield db
    finally:
        db.close()


def _where(filters, alias, time_column):
    clauses, params = [], []
    if filters.strategy_version:
        clauses.append(f'{alias}.strategy_version=?')
        params.append(filters.strategy_version)
    if filters.session_id:
        clauses.append(f'{alias}.session_id=?')
        params.append(filters.session_id)
    if filters.strategy_mode:
        clauses.append(f'{alias}.strategy_mode=?')
        params.append(filters.strategy_mode)
    if filters.recovery == 'recovered':
        clauses.append(f"{alias}.{'is_recovered' if alias == 't' else 'recovery_of_alert_id'} IS NOT NULL"
                       if alias != 't' else f'{alias}.is_recovered=1')
    elif filters.recovery == 'normal':
        clauses.append(f"{alias}.recovery_of_alert_id IS NULL" if alias != 't'
                       else f'{alias}.is_recovered=0')
    if filters.start_ts is not None:
        clauses.append(f'{alias}.{time_column}>=?')
        params.append(filters.start_ts)
    if filters.end_ts is not None:
        clauses.append(f'{alias}.{time_column}<?')
        params.append(filters.end_ts)
    return (' WHERE ' + ' AND '.join(clauses)) if clauses else '', params


def filter_options(path):
    with read_connection(path) as db:
        strategies = [row[0] for row in db.execute(
            'SELECT DISTINCT strategy_version FROM trades ORDER BY strategy_version')]
        sessions = [row[0] for row in db.execute(
            'SELECT session_id FROM trades GROUP BY session_id ORDER BY MAX(opened_at) DESC')]
        modes = [row[0] for row in db.execute(
            'SELECT DISTINCT strategy_mode FROM trades ORDER BY strategy_mode')]
        status = db.execute('SELECT session_id,strategy_version FROM backend_status WHERE id=1').fetchone()
        if status:
            if status['strategy_version'] not in strategies:
                strategies.append(status['strategy_version'])
            if status['session_id'] not in sessions:
                sessions.insert(0, status['session_id'])
    return {'strategy_versions': strategies, 'sessions': sessions,
            'strategy_modes': modes}


def query_trades(path, filters=DashboardFilters()):
    where, params = _where(filters, 't', 'opened_at')
    with read_connection(path) as db:
        rows = [dict(row) for row in db.execute(f'''SELECT
            t.*, a.source_key, a.raw_text, a.received_at, a.details,
            a.trader AS persisted_trader,
            original.source_key AS original_source_key
            FROM trades t JOIN alerts a ON a.id=t.alert_id
            LEFT JOIN alerts original ON original.id=t.recovery_original_alert_id
            {where} ORDER BY t.opened_at DESC''', params)]
    records = report_rows(rows)
    for record, source in zip(records, rows):
        if record.get('trader') in (None, '', 'Unknown'):
            record['trader'] = (source.get('persisted_trader') or 'UNKNOWN').strip() or 'UNKNOWN'
        record['_trade_id'] = source['id']
        record['_opened_at'] = source['opened_at']
        record['_closed_at'] = source['closed_at']
        record['_portfolio_closed_at'] = source.get('portfolio_closed_at')
        record['_portfolio_realized_units'] = source.get('portfolio_realized_pnl')
        record['is_recovered'] = bool(source.get('is_recovered'))
        record['recovery_original_alert_id'] = source.get('recovery_original_alert_id')
    if filters.trade_mode:
        records = [row for row in records if row['trade_mode'] == filters.trade_mode]
    return records


def _decimal(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _alert_record(row):
    try:
        details = json.loads(row.get('details') or '{}')
    except (TypeError, ValueError):
        details = {}
    alert = details.get('alert') or {}
    if not alert and row.get('parsed_alert'):
        try:
            alert = json.loads(row['parsed_alert'])
        except (TypeError, ValueError):
            alert = {}
    if not alert:
        try:
            alert = parse_alert(row.get('raw_text') or '') or {}
        except (TypeError, ValueError):
            alert = {}
    match = details.get('match') or {}
    timing = details.get('resolution_timing') or {}
    quote = details.get('quote') or {}
    side = match.get('side')
    live = _decimal(quote.get(f'{side}_ask_dollars')) if side else None
    whale = _decimal(alert.get('price'))
    original = alert.get('market')
    if not original:
        original = next((line.strip().removeprefix('📌').strip()
                         for line in (row.get('raw_text') or '').splitlines()
                         if line.strip()), '')
    status = row['status'].upper()
    if row['status'] in ('unmatched', 'ambiguous', 'unsupported', 'line_not_found', 'stale',
                         'unsupported_series', 'event_not_found', 'contract_not_found',
                         'ambiguous_match', 'name_alias_miss'):
        status = f'SKIPPED ({status})'
    recovered = row.get('recovery_of_alert_id') is not None
    if recovered:
        status = f'RECOVERED → {status}'
    return {
        'time': row['updated_at'], 'opened_time': row['received_at'],
        'trader': alert.get('trader') or 'Unknown',
        'original_market': original, 'market_type': alert.get('market_type') or 'Unknown',
        'whale_side': alert.get('side') or 'Unknown', 'whale_price': whale,
        'our_entry_price': live, 'slippage': live - whale if live is not None and whale is not None else None,
        'stake': Decimal(0), 'status': status, 'result_p_l': row.get('reason') or '',
        'session_id': row.get('session_id') or 'legacy',
        'strategy_version': row.get('strategy_version') or 'legacy',
        'strategy_mode': row.get('strategy_mode') or 'legacy',
        'code_version': row.get('code_version') or 'legacy',
        'backend_run_id': row.get('backend_run_id') or 'legacy',
        'trade_mode': '', 'shadow_reason': '',
        'whale_win_rate': _decimal(alert.get('win_rate')),
        'whale_roi': _decimal(alert.get('roi')),
        'hours_to_close': _decimal(details.get('hours_to_close')),
        'resolution_time_source': timing.get('source') or 'Unknown',
        'effective_resolution_time': timing.get('timestamp') or '',
        'can_close_early': timing.get('can_close_early'),
        'is_open': False, 'is_closed': False,
        'is_recovered': recovered,
        'recovery_original_alert_id': row.get('recovery_of_alert_id'),
    }


def query_skipped_alerts(path, filters=DashboardFilters(), limit=None):
    where, params = _where(filters, 'a', 'received_at')
    extra = "a.status NOT IN ('traded','shadow_only','pending','processing')"
    where = f'{where} AND {extra}' if where else f' WHERE {extra}'
    sql = f'SELECT * FROM alerts a {where} ORDER BY updated_at DESC'
    if limit is not None:
        sql += ' LIMIT ?'
        params.append(limit)
    with read_connection(path) as db:
        return [_alert_record(dict(row)) for row in db.execute(sql, params)]


def trade_activity(records):
    rows = []
    for row in records:
        settled = row['trade_status'] == 'SETTLED'
        pnl = row['net_p_l']
        rows.append({
            'time': (row.get('_portfolio_closed_at') or row['_closed_at']
                     if row.get('portfolio_status') == 'CLOSED' or settled
                     else row['_opened_at']),
            'opened_time': row['_opened_at'],
            'trader': row['trader'], 'original_market': row['original_market'],
            'market_type': row['market_type'], 'whale_side': row['whale_side'],
            'whale_price': row['whale_entry'],
            'our_entry_price': row['simulated_live_entry'], 'slippage': row['slippage'],
            'stake': row['simulated_stake'],
            'status': (f"SHADOW_ONLY {row['settlement_result']}" if settled else 'SHADOW_ONLY OPEN')
                      if row['trade_mode'] == 'SHADOW_ONLY'
                      else row['settlement_result'] if settled else 'OPEN',
            'result_p_l': pnl, 'session_id': row['session_id'],
            'strategy_version': row['strategy_version'], 'code_version': row['code_version'],
            'strategy_mode': row['strategy_mode'],
            'backend_run_id': row['backend_run_id'],
            'trade_mode': row['trade_mode'], 'shadow_reason': row['shadow_reason'],
            'whale_win_rate': row['whale_win_rate'], 'whale_roi': row['whale_roi'],
            'hours_to_close': row['hours_to_close'],
            'resolution_time_source': row['resolution_time_source'],
            'effective_resolution_time': row['effective_resolution_time'],
            'can_close_early': row['can_close_early'],
            'is_open': (not settled and row.get('portfolio_status') != 'CLOSED'),
            'is_closed': (settled or row.get('portfolio_status') == 'CLOSED'),
            'is_recovered': row.get('is_recovered', False),
            'recovery_original_alert_id': row.get('recovery_original_alert_id'),
        })
        if row.get('is_recovered'):
            if (row.get('trade_mode') == 'SHADOW_ONLY' and
                    'positive slippage' in (row.get('shadow_reason') or '').lower()):
                rows[-1]['status'] = 'RECOVERED → PRICE NOW WORSE'
            elif row.get('trade_mode') == 'PAPER' and not settled:
                rows[-1]['status'] = 'RECOVERED → PAPER FILLED'
            else:
                rows[-1]['status'] = f"RECOVERED → {rows[-1]['status']}"
        if row.get('portfolio_status') == 'CLOSED':
            result = row.get('settlement_result') or 'AWAITING SETTLEMENT'
            rows[-1]['status'] = f'FLIPPED OUT — {result}'
            rows[-1]['result_p_l'] = row.get('portfolio_realized_p_l')
        elif row.get('conflict_action') == 'flip' and row.get('trade_mode') == 'PAPER':
            rows[-1]['status'] = ('REVERSAL OPEN' if not settled
                                  else f"REVERSAL {row['settlement_result']}")
        if (row.get('trade_mode') == 'SHADOW_ONLY' and row.get('conflict_decision')):
            prefix = 'RECOVERED → ' if row.get('is_recovered') else ''
            rows[-1]['status'] = prefix + 'SHADOW_ONLY — CONFLICTING OPEN POSITION'
            existing = row.get('conflict_existing') or 'existing outcome'
            new = row.get('conflict_new') or 'new outcome'
            rows[-1]['result_p_l'] = f'Existing: {existing} | New: {new}'
    return rows


def open_bet_rows(records):
    """Project active paper positions for the dashboard; exclude shadow and settled rows."""
    rows = []
    for row in records:
        if row['trade_mode'] != 'PAPER' or row.get('portfolio_status') != 'OPEN':
            continue
        rows.append({
            'trader': row['trader'],
            'original_market': row['original_market'],
            'kalshi_market': row['kalshi_market_title'],
            'market_type': row['market_type'],
            'side': row['whale_side'],
            'direction': row['yes_no_direction'],
            'whale_entry': row['whale_entry'],
            'our_entry': row['simulated_live_entry'],
            'slippage': row['slippage'],
            'stake': row['simulated_stake'],
            'contracts': row['contracts'],
            'estimated_fee': row['estimated_fee'],
            'total_paper_cost': row['total_paper_cost'],
            'open_exposure_contribution': row['simulated_stake'],
            'strategy_version': row['strategy_version'],
            'strategy_mode': row['strategy_mode'],
            'session': row['session_id'],
            'time_opened': row['_opened_at'],
            'expected_resolution_time': row['effective_resolution_time'],
            'resolution_time_source': row['resolution_time_source'],
        })
    return rows


def recent_activity(path, filters=DashboardFilters(), limit=100):
    rows = trade_activity(query_trades(path, filters))
    rows += query_skipped_alerts(path, filters, limit=limit)
    return sort_activity_rows(rows, 'Time opened', ascending=False)[:limit]


def placed_trade_activity(records, limit=100):
    """Return only actual PAPER ledger activity for the concise top panel."""
    paper = [row for row in records if row.get('trade_mode') == 'PAPER']
    return sort_activity_rows(trade_activity(paper), 'Time opened', ascending=False)[:limit]


def _resolution_epoch(row):
    value = row.get('effective_resolution_time')
    if not value:
        return float('inf')
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return float('inf')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _activity_state(row):
    if row.get('is_open'):
        return 'active'
    if row.get('is_closed'):
        return 'closed'
    status = str(row.get('status') or '').upper()
    if 'OPEN' in status and 'FLIPPED OUT' not in status:
        return 'active'
    if any(label in status for label in
           ('WIN', 'LOSS', 'PUSH', 'VOID', 'SETTLED', 'FLIPPED OUT')):
        return 'closed'
    return 'other'


def sort_activity_rows(rows, sort_by='Time opened', ascending=False):
    """Sort dashboard activity without changing lifecycle or stored values."""
    if sort_by == 'Trader':
        return sorted(rows, key=lambda row: (
            str(row.get('trader') or '').casefold(), row.get('opened_time') or 0),
            reverse=not ascending)
    if sort_by == 'T-resolve':
        def resolution_key(row):
            state = _activity_state(row)
            if ascending:
                return ({'active': 0, 'closed': 1, 'other': 2}[state],
                        _resolution_epoch(row))
            return ({'closed': 0, 'active': 1, 'other': 2}[state],
                    -_resolution_epoch(row))
        return sorted(rows, key=resolution_key)
    return sorted(rows, key=lambda row: row.get('opened_time') or row.get('time') or 0,
                  reverse=not ascending)


def read_operational_state(path):
    with read_connection(path) as db:
        setting = db.execute("SELECT value FROM settings WHERE key='starting_cash'").fetchone()
        status = db.execute('SELECT * FROM backend_status WHERE id=1').fetchone()
        open_exposure = db.execute('''SELECT COALESCE(SUM(stake),0) FROM trades
            WHERE portfolio_status='OPEN' AND trade_mode='PAPER' ''').fetchone()[0]
        open_trades = db.execute(
            """SELECT COUNT(*) FROM trades WHERE portfolio_status='OPEN'
            AND trade_mode='PAPER'""").fetchone()[0]
        realized = db.execute('''SELECT COALESCE(SUM(CASE
            WHEN portfolio_status='CLOSED' THEN portfolio_realized_pnl
            WHEN portfolio_status='SETTLED' THEN net_pnl ELSE 0 END),0)
            FROM trades WHERE trade_mode='PAPER' ''').fetchone()[0]
    return {'starting_bankroll': setting[0] if setting else 0,
            'backend_status': dict(status) if status else None,
            'open_exposure': open_exposure, 'open_trades': open_trades,
            'realized_pnl': realized}


def recovery_summary(path):
    """Read aggregate outcomes without coupling the dashboard to the worker."""
    with read_connection(path) as db:
        exists = db.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='recovery_attempts'""").fetchone()
        if not exists:
            return {'historical_alerts_reconsidered': 0, 'newly_matched': 0,
                    'recovered_paper_trades': 0, 'price_worsened': 0,
                    'opportunity_gone': 0, 'still_unmatched': 0}
        counts = dict(db.execute('''SELECT recovery_outcome,COUNT(*)
            FROM recovery_attempts GROUP BY recovery_outcome''').fetchall())
        total = db.execute('SELECT COUNT(*) FROM recovery_attempts').fetchone()[0]
        matched = db.execute("""SELECT COUNT(*) FROM recovery_attempts
            WHERE recovery_match_status IN ('matched','stale','MATCHED')""").fetchone()[0]
    return {'historical_alerts_reconsidered': total, 'newly_matched': matched,
            'recovered_paper_trades': counts.get('paper_filled', 0),
            'price_worsened': counts.get('price_now_worse', 0),
            'opportunity_gone': counts.get('event_gone', 0),
            'still_unmatched': counts.get('still_unmatched', 0)}


def backend_is_online(status, now=None, offline_after=15):
    if not status:
        return False
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    return now - status['last_heartbeat'] <= offline_after


def _outcome(row, net_p_l, gross_p_l):
    result = str(row.get('settlement_result') or '').upper()
    if result in ('WIN', 'LOSS', 'PUSH', 'VOID'):
        return result
    if gross_p_l is not None:
        return 'WIN' if gross_p_l > 0 else 'LOSS' if gross_p_l < 0 else 'PUSH'
    return 'WIN' if net_p_l > 0 else 'LOSS' if net_p_l < 0 else 'PUSH'


def realized_position(row):
    """Return the one portfolio-realization event represented by a trade row."""
    paper = row.get('trade_mode') == 'PAPER'
    status = row.get('portfolio_status')
    if paper and status == 'CLOSED':
        realized_at = row.get('_portfolio_closed_at')
        net_p_l = row.get('portfolio_realized_p_l')
        exit_fee = row.get('portfolio_exit_fee') or Decimal(0)
        gross_p_l = None
        if row.get('portfolio_exit_proceeds') is not None:
            gross_p_l = (row['portfolio_exit_proceeds'] + exit_fee -
                         row['simulated_stake'])
        fees = (row.get('estimated_fee') or Decimal(0)) + exit_fee
        value = row.get('portfolio_exit_price')
        source = 'RECOVERED FLIP EXIT' if row.get('is_recovered') else 'FLIP EXIT'
    elif ((paper and status == 'SETTLED') or
          (not paper and row.get('trade_status') == 'SETTLED')):
        realized_at = row.get('_portfolio_closed_at') or row.get('_closed_at')
        net_p_l = row.get('net_p_l')
        gross_p_l = row.get('gross_p_l')
        fees = row.get('estimated_fee') or Decimal(0)
        value = row.get('settlement_value')
        if paper:
            source = 'RECOVERED' if row.get('is_recovered') else 'NORMAL'
        else:
            source = 'RECOVERED SHADOW' if row.get('is_recovered') else 'SHADOW'
    else:
        return None
    if realized_at is None or net_p_l is None:
        return None
    trader = str(row.get('trader') or '').strip() or 'UNKNOWN'
    return {
        'settled_time': realized_at,
        'trader': trader,
        'market': row.get('original_market') or row.get('kalshi_market_title') or 'Unknown',
        'side': row.get('whale_side') or row.get('yes_no_direction') or 'Unknown',
        'entry': row.get('simulated_live_entry'),
        'exit/settlement value': value,
        'stake': row.get('simulated_stake') or Decimal(0),
        'fees': fees,
        'result': _outcome(row, net_p_l, gross_p_l),
        'gross_p_l': gross_p_l,
        'net_p_l': net_p_l,
        'source': source,
        '_record': row,
    }


def realized_positions(records, start_ts=None, end_ts=None):
    rows = []
    for record in records:
        row = realized_position(record)
        if row is None:
            continue
        if start_ts is not None and row['settled_time'] < start_ts:
            continue
        if end_ts is not None and row['settled_time'] >= end_ts:
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: row['settled_time'], reverse=True)


def realized_summary(records, start_ts=None, end_ts=None):
    rows = realized_positions(records, start_ts, end_ts)
    counts = {name: sum(row['result'] == name for row in rows)
              for name in ('WIN', 'LOSS', 'PUSH', 'VOID')}
    stake = sum((row['stake'] for row in rows), Decimal(0))
    net = sum((row['net_p_l'] for row in rows), Decimal(0))
    gross = sum((row['gross_p_l'] or Decimal(0) for row in rows), Decimal(0))
    fees = sum((row['fees'] for row in rows), Decimal(0))
    decided = counts['WIN'] + counts['LOSS']
    return {
        'rows': rows, 'settled_trades': len(rows),
        'wins': counts['WIN'], 'losses': counts['LOSS'],
        'pushes': counts['PUSH'], 'voids': counts['VOID'],
        'win_rate': Decimal(counts['WIN']) / decided if decided else None,
        'settled_staked': stake, 'gross_p_l': gross,
        'total_fees': fees, 'net_p_l': net,
        'roi': net / stake if stake else None,
    }


def dashboard_metrics(records, starting_bankroll, day_start_ts,
                      portfolio_realized_units=None, day_end_ts=None):
    totals = realized_summary(records)
    today_totals = realized_summary(records, day_start_ts, day_end_ts)
    entries = summarize(records)
    realized_units = portfolio_realized_units
    if realized_units is None:
        realized_units = int(totals['net_p_l'] * 10000)
    return {
        **{key: value for key, value in entries.items()
           if key not in ('settled_trades', 'open_trades', 'wins', 'losses',
                          'pushes', 'voids', 'win_rate', 'net_p_l', 'roi')},
        **{key: totals[key] for key in ('settled_trades', 'wins', 'losses',
                                       'pushes', 'voids', 'win_rate', 'net_p_l', 'roi')},
        'open_trades': sum(
            (row.get('portfolio_status') == 'OPEN' if row.get('trade_mode') == 'PAPER'
             else row.get('trade_status') != 'SETTLED') for row in records),
        'paper_bankroll': Decimal(starting_bankroll) / 10000,
        'realized_bankroll': Decimal(starting_bankroll + realized_units) / 10000,
        'today_net_p_l': today_totals['net_p_l'],
        'today_roi': today_totals['roi'],
        'settled_today': today_totals['settled_trades'],
    }


def whale_rows(records, sort_by='Net P/L'):
    groups = defaultdict(list)
    for row in records:
        groups[str(row.get('trader') or '').strip() or 'UNKNOWN'].append(row)
    rows = []
    for trader, group in groups.items():
        values = realized_summary(group)
        rows.append({
            'Trader': trader, 'Trades': values['settled_trades'],
            'Wins': values['wins'], 'Losses': values['losses'],
            'Pushes': values['pushes'], 'Voids': values['voids'],
            'Win rate': values['win_rate'], 'Total stake': values['settled_staked'],
            'Net P/L': values['net_p_l'], 'ROI': values['roi'],
            'Average slippage': summarize(group)['average_slippage'],
        })
    key = {'P/L': 'Net P/L', 'ROI': 'ROI', 'Trade count': 'Trades',
           'Win rate': 'Win rate'}.get(sort_by, 'Net P/L')
    return sorted(rows, key=lambda row: (row[key] is not None, row[key]), reverse=True)


def grouped_rows(records, key):
    rows = []
    for label, values in breakdown(records, key).items():
        rows.append({
            'Group': label, 'Trades': values['total_trades'],
            'Settled': values['settled_trades'], 'Wins': values['wins'],
            'Losses': values['losses'], 'Win rate': values['win_rate'],
            'Stake': values['total_staked'], 'Fees': values['total_fees'],
            'Net P/L': values['net_p_l'], 'ROI': values['roi'],
        })
    return rows


def comparison_rows(records):
    rows = []
    for label, values in strategy_comparison(records).items():
        rows.append({'Portfolio': label, 'Trades': values['total_trades'],
                     'Settled': values['settled_trades'], 'Wins': values['wins'],
                     'Losses': values['losses'], 'Win rate': values['win_rate'],
                     'Stake': values['total_staked'], 'Fees': values['total_fees'],
                     'Net P/L': values['net_p_l'], 'ROI': values['roi']})
    return rows


def conflicting_signal_count(records):
    return sum(
        row.get('trade_mode') == 'SHADOW_ONLY' and
        bool(row.get('conflict_decision'))
        for row in records)
