import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from config import Settings
from database import Database
from reporting import (CSV_FIELDS, breakdown, export_csv, format_report,
                       report_rows, summarize, whale_performance)


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.db = Database(self.path, Settings().starting_cash)

    def add_trade(self, message_id, ticker, side, price, fee, whale_price,
                  trader='SampleTrader', market_type='game_winner', series='KXNFLGAME'):
        raw = f'📌 Market {ticker}\n{trader} bought Team for $1,250\nEntry price: ${whale_price}'
        self.db.ingest(f'-100:{message_id}', raw, time.time())
        alert = self.db.claim()
        details = {'alert': {'market': f'Market {ticker}', 'trader': trader,
                             'market_type': market_type, 'side': 'Team',
                             'price': str(whale_price), 'amount': '1250'},
                   'match': {'event': {'series_ticker': series}}}
        status = self.db.enter(alert['id'], ticker, side, 2, price, fee,
                               1_000_000, details)
        self.assertEqual(status, 'traded')
        return alert['id']

    def test_settlement_and_summary_math(self):
        self.add_trade(101, 'WIN', 'yes', 5000, 100, '.49')
        self.assertTrue(self.db.settle(1, 'yes'))
        self.add_trade(102, 'LOSS', 'no', 4000, 100, '.38', market_type='total')
        self.assertTrue(self.db.settle(2, 'yes'))
        self.add_trade(103, 'OPEN', 'yes', 6000, 100, '.60', trader='Other')

        rows = report_rows(self.db.trade_history())
        totals = summarize(rows)
        self.assertEqual((totals['total_trades'], totals['settled_trades'], totals['open_trades']),
                         (3, 2, 1))
        self.assertEqual((totals['wins'], totals['losses']), (1, 1))
        self.assertEqual(totals['win_rate'], .5)
        self.assertEqual(totals['total_staked'], 3)
        self.assertEqual(float(totals['total_fees']), .03)
        self.assertEqual(float(totals['net_p_l']), .18)
        self.assertAlmostEqual(float(totals['roi']), .10)
        self.assertEqual(rows[0]['telegram_alert_id'], '101')
        self.assertEqual(rows[0]['settlement_result'], 'WIN')
        self.assertEqual(rows[0]['gross_p_l'], 1)
        self.assertEqual(float(rows[0]['net_p_l']), .99)
        self.assertEqual(set(breakdown(rows, 'market_type')), {'game_winner', 'total'})
        leaders = whale_performance(rows)
        self.assertEqual(leaders[0][0], 'SampleTrader')
        self.assertEqual(float(leaders[0][1]['total_staked']), 1.8)
        self.assertEqual(float(leaders[0][1]['net_p_l']), .18)
        self.assertAlmostEqual(float(leaders[0][1]['roi']), .10)
        output = format_report(rows, self.db.portfolio_stats())
        self.assertIn('WHALE PERFORMANCE', output)
        self.assertIn('BY SPORT/SERIES', output)
        self.assertIn('Starting bankroll: $10,000.00', output)
        self.assertIn('Current realized bankroll: $10,000.18', output)

    def test_slippage_buckets_and_csv_columns(self):
        for index, (live, whale) in enumerate(((5000, '.50'), (5200, '.50'),
                                               (5500, '.50'), (5600, '.50')), 1):
            self.add_trade(index, f'T{index}', 'yes', live, 0, whale)
            self.db.settle(index, 'yes')
        rows = report_rows(self.db.trade_history())
        self.assertEqual(set(breakdown(rows, 'slippage')),
                         {'0-1 cents', '2-3 cents', '4-5 cents', '6+ cents'})
        output = export_csv(rows, Path(self.temp.name) / 'results.csv')
        self.assertTrue(output.read_bytes().startswith(b'\xef\xbb\xbf'))
        with output.open(encoding='utf-8-sig', newline='') as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(exported[0].keys(), dict.fromkeys(CSV_FIELDS).keys())
        self.assertEqual(exported[0]['trade_status'], 'SETTLED')
        self.assertEqual(exported[0]['kalshi_ticker'], 'T1')

    def test_existing_ledger_is_migrated_and_backfilled(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        db = sqlite3.connect(legacy)
        try:
            db.executescript('''
                CREATE TABLE settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT INTO settings VALUES ('starting_cash', 10000000);
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
                    raw_text TEXT NOT NULL, received_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'traded', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}');
                INSERT INTO alerts VALUES (1,'chat:1','old',0,'traded',1,0,0,'','{}');
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
                    ticker TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL,
                    price INTEGER NOT NULL, fee INTEGER NOT NULL, cost INTEGER NOT NULL,
                    opened_at REAL NOT NULL, closed_at REAL, payout INTEGER, result TEXT);
                INSERT INTO trades VALUES (1,1,'OLD','yes',2,5000,100,10100,0,1,20000,'yes');
            ''')
            db.commit()
        finally:
            db.close()
        migrated = Database(legacy, 1).report()['trades'][0]
        self.assertEqual(migrated['final_result'], 'WIN')
        self.assertEqual(migrated['settlement_value'], 10000)
        self.assertEqual(migrated['gross_pnl'], 10000)
        self.assertEqual(migrated['net_pnl'], 9900)
        self.assertEqual(migrated['session_id'], 'legacy')
        self.assertEqual(migrated['strategy_version'], 'legacy')
        self.assertEqual(migrated['code_version'], 'legacy')
        migrated_db = Database(legacy, 1)
        with migrated_db.connect() as connection:
            alert_columns = {row['name'] for row in connection.execute('PRAGMA table_info(alerts)')}
            status_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='backend_status'").fetchone()
        self.assertTrue({'session_id', 'strategy_version', 'code_version', 'backend_run_id',
                         'strategy_mode',
                         'telegram_chat_id', 'telegram_message_id', 'parsed_alert',
                         'match_status', 'processed_at'} <= alert_columns)
        with migrated_db.connect() as connection:
            trade_columns = {row['name'] for row in connection.execute('PRAGMA table_info(trades)')}
            backend_columns = {row['name'] for row in connection.execute(
                'PRAGMA table_info(backend_status)')}
            run_columns = {row['name'] for row in connection.execute(
                'PRAGMA table_info(backend_runs)')}
        self.assertTrue({'trade_mode', 'shadow_reason', 'whale_win_rate',
                         'whale_roi', 'hours_to_close', 'resolution_time_source',
                         'effective_resolution_time', 'can_close_early',
                         'backend_run_id', 'strategy_mode'} <= trade_columns)
        self.assertEqual(migrated['trade_mode'], 'PAPER')
        self.assertIn('strategy_mode', backend_columns)
        self.assertIn('strategy_mode', run_columns)
        self.assertIsNotNone(status_table)
        with migrated_db.connect() as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            original_alert = connection.execute(
                'SELECT source_key,status FROM alerts WHERE id=1').fetchone()
        self.assertTrue({'runtime_state', 'telegram_offsets', 'backend_runs'} <= tables)
        self.assertEqual(tuple(original_alert), ('chat:1', 'traded'))


if __name__ == '__main__':
    unittest.main()
