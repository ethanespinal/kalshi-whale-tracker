from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from database import Database
from reporting import breakdown, report_rows, strategy_comparison
from simulator import PaperTrader
from samples import message
from test_matching import FakeClient, event, market


class StrategyModeTests(unittest.TestCase):
    def run_signal(self, market_type, strategy_mode):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'paper.sqlite3'
        settings = replace(Settings(database=path), strategy_mode=strategy_mode)
        database = Database(path, settings.starting_cash)
        if market_type == 'game_winner':
            event_row = event()
            market_row = market()
            alert_text = message('NFL: Falcons vs Packers', 'Falcons')
        elif market_type == 'spread':
            event_row = event('KXNCAAFSPREAD', 'Ohio State Buckeyes vs Marshall')
            market_row = market(
                label='Ohio State wins by over 27.5 points',
                floor_strike=27.5, strike_type='greater')
            alert_text = message('Spread: Ohio State (-27.5)', 'Ohio State')
        else:
            event_row = event('KXNFLTOTAL',
                              'ATL Falcons vs GB Packers: Total Points')
            market_row = market(
                label='Over 40.5 points', floor_strike=40.5,
                strike_type='greater')
            alert_text = message('NFL: Falcons vs Packers: O/U 40.5', 'Over')
        client = FakeClient([event_row], [market_row])
        database.ingest('chat:1', alert_text, time.time(),
                        strategy_mode=strategy_mode)
        trader = PaperTrader(client, database, settings)
        trader.process_next()
        return database, trader, database.report()['trades'][0]

    def test_conservative_mode_fills_game_winners(self):
        database, _, trade = self.run_signal('game_winner', 'conservative')
        self.assertEqual((trade['trade_mode'], trade['strategy_mode']),
                         ('PAPER', 'conservative'))
        self.assertEqual(database.report()['alerts'], {'traded': 1})

    def test_conservative_mode_shadows_valid_spreads_and_totals(self):
        for market_type in ('spread', 'total'):
            with self.subTest(market_type=market_type):
                database, trader, trade = self.run_signal(market_type, 'conservative')
                self.assertEqual(trade['trade_mode'], 'SHADOW_ONLY')
                self.assertEqual(trade['shadow_reason'],
                                 'Filtered by conservative strategy mode')
                trader.client.markets[0].update(status='settled', result='yes')
                self.assertEqual(trader.settle(), 1)
                self.assertIsNotNone(database.trade_history()[0]['final_result'])

    def test_moneyline_only_fills_winners_and_shadows_other_markets(self):
        _, _, winner = self.run_signal('game_winner', 'moneyline_only')
        self.assertEqual(winner['trade_mode'], 'PAPER')
        for market_type in ('spread', 'total'):
            with self.subTest(market_type=market_type):
                _, _, trade = self.run_signal(market_type, 'moneyline_only')
                self.assertEqual(trade['trade_mode'], 'SHADOW_ONLY')
                self.assertEqual(trade['shadow_reason'],
                                 'Filtered by moneyline-only strategy mode')

    def test_all_supported_preserves_current_market_type_behavior(self):
        for market_type in ('game_winner', 'spread', 'total'):
            with self.subTest(market_type=market_type):
                _, _, trade = self.run_signal(market_type, 'all_supported')
                self.assertEqual(trade['trade_mode'], 'PAPER')

    def test_reporting_separates_mode_market_type_and_shadow_exclusions(self):
        records = []
        for market_type in ('game_winner', 'spread', 'total'):
            database, _, _ = self.run_signal(market_type, 'conservative')
            records.extend(report_rows(database.trade_history()))
        comparison = strategy_comparison(records)
        self.assertEqual(
            comparison['Strategy-mode excluded shadow signals']['total_trades'], 2)
        self.assertEqual(comparison['All qualifying shadow signals']['total_trades'], 2)
        self.assertEqual(set(breakdown(records, 'market_type')),
                         {'game_winner', 'spread', 'total'})
        self.assertEqual(set(breakdown(records, 'strategy_mode')), {'conservative'})
        self.assertEqual(set(breakdown(records, 'market_type_trade_mode')), {
            'game_winner / PAPER', 'spread / SHADOW_ONLY', 'total / SHADOW_ONLY'})


if __name__ == '__main__':
    unittest.main()
