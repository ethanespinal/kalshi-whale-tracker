from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from database import Database
from dashboard_data import (DashboardFilters, query_trades, recent_activity,
                            recovery_summary)
from recover_alerts import recover
from simulator import PaperTrader
from samples import message
from test_matching import FakeClient, event, market


class HistoricalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.settings = Settings(database=self.path)
        self.db = Database(self.path, self.settings.starting_cash)

    def old_failure(self, key='chat:1'):
        raw = message('NFL: Falcons vs Packers', 'Falcons')
        self.db.ingest(key, raw, time.time() - 3600, 'old-session', 'v1.8',
                       'old-code', strategy_mode='conservative')
        row = self.db.claim()
        self.db.finish(row['id'], 'event_not_found',
                       'Supported series searched; exact event not found',
                       {'original': True})
        return row['id']

    def run_recovery(self, market_row):
        trader = PaperTrader(
            FakeClient([event()], [market_row]), self.db, self.settings,
            session_id='experiment', strategy_version='v1.10',
            code_version='new-code', backend_run_id='recovery-run')
        return recover(self.db, trader, self.settings, 'experiment', 'new-code', 100)

    def test_unchanged_price_recovers_paper_trade_and_preserves_original(self):
        original_id = self.old_failure()
        processed, summary = self.run_recovery(market(yes_ask_dollars='.5000'))
        self.assertEqual(processed, 1)
        self.assertEqual(summary['recovered_paper_trades'], 1)
        trade = self.db.trade_history()[0]
        self.assertEqual((trade['trade_mode'], trade['is_recovered']), ('PAPER', 1))
        self.assertEqual(trade['recovery_original_alert_id'], original_id)
        with self.db.connect() as db:
            original = db.execute('SELECT * FROM alerts WHERE id=?', (original_id,)).fetchone()
            attempt = db.execute('SELECT * FROM recovery_attempts').fetchone()
        self.assertEqual((original['status'], original['strategy_version'], original['code_version']),
                         ('event_not_found', 'v1.8', 'old-code'))
        self.assertEqual(attempt['original_reason'],
                         'Supported series searched; exact event not found')
        self.assertEqual(attempt['recovery_strategy_version'], 'v1.10')
        activity = recent_activity(
            self.path, DashboardFilters(recovery='recovered'))
        self.assertEqual(activity[0]['status'], 'RECOVERED → PAPER FILLED')

    def test_improved_price_recovers_paper_trade(self):
        self.old_failure()
        _, summary = self.run_recovery(market(yes_ask_dollars='.4800'))
        self.assertEqual(summary['recovered_paper_trades'], 1)
        self.assertEqual(self.db.trade_history()[0]['price'], 4800)

    def test_worse_price_is_shadow_only(self):
        self.old_failure()
        _, summary = self.run_recovery(market(yes_ask_dollars='.5100'))
        self.assertEqual(summary['recovered_paper_trades'], 0)
        self.assertEqual(summary['price_worsened'], 1)
        trade = self.db.trade_history()[0]
        self.assertEqual(trade['trade_mode'], 'SHADOW_ONLY')
        self.assertEqual(trade['shadow_reason'], 'Positive slippage above strategy maximum')

    def test_ended_event_is_historical_rematch_only(self):
        self.old_failure()
        ended = market(status='settled', result='yes')
        _, summary = self.run_recovery(ended)
        self.assertEqual(self.db.trade_history(), [])
        self.assertEqual(summary['opportunity_gone'], 1)
        with self.db.connect() as db:
            attempt = db.execute('SELECT * FROM recovery_attempts').fetchone()
        self.assertEqual(attempt['recovery_match_status'], 'MATCHED')
        self.assertIn('no longer open', attempt['recovery_reason'].lower())
        self.assertEqual(summary['newly_matched'], 1)

    def test_second_run_does_not_duplicate_recovery_trade(self):
        self.old_failure()
        self.run_recovery(market())
        processed, summary = self.run_recovery(market())
        self.assertEqual(processed, 0)
        self.assertEqual(summary['recovered_paper_trades'], 1)
        self.assertEqual(len(self.db.trade_history()), 1)

    def test_live_queue_cannot_claim_prepared_recovery_row(self):
        original_id = self.old_failure()
        prepared = self.db.prepare_recovery(
            original_id, 'experiment', 'v1.9', 'conservative',
            'new-code', 'recovery-run')
        self.assertTrue(prepared['process'])
        self.assertIsNone(self.db.claim('listener-run'))
        claimed = self.db.claim_alert(prepared['alert_id'], 'recovery-run')
        self.assertEqual(claimed['recovery_of_alert_id'], original_id)

    def test_dashboard_filter_and_indicator_use_recovery_ledger(self):
        self.old_failure()
        self.run_recovery(market(yes_ask_dollars='.5100'))
        recovered = query_trades(
            self.path, DashboardFilters(recovery='recovered'))
        normal = query_trades(self.path, DashboardFilters(recovery='normal'))
        self.assertEqual(len(recovered), 1)
        self.assertEqual(normal, [])
        activity = recent_activity(
            self.path, DashboardFilters(recovery='recovered'))
        self.assertEqual(activity[0]['status'], 'RECOVERED → PRICE NOW WORSE')
        self.assertEqual(recovery_summary(self.path)['price_worsened'], 1)


if __name__ == '__main__':
    unittest.main()
