from decimal import Decimal
from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest

from config import MAX_ALLOWED_SLIPPAGE, Settings
from database import Database
from reporting import report_rows, strategy_comparison
from simulator import PaperTrader, positive_slippage_reason
from samples import message
from test_matching import FakeClient, event, market


class SlippageStrategyTests(unittest.TestCase):
    def run_signal(self, whale_price, executable_price, side='yes', opposite_price='.10'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'paper.sqlite3'
        settings = Settings(database=path)
        database = Database(path, settings.starting_cash)
        if side == 'yes':
            event_row = event()
            market_row = market(
                yes_ask_dollars=executable_price, no_ask_dollars=opposite_price)
            alert_text = message('NFL: Falcons vs Packers', 'Falcons', whale_price)
        else:
            event_row = event('KXNFLTOTAL',
                              'ATL Falcons vs GB Packers: Total Points')
            market_row = market(
                label='Over 40.5 points', floor_strike=40.5,
                strike_type='greater', yes_ask_dollars=opposite_price,
                no_ask_dollars=executable_price)
            alert_text = message(
                'NFL: Falcons vs Packers: O/U 40.5', 'Under', whale_price)
        client = FakeClient([event_row], [market_row])
        database.ingest('chat:1', alert_text, time.time())
        trader = PaperTrader(client, database, settings)
        trader.process_next()
        return database, trader, database.report()['trades'][0]

    def test_strict_threshold_is_configured_at_zero(self):
        self.assertEqual(MAX_ALLOWED_SLIPPAGE, 0.00)
        self.assertEqual(Settings().max_allowed_slippage, 0.00)
        self.assertEqual(Settings().strategy_version, 'v1.10')
        self.assertEqual(
            positive_slippage_reason(5700, '0.56', Settings()),
            'Positive slippage above strategy maximum')
        custom = replace(Settings(), max_allowed_slippage=.01)
        self.assertEqual(positive_slippage_reason(5700, '0.56', custom), '')

    def test_negative_and_zero_slippage_are_paper_filled(self):
        for executable, expected_cents in (('.54', -2), ('.55', -1), ('.56', 0)):
            with self.subTest(executable=executable):
                database, _, trade = self.run_signal('0.56', executable)
                self.assertEqual(trade['trade_mode'], 'PAPER')
                self.assertEqual(database.report()['alerts'], {'traded': 1})
                row = report_rows(database.trade_history())[0]
                self.assertEqual(row['slippage'], Decimal(expected_cents) / 100)

    def test_positive_one_two_and_three_cents_are_shadow_only(self):
        records = []
        for executable, expected_cents in (('.57', 1), ('.58', 2), ('.59', 3)):
            with self.subTest(executable=executable):
                database, trader, trade = self.run_signal('0.56', executable)
                self.assertEqual(trade['trade_mode'], 'SHADOW_ONLY')
                self.assertEqual(
                    trade['shadow_reason'], 'Positive slippage above strategy maximum')
                self.assertEqual(database.report()['alerts'], {'shadow_only': 1})
                row = report_rows(database.trade_history())[0]
                self.assertEqual(row['slippage'], Decimal(expected_cents) / 100)
                records.append(row)
                details = database.alert_result(trade['alert_id'])[0]['details']
                self.assertIn(f'"slippage_units": {expected_cents * 100}', details)
                trader.client.markets[0].update(status='settled', result='yes')
                self.assertEqual(trader.settle(), 1)
                self.assertIn(database.trade_history()[0]['final_result'], ('WIN', 'LOSS'))

        comparison = strategy_comparison(records)
        self.assertEqual(comparison['+1¢ signals']['total_trades'], 1)
        self.assertEqual(comparison['+2¢ signals']['total_trades'], 1)
        self.assertEqual(comparison['+3¢ signals']['total_trades'], 1)
        self.assertEqual(comparison['All qualifying shadow signals']['total_trades'], 3)

    def test_yes_direction_uses_yes_executable_ask(self):
        _, _, trade = self.run_signal('0.56', '.57', side='yes', opposite_price='.20')
        self.assertEqual((trade['side'], trade['price'], trade['trade_mode']),
                         ('yes', 5700, 'SHADOW_ONLY'))

    def test_no_direction_uses_no_executable_ask(self):
        _, _, trade = self.run_signal('0.56', '.57', side='no', opposite_price='.20')
        self.assertEqual((trade['side'], trade['price'], trade['trade_mode']),
                         ('no', 5700, 'SHADOW_ONLY'))


if __name__ == '__main__':
    unittest.main()
