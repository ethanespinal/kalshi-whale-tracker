from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from dashboard_data import (DashboardFilters, backend_is_online, dashboard_metrics,
                            filter_options, query_skipped_alerts, query_trades,
                            open_bet_rows, read_operational_state, recent_activity,
                            placed_trade_activity, realized_positions,
                            realized_summary, sort_activity_rows, whale_rows)
from database import Database
from simulator import PaperTrader
from samples import message
from test_matching import FakeClient, event, market


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.settings = Settings(database=self.path)
        self.db = Database(self.path, self.settings.starting_cash)

    def enter(self, key, ticker, trader, session, strategy, result=None,
              strategy_mode='legacy'):
        raw = message(f'NFL: Falcons vs Packers {ticker}', 'Falcons')
        details = {'alert': {'market': f'Market {ticker}', 'trader': trader,
                             'market_type': 'game_winner', 'side': 'Falcons',
                             'price': '.49', 'amount': '1250'},
                   'match': {'event': {'series_ticker': 'KXNFLGAME'},
                             'market': {'title': f'Kalshi market {ticker}'}}}
        self.db.ingest(key, raw, time.time(), session, strategy, 'code123',
                       strategy_mode=strategy_mode)
        alert = self.db.claim()
        self.assertEqual(self.db.enter(
            alert['id'], ticker, 'yes', 50, 5000, 100,
            self.settings.max_exposure, details, stake=250_000,
            session_id=session, strategy_version=strategy,
            code_version='code123', strategy_mode=strategy_mode), 'traded')
        if result:
            self.db.settle(alert['id'], result)

    def test_strategy_code_and_session_persist_on_paper_trade(self):
        raw = message('NFL: Falcons vs Packers', 'Falcons')
        self.db.ingest('chat:1', raw, time.time(), 'receive-session', 'v1.2', 'oldcode')
        trader = PaperTrader(FakeClient([event()], [market()]), self.db, self.settings,
                             session_id='run-session', strategy_version='v1.3',
                             code_version='abc987')
        trader.process_next()
        trade = self.db.trade_history()[0]
        self.assertEqual((trade['session_id'], trade['strategy_version'],
                          trade['strategy_mode'], trade['code_version']),
                         ('run-session', 'v1.3', 'conservative', 'abc987'))

    def test_session_strategy_and_date_filters(self):
        self.enter('a', 'A', 'Alpha', 'session-a', 'v1.3', 'yes', 'all_supported')
        self.enter('b', 'B', 'Beta', 'session-b', 'v1.4', 'no', 'conservative')
        self.assertEqual(len(query_trades(self.path)), 2)
        self.assertEqual(query_trades(
            self.path, DashboardFilters(session_id='session-a'))[0]['trader'], 'Alpha')
        self.assertEqual(query_trades(
            self.path, DashboardFilters(strategy_version='v1.4'))[0]['trader'], 'Beta')
        self.assertEqual(query_trades(
            self.path, DashboardFilters(strategy_mode='conservative'))[0]['trader'], 'Beta')
        future = time.time() + 60
        self.assertEqual(query_trades(self.path, DashboardFilters(start_ts=future)), [])
        options = filter_options(self.path)
        self.assertEqual(set(options['strategy_versions']), {'v1.3', 'v1.4'})
        self.assertEqual(set(options['sessions']), {'session-a', 'session-b'})
        self.assertEqual(set(options['strategy_modes']), {'all_supported', 'conservative'})

    def test_dashboard_metrics_whales_activity_and_skips(self):
        self.enter('a', 'A', 'Alpha', 'session-a', 'v1.3', 'yes')
        self.enter('b', 'B', 'Beta', 'session-a', 'v1.3', 'no')
        records = query_trades(self.path)
        metrics = dashboard_metrics(records, self.settings.starting_cash, 0)
        self.assertEqual(metrics['settled_today'], 2)
        self.assertEqual(metrics['wins'], 1)
        self.assertEqual(metrics['losses'], 1)
        leaders = whale_rows(records, 'P/L')
        self.assertEqual(leaders[0]['Trader'], 'Alpha')
        self.assertGreater(leaders[0]['Net P/L'], leaders[1]['Net P/L'])
        self.assertEqual(sum(row['Trades'] for row in leaders),
                         metrics['settled_trades'])
        self.assertEqual(metrics['wins'] + metrics['losses'] +
                         metrics['pushes'] + metrics['voids'],
                         metrics['settled_trades'])

        self.db.ingest('skip', 'bad alert', time.time(), 'session-a', 'v1.3', 'code123')
        skipped = self.db.claim()
        self.db.finish(skipped['id'], 'skipped', 'Exposure closed')
        skip_rows = query_skipped_alerts(
            self.path, DashboardFilters(session_id='session-a'))
        self.assertEqual(skip_rows[0]['status'], 'SKIPPED')
        statuses = {row['status'] for row in recent_activity(self.path)}
        self.assertTrue({'WIN', 'LOSS', 'SKIPPED'} <= statuses)

    def test_recent_activity_is_placed_only_while_all_activity_is_audit_feed(self):
        self.enter('paper', 'PAPER', 'Alpha', 'session-a', 'v1.8', 'yes')
        raw = message('NFL: Falcons vs Packers shadow', 'Falcons')
        details = {'alert': {'market': 'Shadow market', 'trader': 'Beta',
                             'market_type': 'spread', 'side': 'Falcons',
                             'price': '.50', 'amount': '1250'},
                   'match': {'event': {'series_ticker': 'KXNFLSPREAD'}}}
        self.db.ingest('shadow', raw, time.time(), strategy_mode='conservative')
        alert = self.db.claim()
        self.assertEqual(self.db.enter(
            alert['id'], 'SHADOW', 'yes', 50, 5000, 100,
            self.settings.max_exposure, details, stake=250_000,
            trade_mode='SHADOW_ONLY',
            shadow_reason='Filtered by conservative strategy mode',
            strategy_mode='conservative'), 'shadow_only')
        self.db.ingest('skip-layout', 'bad alert', time.time())
        skipped = self.db.claim()
        self.db.finish(skipped['id'], 'unsupported', 'Unsupported sport/series')

        records = query_trades(self.path)
        top = placed_trade_activity(records)
        audit = recent_activity(self.path)
        self.assertEqual([row['status'] for row in top], ['WIN'])
        self.assertNotIn('SHADOW_ONLY', ' '.join(row['status'] for row in top))
        audit_statuses = {row['status'] for row in audit}
        self.assertIn('WIN', audit_statuses)
        self.assertIn('SHADOW_ONLY OPEN', audit_statuses)
        self.assertIn('SKIPPED (UNSUPPORTED)', audit_statuses)

    def test_wal_allows_repeated_reads_during_backend_writes(self):
        def write_alerts():
            for index in range(40):
                self.db.ingest(f'concurrent:{index}', 'bad', time.time(),
                               'session', 'v1.3', 'code')

        read_operational_state(self.path)
        reads = 1
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(write_alerts)
            while not future.done():
                read_operational_state(self.path)
                query_skipped_alerts(self.path)
                reads += 1
            future.result()
        self.assertGreater(reads, 0)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

    def test_backend_heartbeat_status(self):
        self.db.start_backend('session', 'v1.3', 'abc123',
                              strategy_mode='conservative')
        status = read_operational_state(self.path)['backend_status']
        self.assertTrue(backend_is_online(status, now=status['last_heartbeat'] + 5))
        self.assertFalse(backend_is_online(status, now=status['last_heartbeat'] + 20))
        self.assertEqual(status['strategy_version'], 'v1.3')
        self.assertEqual(status['strategy_mode'], 'conservative')

    def test_open_bets_include_only_active_paper_positions(self):
        self.enter('open', 'OPEN', 'Alpha', 'session-a', 'v1.5')
        rows = open_bet_rows(query_trades(self.path))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kalshi_market'], 'Kalshi market OPEN')
        self.assertEqual(rows[0]['open_exposure_contribution'], 25)
        self.assertGreater(rows[0]['total_paper_cost'], rows[0]['stake'])
        self.db.settle(1, 'yes')
        self.assertEqual(open_bet_rows(query_trades(self.path)), [])

    def test_settled_today_is_canonical_realized_ledger_including_flip_once(self):
        self.enter('flip', 'FLIP', 'Alpha', 'session-a', 'v1.10')
        trade = self.db.trade_history()[0]
        now = time.time()
        exit_price, exit_fee, exit_proceeds = 4800, 100, 239_900
        with self.db.connect() as db:
            db.execute('''UPDATE trades SET portfolio_status='CLOSED',
                portfolio_closed_at=?,portfolio_exit_price=?,portfolio_exit_fee=?,
                portfolio_exit_proceeds=?,portfolio_realized_pnl=? WHERE id=?''',
                       (now, exit_price, exit_fee, exit_proceeds,
                        exit_proceeds - trade['cost'], trade['id']))
        # The whale signal can settle later, but the paper position was already
        # realized at its flip exit and must still appear exactly once.
        self.assertTrue(self.db.settle(trade['id'], 'no'))
        self.enter('normal', 'NORMAL', 'Beta', 'session-a', 'v1.10', 'yes')

        records = query_trades(self.path)
        day_start, day_end = now - 60, now + 60
        rows = realized_positions(records, day_start, day_end)
        summary = realized_summary(records, day_start, day_end)
        metrics = dashboard_metrics(records, self.settings.starting_cash,
                                    day_start, day_end_ts=day_end)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row['net_p_l'] for row in rows),
                         metrics['today_net_p_l'])
        self.assertEqual(summary['net_p_l'], metrics['today_net_p_l'])
        self.assertEqual(sum(row['Trades'] for row in whale_rows(records)),
                         metrics['settled_trades'])
        self.assertEqual(sum(row['result'] == result for row in rows
                             for result in ('WIN', 'LOSS', 'PUSH', 'VOID')), 2)
        flip = next(row for row in rows if row['source'] == 'FLIP EXIT')
        self.assertEqual(flip['net_p_l'],
                         Decimal(exit_proceeds - trade['cost']) / 10000)

    def test_missing_whale_identity_is_visible_as_unknown(self):
        self.enter('unknown', 'UNKNOWN-TICKER', 'Temporary', 'session-a', 'v1.10', 'yes')
        with self.db.connect() as db:
            db.execute("UPDATE alerts SET details='{}',trader=NULL WHERE source_key='unknown'")
        records = query_trades(self.path)
        self.assertEqual(records[0]['trader'], 'UNKNOWN')
        leaders = whale_rows(records)
        self.assertEqual((leaders[0]['Trader'], leaders[0]['Trades']), ('UNKNOWN', 1))

    def test_activity_sorting_handles_open_countdowns_and_closed_rows(self):
        rows = [
            {'opened_time': 3, 'trader': 'Zulu', 'status': 'WIN',
             'effective_resolution_time': '2026-09-27T12:00:00Z'},
            {'opened_time': 2, 'trader': 'Alpha', 'status': 'OPEN',
             'effective_resolution_time': '2026-09-27T11:00:00Z'},
            {'opened_time': 1, 'trader': 'Beta', 'status': 'OPEN',
             'effective_resolution_time': '2026-09-27T10:00:00Z'},
        ]
        self.assertEqual([row['opened_time'] for row in sort_activity_rows(rows)],
                         [3, 2, 1])
        self.assertEqual([row['trader'] for row in sort_activity_rows(
            rows, 'Trader', ascending=True)], ['Alpha', 'Beta', 'Zulu'])
        self.assertEqual([row['trader'] for row in sort_activity_rows(
            rows, 'T-resolve', ascending=True)], ['Beta', 'Alpha', 'Zulu'])
        self.assertEqual([row['trader'] for row in sort_activity_rows(
            rows, 'T-resolve', ascending=False)], ['Zulu', 'Alpha', 'Beta'])


if __name__ == '__main__':
    unittest.main()
