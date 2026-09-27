from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from database import Database
from kalshi import LookupUnavailable
from simulator import PaperTrader, quote_order
from samples import message
from test_matching import FakeClient, event, market


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.settings = Settings(database=self.path)
        self.db = Database(self.path, self.settings.starting_cash)
        self.client = FakeClient([event()], [market()])
        self.trader = PaperTrader(self.client, self.db, self.settings)

    def ingest(self, key='chat:1', age=0, text=None):
        return self.db.ingest(key, text or message('NFL: Falcons vs Packers', 'Falcons'), time.time() - age)

    def status(self):
        with self.db.connect() as db:
            return dict(db.execute('SELECT * FROM alerts ORDER BY id DESC').fetchone())

    def test_end_to_end_entry_replay_and_restart(self):
        self.assertTrue(self.ingest())
        self.assertFalse(self.ingest())
        self.assertTrue(self.trader.process_next())
        self.assertFalse(self.trader.process_next())
        report = self.db.report()
        self.assertEqual(report['alerts'], {'traded': 1})
        self.assertEqual(len(report['trades']), 1)
        trade = report['trades'][0]
        self.assertEqual(trade['stake'], self.settings.fixed_stake)
        self.assertEqual(trade['cost'], trade['stake'] + trade['fee'])
        self.assertEqual(trade['price'], 5000)
        self.assertIn(('market', 'CONTRACT'), self.client.calls)
        reopened = Database(self.path, 1)
        self.assertFalse(reopened.ingest('chat:1', 'same', time.time()))
        self.assertEqual(reopened.report(), report)

    def test_stale_and_invalid_alerts_never_call_network(self):
        self.ingest(age=301)
        self.trader.process_next()
        self.assertEqual(self.status()['status'], 'skipped')
        self.ingest(key='bad', text='bad message')
        self.trader.process_next()
        self.assertEqual(self.status()['status'], 'invalid')
        self.assertEqual(self.client.calls, [])

    def test_unmatched_audited(self):
        self.client.events = []
        self.ingest()
        self.trader.process_next()
        self.assertEqual(self.status()['status'], 'NO_EVENTS_RETURNED')
        self.assertEqual(self.db.report()['trades'], [])

    def test_transient_error_is_persisted_for_retry(self):
        self.client.recent_series_events = lambda _: (_ for _ in ()).throw(LookupUnavailable('cooldown'))
        self.ingest()
        self.trader.process_next()
        self.assertEqual(self.status()['status'], 'retryable')
        self.assertFalse(self.trader.process_next())

    def test_retry_after_restart_and_attempt_limit(self):
        self.client.recent_series_events = lambda _: (_ for _ in ()).throw(LookupUnavailable('incomplete pagination'))
        self.ingest()
        for attempt in range(3):
            with self.db.connect() as db:
                db.execute('UPDATE alerts SET next_attempt=0')
            trader = PaperTrader(self.client, Database(self.path, 1), self.settings)
            trader.process_next()
        self.assertEqual(self.status()['status'], 'error')
        self.assertEqual(self.status()['attempts'], 3)
        self.assertEqual(self.db.report()['trades'], [])

    def test_interrupted_claim_recovers_but_never_trades_stale_alert(self):
        self.ingest(age=1000)
        self.db.claim()
        with self.db.connect() as db:
            db.execute('UPDATE alerts SET updated_at=?', (time.time() - 601,))
        self.assertTrue(self.trader.process_next())
        self.assertEqual(self.status()['status'], 'skipped')
        self.assertEqual(self.client.calls, [])

    def test_maximum_position_count_bounds_settlement_work(self):
        self.ingest()
        self.trader.process_next()
        self.client.markets = [market(ticker='SECOND')]
        self.trader.settings = replace(self.settings, max_open_positions=1)
        self.ingest('chat:2')
        self.trader.process_next()
        self.assertEqual(self.status()['reason'], 'open position limit')

    def test_closed_missing_notional_and_subunit_liquidity(self):
        for change in ({'close_time': '2000-01-01T00:00:00Z'},
                       {'close_time': 'nonsense'}, {'notional_value_dollars': None},
                       {'yes_ask_size_fp': '0.9'}, {'yes_ask_dollars': '1.0000'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                quote_order(market(**change), 'yes', '.50', self.settings)

    def test_positive_slippage_is_shadow_but_bad_quotes_still_skip(self):
        self.client.markets = [market(yes_ask_dollars='.54')]
        self.ingest(key='positive-slippage')
        self.trader.process_next()
        trade = self.db.report()['trades'][0]
        self.assertEqual(trade['trade_mode'], 'SHADOW_ONLY')
        self.assertEqual(trade['shadow_reason'], 'Positive slippage above strategy maximum')

        for i, change in enumerate(({'yes_ask_size_fp': '0'},
                                    {'yes_ask_dollars': 'NaN'}, {'status': 'closed'})):
            self.client.markets = [market(**change)]
            self.ingest(key=str(i))
            self.trader.process_next()
        self.assertEqual(len(self.db.report()['trades']), 1)

    def test_fresh_quote_revalidated(self):
        self.client.market = lambda _: market(event_ticker='WRONG')
        self.ingest()
        self.trader.process_next()
        self.assertEqual(self.status()['status'], 'skipped')

    def test_no_ask_price_and_bid_size_complement(self):
        m = market(no_ask_dollars='.52', no_ask_size_fp=None,
                   yes_bid_dollars='.48', yes_bid_size_fp='100')
        quantity, price, _ = quote_order(m, 'no', '.50', self.settings)
        self.assertEqual(price, 5200)
        self.assertEqual(quantity * price, self.settings.fixed_stake)
        m['yes_bid_dollars'] = '.49'
        with self.assertRaises(ValueError):
            quote_order(m, 'no', '.50', self.settings)

    def test_risk_limits_and_one_open_contract(self):
        self.ingest()
        self.trader.process_next()
        self.ingest('chat:2')
        self.trader.process_next()
        self.assertEqual(len(self.db.report()['trades']), 1)
        self.assertEqual(self.status()['reason'], 'contract already held')
        self.client.markets = [market(ticker='SECOND')]
        self.trader.settings = replace(self.settings, max_exposure=1)
        self.ingest('chat:3')
        self.trader.process_next()
        self.assertEqual(self.status()['reason'], 'Exposure closed; waiting for positions to settle')

    def test_settlement_payout_and_idempotence(self):
        self.ingest()
        self.trader.process_next()
        trade = self.db.report()['trades'][0]
        self.client.markets[0].update(status='settled', result='yes')
        self.assertEqual(self.trader.settle(), 1)
        self.assertEqual(self.trader.settle(), 0)
        report = self.db.report()
        self.assertEqual(report['open_cost_dollars'], 0)
        self.assertEqual(report['realized_pnl_dollars'], (trade['quantity'] * 10000 - trade['cost']) / 10000)
        settled = report['trades'][0]
        self.assertEqual(settled['final_result'], 'WIN')
        self.assertEqual(settled['settlement_value'], 10000)
        self.assertEqual(settled['gross_pnl'], settled['payout'] - settled['quantity'] * settled['price'])
        self.assertEqual(settled['net_pnl'], settled['payout'] - settled['cost'])

    def test_no_side_losing_settlement(self):
        self.ingest()
        row = self.db.claim()
        self.db.enter(row['id'], 'CONTRACT', 'no', 2, 5000, 100, 100000, {})
        self.assertTrue(self.db.settle(1, 'yes'))
        self.assertEqual(self.db.report()['realized_pnl_dollars'], -1.01)
        self.assertFalse(self.db.settle(1, 'no'))

    def test_determined_is_not_settled_and_unknown_results_stay_open(self):
        self.ingest()
        self.trader.process_next()
        self.client.markets[0].update(status='determined', result='yes')
        self.assertEqual(self.trader.settle(), 0)
        self.client.markets[0].update(status='settled', result='scalar')
        self.assertEqual(self.trader.settle(), 0)
        self.assertEqual(len(self.db.open_trades()), 1)

    def test_finalized_fractional_settlement_records_push(self):
        self.ingest()
        self.trader.process_next()
        self.client.markets[0].update(status='finalized', result='scalar',
                                      settlement_value_dollars='.5000')
        self.assertEqual(self.trader.settle(), 1)
        trade = self.db.report()['trades'][0]
        self.assertEqual(trade['final_result'], 'PUSH')
        self.assertEqual(trade['settlement_value'], 5000)
        self.assertEqual(trade['gross_pnl'], 0)
        self.assertEqual(trade['net_pnl'], -trade['fee'])

    def test_void_result_uses_explicit_exchange_value(self):
        self.ingest()
        row = self.db.claim()
        self.db.enter(row['id'], 'CONTRACT', 'yes', 2, 5000, 100, 100000, {})
        self.assertTrue(self.db.settle(1, 'void', 5000))
        trade = self.db.report()['trades'][0]
        self.assertEqual(trade['final_result'], 'VOID')
        self.assertEqual(trade['gross_pnl'], 0)
        self.assertEqual(trade['net_pnl'], -100)

    def test_fractional_settlement_complements_no_direction(self):
        self.ingest()
        row = self.db.claim()
        self.db.enter(row['id'], 'CONTRACT', 'no', 2, 6000, 100, 100000, {})
        self.assertTrue(self.db.settle(1, 'scalar', 3000))
        trade = self.db.report()['trades'][0]
        self.assertEqual(trade['settlement_value'], 7000)
        self.assertEqual(trade['payout'], 14000)
        self.assertEqual(trade['gross_pnl'], 2000)
        self.assertEqual(trade['net_pnl'], 1900)
        self.assertEqual(trade['final_result'], 'WIN')

    def test_import_listener_has_no_connection_or_credential_side_effects(self):
        import telegram_listener
        self.assertFalse(hasattr(telegram_listener, 'client'))

