import json
from pathlib import Path
import tempfile
import time
import unittest

from alert_parser import parse_alert
from config import Settings
from console_output import format_summary
from database import Database
from simulator import PaperTrader
from samples import message
from test_matching import FakeClient, event, market


class ConsoleTests(unittest.TestCase):
    def row(self, status='traded', reason='', side='no'):
        raw = message('NFL: Falcons vs Packers: O/U 40.5', 'Under')
        return {'source_key': 'chat:1', 'raw_text': raw, 'status': status, 'reason': reason,
                'details': json.dumps({'alert': parse_alert(raw), 'match': {
                    'side': side, 'market': {'title': 'Over 40.5 points?', 'ticker': 'TOTAL-41'}},
                    'quote': {'yes_ask_dollars': '.75', 'no_ask_dollars': '.52'},
                    'hours_to_close': 2.5,
                    'resolution_timing': {'source': 'market.occurrence_datetime'}})}

    def test_filled_no_side_summary_has_all_fields(self):
        trade = {'side': 'no', 'ticker': 'TOTAL-41', 'quantity': 10,
                 'price': 5200, 'fee': 1900, 'cost': 53900}
        output = format_summary(self.row(), trade)
        for expected in ('Market: NFL: Falcons vs Packers: O/U 40.5',
                         'Trader: SampleTrader', 'Type: Total', 'Whale side: Under',
                         'Price: $0.5000', 'Amount: $1,250.00',
                         'Kalshi market: Over 40.5 points?', 'Ticker: TOTAL-41',
                         'Direction: NO', 'Live ask: $0.5200',
                         'Slippage (live - whale): +$0.0200',
                         'Hours to resolution: 2.50h',
                         'Source: market.occurrence_datetime',
                         'Simulated stake: $5.2000', 'Contracts: 10',
                         'Estimated fee: $0.19', 'Total paper cost: $5.3900',
                         'Trade status: PAPER FILLED'):
            self.assertIn(expected, output)

    def test_skip_preserves_exact_reason_and_does_not_invent_fill(self):
        output = format_summary(self.row('skipped', 'Ask exceeds allowed slippage'))
        self.assertIn('Skip reason: Ask exceeds allowed slippage', output)
        self.assertIn('Simulated stake: $0.0000 | Contracts: 0', output)
        self.assertIn('Live ask: $0.5200', output)

    def test_stale_alert_displays_original_fields_without_quote(self):
        row = self.row('skipped', 'Stale or future-dated alert')
        row['details'] = '{}'
        output = format_summary(row)
        self.assertIn('Trader: SampleTrader', output)
        self.assertIn('Live ask: Unavailable', output)
        self.assertIn('Kalshi market: Unavailable', output)
        self.assertIn('Skip reason: Stale or future-dated alert', output)

    def test_invalid_and_retryable_statuses(self):
        row = self.row('invalid', 'Alert parser rejected message')
        row.update(raw_text='Original unparseable market', details='{}')
        output = format_summary(row)
        self.assertIn('Market: Original unparseable market', output)
        self.assertIn('Trader: Unavailable', output)
        self.assertIn('Skip reason: Alert parser rejected message', output)
        output = format_summary(self.row('retryable', 'Kalshi cooldown active; retry later'))
        self.assertIn('Trade status: DEFERRED (retry pending)', output)
        self.assertIn('Reason: Kalshi cooldown active; retry later', output)

    def test_total_line_and_stale_statuses_are_plain_english_skips(self):
        output = format_summary(self.row('line_not_found',
                                         'Exact total line 2.5 not found for matched event'))
        self.assertIn('Trade status: SKIPPED (line not found)', output)
        self.assertIn('Skip reason: Exact total line 2.5 not found for matched event', output)
        output = format_summary(self.row('stale',
                                         'Exact total market is not open (status: finalized)'))
        self.assertIn('Trade status: SKIPPED (stale/closed market)', output)
        self.assertIn('Skip reason: Exact total market is not open (status: finalized)', output)

    def test_structured_matching_failure_codes_are_visible(self):
        for status in ('unsupported_series', 'event_not_found', 'contract_not_found',
                       'ambiguous_match', 'name_alias_miss'):
            with self.subTest(status=status):
                output = format_summary(self.row(status, 'diagnostic reason'))
                self.assertIn(f'Trade status: {status.upper()}', output)
                self.assertIn('Skip reason: diagnostic reason', output)

    def test_callbacks_run_after_commit_on_fill_and_early_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / 'paper.sqlite3', Settings().starting_cash)
            captured = []
            trader = PaperTrader(FakeClient([event()], [market()]), db,
                                 on_result=lambda row, trade: captured.append((row, trade)))
            raw = message('NFL: Falcons vs Packers', 'Falcons')
            db.ingest('filled', raw, time.time())
            trader.process_next()
            db.ingest('stale', raw, time.time() - 1000)
            trader.process_next()
            trader.process_next()  # An empty queue must not repeat a summary.
            self.assertEqual(len(captured), 2)
            self.assertEqual(captured[0][0]['status'], 'traded')
            self.assertGreater(captured[0][1]['quantity'], 0)
            self.assertEqual(captured[1][0]['reason'], 'Stale or future-dated alert')
            self.assertIsNone(captured[1][1])

    def test_broken_output_cannot_change_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / 'paper.sqlite3', Settings().starting_cash)
            def broken_output(*args):
                raise OSError('Console disconnected')
            trader = PaperTrader(FakeClient([event()], [market()]), db, on_result=broken_output)
            db.ingest('filled', message('NFL: Falcons vs Packers', 'Falcons'), time.time())
            with self.assertLogs('simulator', level='ERROR'):
                self.assertTrue(trader.process_next())
            self.assertEqual(db.report()['alerts'], {'traded': 1})
            self.assertEqual(len(db.report()['trades']), 1)

    def test_shadow_only_summary_is_explicit(self):
        row = self.row('shadow_only', 'Whale ROI below strategy minimum', 'yes')
        trade = {'side': 'yes', 'ticker': 'TOTAL-41', 'quantity': 50,
                 'price': 5000, 'stake': 250000, 'fee': 100, 'cost': 250100}
        output = format_summary(row, trade)
        self.assertIn('Trade status: SHADOW_ONLY', output)
        self.assertIn('Shadow reason: Whale ROI below strategy minimum', output)

    def test_far_future_skip_has_clear_status(self):
        output = format_summary(self.row('skipped', 'Market closes too far in the future'))
        self.assertIn('Trade status: SKIPPED — market closes too far in the future', output)
        self.assertIn('Skip reason: Market closes too far in the future', output)
