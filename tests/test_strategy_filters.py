from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from dashboard_data import DashboardFilters, comparison_rows, query_trades
from database import Database
from reporting import breakdown, report_rows
from simulator import PaperTrader
from samples import message
from test_matching import FakeClient, event, market


class StrategyFilterTests(unittest.TestCase):
    def run_signal(self, resolution_hours=2, win_rate=75, roi=75,
                   close_hours=48, occurrence=True, expected=True,
                   include_close=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'paper.sqlite3'
        settings = Settings(database=path)
        database = Database(path, settings.starting_cash)
        received_at = time.time()
        def timestamp(hours):
            return datetime.fromtimestamp(
                received_at + hours * 3600, timezone.utc).isoformat().replace('+00:00', 'Z')
        close_time = timestamp(close_hours)
        timing = {}
        if resolution_hours is not None and occurrence:
            timing['occurrence_datetime'] = timestamp(resolution_hours)
        if resolution_hours is not None and expected:
            timing['expected_expiration_time'] = timestamp(resolution_hours)
        client = FakeClient([event()], [market(close_time=close_time if include_close else None,
                                                latest_expiration_time=close_time,
                                                can_close_early=True, **timing)])
        trader = PaperTrader(client, database, settings)
        database.ingest('chat:1', message('NFL: Falcons vs Packers', 'Falcons',
                                          win_rate=win_rate, roi=roi), received_at)
        trader.process_next()
        with database.connect() as connection:
            alert = dict(connection.execute('SELECT * FROM alerts').fetchone())
        return database, trader, alert

    def test_same_day_and_tomorrow_markets_are_eligible(self):
        for hours in (2, 23):
            with self.subTest(hours=hours):
                database, _, alert = self.run_signal(hours)
                self.assertEqual(alert['status'], 'traded')
                trade = database.report()['trades'][0]
                self.assertEqual(trade['trade_mode'], 'PAPER')
                self.assertAlmostEqual(trade['hours_to_close'], hours, places=3)
                self.assertEqual(trade['resolution_time_source'],
                                 'market.occurrence_datetime')

    def test_genuine_twenty_five_hour_event_is_rejected(self):
        database, _, alert = self.run_signal(25)
        self.assertEqual(alert['status'], 'skipped')
        self.assertEqual(alert['reason'], 'Market closes too far in the future')
        self.assertEqual(database.report()['trades'], [])
        self.assertAlmostEqual(json.loads(alert['details'])['hours_to_close'], 25, places=3)

    def test_expected_expiration_beats_forty_hour_close_bound(self):
        database, _, alert = self.run_signal(3, close_hours=40, occurrence=False)
        self.assertEqual(alert['status'], 'traded')
        trade = database.report()['trades'][0]
        self.assertAlmostEqual(trade['hours_to_close'], 3, places=3)
        self.assertEqual(trade['resolution_time_source'],
                         'market.expected_expiration_time')
        self.assertTrue(trade['can_close_early'])

    def test_live_game_is_eligible_and_clamped_to_zero_hours(self):
        database, _, alert = self.run_signal(-2, close_hours=40)
        self.assertEqual(alert['status'], 'traded')
        trade = database.report()['trades'][0]
        self.assertEqual(trade['hours_to_close'], 0)
        timing = json.loads(alert['details'])['resolution_timing']
        self.assertLess(timing['raw_hours'], 0)
        self.assertEqual(timing['source'], 'market.occurrence_datetime')

    def test_missing_expected_expiration_uses_conservative_close_fallback(self):
        database, _, alert = self.run_signal(None, close_hours=40)
        self.assertEqual((alert['status'], alert['reason']),
                         ('skipped', 'Market closes too far in the future'))
        timing = json.loads(alert['details'])['resolution_timing']
        self.assertEqual(timing['source'], 'market.close_time')
        self.assertAlmostEqual(timing['hours'], 40, places=3)

    def test_missing_reliable_close_time_is_rejected(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'paper.sqlite3'
        settings = Settings(database=path)
        database = Database(path, settings.starting_cash)
        client = FakeClient([event()], [market(close_time=None)])
        database.ingest('chat:missing-close',
                        message('NFL: Falcons vs Packers', 'Falcons'), time.time())
        PaperTrader(client, database, settings).process_next()
        with database.connect() as connection:
            alert = dict(connection.execute('SELECT * FROM alerts').fetchone())
        self.assertEqual((alert['status'], alert['reason']),
                         ('skipped', 'Reliable market resolution time unavailable'))
        self.assertEqual(database.report()['trades'], [])

    def test_event_close_time_is_used_when_market_omits_it(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'paper.sqlite3'
        settings = Settings(database=path)
        database = Database(path, settings.starting_cash)
        received_at = time.time()
        close_time = datetime.fromtimestamp(
            received_at + 2 * 3600, timezone.utc).isoformat().replace('+00:00', 'Z')
        event_row = {**event(), 'close_time': close_time}
        client = FakeClient([event_row], [market(close_time=None)])
        database.ingest('chat:event-close',
                        message('NFL: Falcons vs Packers', 'Falcons'), received_at)
        PaperTrader(client, database, settings).process_next()
        self.assertEqual(database.report()['alerts'], {'traded': 1})

    def test_latest_expiration_is_last_resort_timing_fallback(self):
        database, _, alert = self.run_signal(
            None, close_hours=2, occurrence=False, expected=False,
            include_close=False)
        self.assertEqual(alert['status'], 'traded')
        trade = database.report()['trades'][0]
        self.assertEqual(trade['resolution_time_source'],
                         'market.latest_expiration_time')

    def test_low_win_rate_is_shadow_only(self):
        database, trader, alert = self.run_signal(2, win_rate=49, roi=100)
        self.assertEqual((alert['status'], alert['reason']),
                         ('shadow_only', 'Whale win rate below strategy minimum'))
        trade = database.report()['trades'][0]
        self.assertEqual(trade['trade_mode'], 'SHADOW_ONLY')
        self.assertEqual(database.portfolio_stats()['open_exposure'], 0)
        trader.client.markets[0].update(status='settled', result='yes')
        self.assertEqual(trader.settle(), 1)
        self.assertEqual(database.trade_history()[0]['final_result'], 'WIN')
        self.assertEqual(database.portfolio_stats()['realized_bankroll'],
                         database.portfolio_stats()['starting_bankroll'])

    def test_low_roi_is_shadow_only(self):
        database, _, alert = self.run_signal(2, win_rate=80, roi=49)
        self.assertEqual((alert['status'], alert['reason']),
                         ('shadow_only', 'Whale ROI below strategy minimum'))
        self.assertEqual(database.report()['trades'][0]['trade_mode'], 'SHADOW_ONLY')

    def test_both_quality_thresholds_pass(self):
        database, _, alert = self.run_signal(2, win_rate=50, roi=50)
        self.assertEqual(alert['status'], 'traded')
        self.assertEqual(database.report()['trades'][0]['trade_mode'], 'PAPER')

    def test_filtered_and_shadow_performance_reporting(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'paper.sqlite3'
        settings = Settings(database=path)
        database = Database(path, settings.starting_cash)
        for index, mode, win_rate, roi, result in (
            (1, 'PAPER', 75, 100, 'yes'),
            (2, 'SHADOW_ONLY', 40, 25, 'no'),
        ):
            database.ingest(f'chat:{index}', message(f'NFL: Falcons vs Packers {index}',
                                                     'Falcons', win_rate=win_rate, roi=roi),
                            time.time())
            alert = database.claim()
            details = {'alert': {'market': f'Market {index}', 'trader': 'Whale',
                                  'market_type': 'game_winner', 'side': 'Falcons',
                                  'price': .5, 'win_rate': win_rate, 'roi': roi},
                       'match': {'event': {'series_ticker': 'KXNFLGAME'}},
                       'hours_to_close': 2,
                       'resolution_timing': {'source': 'market.occurrence_datetime',
                                             'timestamp': '2026-09-26T20:00:00Z',
                                             'can_close_early': True}}
            status = database.enter(
                alert['id'], f'T{index}', 'yes', 50, 5000, 100,
                settings.max_exposure, details, stake=settings.fixed_stake,
                trade_mode=mode,
                shadow_reason=('Whale quality below strategy thresholds'
                               if mode == 'SHADOW_ONLY' else ''),
                whale_win_rate=win_rate, whale_roi=roi, hours_to_close=2,
                resolution_time_source='market.occurrence_datetime',
                effective_resolution_time='2026-09-26T20:00:00Z',
                can_close_early=True)
            self.assertEqual(status, 'traded' if mode == 'PAPER' else 'shadow_only')
            database.settle(index, result)

        records = report_rows(database.trade_history())
        comparison = {row['Portfolio']: row for row in comparison_rows(records)}
        self.assertEqual(comparison['Strict strategy trades (slippage <= 0¢)']['Wins'], 1)
        self.assertEqual(comparison['Whale-quality shadow trades']['Losses'], 1)
        self.assertEqual(comparison['All qualifying shadow signals']['Losses'], 1)
        self.assertEqual(comparison['All qualifying signals']['Settled'], 2)
        self.assertEqual(set(breakdown(records, 'whale_win_rate_bucket')),
                         {'under 50%', '70-79%'})
        self.assertEqual(set(breakdown(records, 'whale_roi_bucket')),
                         {'under 50%', '100-149%'})
        self.assertEqual(set(breakdown(records, 'hours_to_close_bucket')), {'0-3h'})
        queried = query_trades(path)
        self.assertEqual({row['trade_mode'] for row in queried}, {'PAPER', 'SHADOW_ONLY'})
        shadow = query_trades(path, DashboardFilters(trade_mode='SHADOW_ONLY'))
        self.assertEqual(len(shadow), 1)
        self.assertEqual(shadow[0]['shadow_reason'],
                         'Whale quality below strategy thresholds')
        self.assertEqual(shadow[0]['resolution_time_source'],
                         'market.occurrence_datetime')


if __name__ == '__main__':
    unittest.main()
