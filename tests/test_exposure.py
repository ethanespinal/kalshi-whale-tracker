from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from database import Database
from simulator import PaperTrader, quote_order
from samples import message
from test_matching import FakeClient, event, market


class ExposureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.settings = Settings(database=self.path)
        self.db = Database(self.path, self.settings.starting_cash)

    def enter_direct(self, index, ticker, settings=None):
        settings = settings or self.settings
        self.db.ingest(f'direct:{index}', message('NFL: Falcons vs Packers', 'Falcons'),
                       time.time())
        alert = self.db.claim()
        quantity = Decimal(settings.fixed_stake) / 5000
        return self.db.enter(alert['id'], ticker, 'yes', quantity, 5000, 0,
                             settings.max_exposure, {}, settings.max_open_positions,
                             stake=settings.fixed_stake)

    def test_fixed_twenty_five_dollar_sizing(self):
        quantity, price, fee = quote_order(market(yes_ask_dollars='.5300'), 'yes',
                                           '.53', self.settings)
        self.assertEqual(quantity * price, 250_000)
        self.assertGreater(fee, 0)
        self.assertEqual(self.settings.starting_cash, 100_000_000)
        self.assertEqual(self.settings.fixed_stake, 250_000)
        self.assertEqual(self.settings.max_exposure, 20_000_000)

    def test_two_thousand_dollar_exposure_cap(self):
        for index in range(80):
            self.assertEqual(self.enter_direct(index, f'T{index}'), 'traded')
        stats = self.db.portfolio_stats()
        self.assertEqual(stats['open_exposure'], 20_000_000)
        self.assertEqual(stats['peak_open_exposure'], 20_000_000)
        self.assertFalse(self.db.exposure_allows(
            self.settings.fixed_stake, self.settings.max_exposure))

        self.db.ingest('direct:blocked', message('NFL: Falcons vs Packers', 'Falcons'),
                       time.time())
        alert = self.db.claim()
        status = self.db.enter(alert['id'], 'BLOCKED', 'yes', 50, 5000, 0,
                               self.settings.max_exposure, {}, self.settings.max_open_positions,
                               stake=self.settings.fixed_stake)
        self.assertEqual(status, 'exposure closed')
        self.assertEqual(len(self.db.open_trades()), 80)

    def test_closed_state_suppresses_alerts_once_and_reopens_after_settlement(self):
        settings = replace(self.settings, max_exposure=500_000, max_open_positions=2)
        self.assertEqual(self.enter_direct(1, 'T1', settings), 'traded')
        self.assertEqual(self.enter_direct(2, 'T2', settings), 'traded')
        changes, normal_output = [], []
        client = FakeClient([event()], [
            market('T1', status='finalized', result='yes'),
            market('T2'),
        ])
        trader = PaperTrader(client, self.db, settings,
                             on_result=lambda *args: normal_output.append(args),
                             on_exposure_change=changes.append)

        for index in (3, 4):
            self.db.ingest(f'chat:{index}', message('NFL: Falcons vs Packers', 'Falcons'),
                           time.time())
            self.assertTrue(trader.process_next())
        self.assertEqual(changes, ['⏸ EXPOSURE CLOSED — waiting for positions to settle'])
        self.assertEqual(normal_output, [])
        self.assertEqual(client.calls, [])
        with self.db.connect() as connection:
            reasons = [row[0] for row in connection.execute(
                "SELECT reason FROM alerts WHERE source_key LIKE 'chat:%' ORDER BY id")]
        self.assertEqual(reasons, ['Exposure closed; waiting for positions to settle'] * 2)

        self.assertEqual(trader.settle(), 1)
        self.assertEqual(changes[-1], '▶ EXPOSURE REOPENED — paper entries resumed')
        self.assertEqual(self.db.portfolio_stats()['open_exposure'], 250_000)

        client.calls.clear()
        client.markets = [market()]
        self.db.ingest('chat:5', message('NFL: Falcons vs Packers', 'Falcons'), time.time())
        self.assertTrue(trader.process_next())
        self.assertEqual(len(normal_output), 1)
        self.assertEqual(normal_output[0][0]['status'], 'traded')
        self.assertEqual(self.db.portfolio_stats()['peak_open_exposure'], 500_000)


if __name__ == '__main__':
    unittest.main()
