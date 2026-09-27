import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from dashboard_data import DashboardFilters, query_trades
from database import Database
from simulator import PaperTrader
from telegram_listener import backfill_telegram_messages
from samples import message
from test_matching import FakeClient, event, market


CHAT_ID = 8624141013


class TelegramMessage:
    def __init__(self, message_id, text=None):
        self.chat_id = CHAT_ID
        self.id = message_id
        self.raw_text = text or message('NFL: Falcons vs Packers', 'Falcons')
        self.date = datetime.now(timezone.utc)


class TelegramHistory:
    def __init__(self, messages):
        self.messages = messages

    async def iter_messages(self, chat_id, min_id=0, reverse=False):
        rows = [item for item in self.messages
                if item.chat_id == chat_id and item.id > min_id]
        for item in sorted(rows, key=lambda row: row.id, reverse=not reverse):
            yield item


class RestartPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.settings = Settings(database=self.path)
        self.db = Database(self.path, self.settings.starting_cash)
        self.assertEqual(
            self.db.get_or_create_session_id('experiment-1'), 'experiment-1')

    def trader(self, db=None, client=None, run='run-1'):
        return PaperTrader(
            client or FakeClient([event()], [market()]), db or self.db,
            self.settings, session_id='experiment-1', strategy_version='v1.6',
            code_version='abc123', backend_run_id=run)

    def ingest(self, message_id=1, db=None, run='run-1'):
        db = db or self.db
        return db.ingest(
            f'{CHAT_ID}:{message_id}',
            message('NFL: Falcons vs Packers', 'Falcons'), time.time(),
            'experiment-1', 'v1.6', 'abc123', run, CHAT_ID, message_id)

    def test_restart_recovers_open_trade_and_stable_experiment_session(self):
        self.ingest()
        self.trader().process_next()
        reopened = Database(self.path, 1)
        self.assertEqual(
            reopened.get_or_create_session_id('new-default'), 'experiment-1')
        recovery = self.trader(reopened, run='run-2').start_backend(CHAT_ID)
        self.assertEqual(recovery['open_trades'], 1)
        self.assertEqual(len(reopened.open_trades()), 1)
        self.assertEqual(reopened.open_trades()[0]['backend_run_id'], 'run-1')

    def test_restart_after_settlement_preserves_history(self):
        self.ingest()
        first = self.trader()
        first.process_next()
        first.client.markets[0].update(status='settled', result='yes')
        self.assertEqual(first.settle(), 1)
        reopened = Database(self.path, 1)
        recovery = self.trader(reopened, run='run-2').start_backend(CHAT_ID)
        self.assertEqual(recovery['open_trades'], 0)
        self.assertEqual(reopened.trade_history()[0]['final_result'], 'WIN')

    def test_duplicate_telegram_alert_after_restart_is_idempotent(self):
        self.assertTrue(self.ingest(message_id=17))
        self.trader().process_next()
        reopened = Database(self.path, 1)
        self.assertFalse(self.ingest(message_id=17, db=reopened, run='run-2'))
        offset = reopened.telegram_offset(CHAT_ID)
        self.assertEqual(offset['last_persisted_message_id'], 17)
        self.assertEqual(offset['last_processed_message_id'], 17)
        with reopened.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0], 1)

    def test_interrupted_alert_is_reclaimed_with_parse_checkpoint(self):
        self.ingest(message_id=20)
        row = self.db.claim('run-1')
        parsed = {'market': 'NFL: Falcons vs Packers', 'market_type': 'game_winner',
                  'trader': 'SportsWhale', 'side': 'Falcons', 'price': '.50',
                  'amount': '20000', 'win_rate': '65', 'roi': '120'}
        self.db.checkpoint(row['id'], {'alert': parsed}, parsed=parsed)

        reopened = Database(self.path, 1)
        recovery = self.trader(reopened, run='run-2').start_backend(CHAT_ID)
        self.assertEqual(recovery['recovered_alerts'], 1)
        self.assertTrue(self.trader(reopened, run='run-2').process_next())
        with reopened.connect() as db:
            alert = dict(db.execute('SELECT * FROM alerts').fetchone())
        self.assertEqual(alert['status'], 'traded')
        self.assertEqual(alert['attempts'], 2)
        self.assertEqual(alert['market_type'], 'game_winner')
        self.assertIsNotNone(alert['processed_at'])
        self.assertEqual(len(reopened.trade_history()), 1)

    def test_dashboard_reads_trades_across_backend_runs(self):
        self.ingest(message_id=1, run='run-1')
        first = self.trader(run='run-1')
        first.process_next()
        first.client.markets[0].update(status='settled', result='yes')
        first.settle()

        reopened = Database(self.path, 1)
        self.ingest(message_id=2, db=reopened, run='run-2')
        second_market = market(ticker='SECOND')
        self.trader(reopened, FakeClient([event()], [second_market]), 'run-2').process_next()

        rows = query_trades(self.path)
        filtered = query_trades(
            self.path, DashboardFilters(session_id='experiment-1'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(filtered), 2)
        self.assertEqual({row['backend_run_id'] for row in rows}, {'run-1', 'run-2'})

    def test_startup_refresh_settles_trade_created_before_restart(self):
        self.ingest()
        self.trader().process_next()
        settled_market = market(status='settled', result='no')
        reopened = Database(self.path, 1)
        recovery = self.trader(
            reopened, FakeClient([event()], [settled_market]), 'run-2').start_backend(CHAT_ID)
        self.assertEqual(recovery['settled'], 1)
        self.assertEqual(reopened.trade_history()[0]['final_result'], 'LOSS')
        self.assertEqual(reopened.open_trades(), [])

    def test_telegram_backfill_starts_after_last_persisted_id(self):
        self.trader().start_backend(CHAT_ID)
        self.ingest(message_id=30)
        history = TelegramHistory([TelegramMessage(29), TelegramMessage(30),
                                   TelegramMessage(31), TelegramMessage(32)])
        saved = asyncio.run(backfill_telegram_messages(history, self.trader(), CHAT_ID))
        self.assertEqual(saved, 2)
        self.assertEqual(self.db.telegram_offset(CHAT_ID)['last_persisted_message_id'], 32)
        with self.db.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
