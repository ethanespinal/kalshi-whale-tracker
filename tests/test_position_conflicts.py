from datetime import datetime, timezone
from pathlib import Path
import tempfile
import time
import unittest

from config import Settings
from database import Database
from dashboard_data import query_trades, recent_activity
from reporting import format_report, strategy_comparison
from simulator import PaperTrader
from samples import message
from test_matching import FakeClient, event, market


EVENT = 'KXCS2GAME-26SEP27MGCNIP'


def cs_event(title='magic vs. NIP', ticker=EVENT):
    return event('KXCS2GAME', title, ticker)


def cs_market(ticker, label, event_ticker=EVENT, **extra):
    return market(ticker, label, event_ticker, **extra)


class PositionConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'paper.sqlite3'
        self.settings = Settings(database=self.path)
        self.db = Database(self.path, self.settings.starting_cash)
        self.magic = cs_market('MAGIC', 'magic')
        self.nip = cs_market('NIP', 'NIP')
        self.client = FakeClient([cs_event()], [self.magic, self.nip])
        self.trader = PaperTrader(self.client, self.db, self.settings)

    def ingest(self, key, side, title='Counter-Strike: NIP vs magic (BO3)',
               trader='Whale', win_rate=75, roi=75, price='0.50'):
        raw = message(title, side, price=price, win_rate=win_rate, roi=roi).replace(
            'SampleTrader', trader)
        self.assertTrue(self.db.ingest(key, raw, time.time()))
        self.assertTrue(self.trader.process_next())

    def trades(self):
        return self.db.trade_history()

    def seed_settled(self, trader, wins, losses):
        now = time.time() - 100
        with self.db.connect() as db:
            for index, result in enumerate(['WIN'] * wins + ['LOSS'] * losses):
                source = f'history:{trader}:{index}'
                alert_id = db.execute('''INSERT INTO alerts
                    (source_key,raw_text,received_at,updated_at,status,trader)
                    VALUES (?,?,?,?,?,?)''',
                    (source, 'history', now - index, now, 'traded', trader)).lastrowid
                payout = 500_000 if result == 'WIN' else 0
                net = payout - 259_000
                db.execute('''INSERT INTO trades
                    (alert_id,ticker,side,quantity,price,stake,fee,cost,opened_at,
                     closed_at,payout,final_result,net_pnl,trade_mode,portfolio_status,
                     portfolio_closed_at,market_type,series_ticker)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (alert_id, f'HISTORY-{trader}-{index}', 'yes', 50, 5000,
                     250_000, 9_000, 259_000, now - index, now - index,
                     payout, result, net, 'SHADOW_ONLY', 'SHADOW', None,
                     'game_winner', 'KXCS2GAME'))

    def test_opposite_team_is_shadow_only_with_conflict_context(self):
        self.ingest('chat:1', 'magic', trader='CORGI777')
        self.ingest('chat:2', 'NIP', trader='RN1')
        paper, shadow = self.trades()
        self.assertEqual(paper['trade_mode'], 'PAPER')
        self.assertEqual(shadow['trade_mode'], 'SHADOW_ONLY')
        self.assertEqual(shadow['shadow_reason'],
                         'Opposite side already open; new whale not clearly stronger')
        self.assertEqual(shadow['event_ticker'], paper['event_ticker'])
        self.assertIn('"existing_contract": "magic"', shadow['details'])
        self.assertIn('"new_contract": "nip"', shadow['details'])
        activity = recent_activity(self.path)
        self.assertIn('SHADOW_ONLY — CONFLICTING OPEN POSITION',
                      {row['status'] for row in activity})
        records = query_trades(self.path)
        conflicts = strategy_comparison(records)['Conflicting-position shadow signals']
        self.assertEqual(conflicts['total_trades'], 1)
        self.assertIn('Signals blocked by conflicting open position: 1',
                      format_report(records, self.db.portfolio_stats()))
        shadow_record = next(row for row in records if row['trade_mode'] == 'SHADOW_ONLY')
        self.assertEqual(shadow_record['conflict_existing'], 'magic YES')
        self.assertEqual(shadow_record['conflict_new'], 'nip YES')

    def test_same_contract_yes_then_no_is_shadow_only(self):
        today = datetime.now(timezone.utc).date().isoformat()
        self.ingest('chat:yes', 'Yes', f'Will NIP win on {today}?')
        self.ingest('chat:no', 'No', f'Will NIP win on {today}?')
        self.assertEqual([row['trade_mode'] for row in self.trades()],
                         ['PAPER', 'SHADOW_ONLY'])

    def test_same_side_same_contract_uses_existing_duplicate_rule(self):
        self.ingest('chat:1', 'magic', trader='First')
        self.ingest('chat:2', 'magic', trader='Second')
        self.assertEqual(len(self.trades()), 1)
        with self.db.connect() as db:
            second = db.execute("SELECT * FROM alerts WHERE source_key='chat:2'").fetchone()
        self.assertEqual(second['reason'], 'contract already held')

    def test_different_event_is_allowed(self):
        other_event = cs_event('Alpha vs. Beta', 'KXCS2GAME-26SEP27ALPBET')
        other_market = cs_market('ALPHA', 'Alpha', other_event['event_ticker'])
        self.client.events.append(other_event)
        self.client.markets.append(other_market)
        self.ingest('chat:1', 'magic')
        self.ingest('chat:2', 'Alpha', 'Counter-Strike: Alpha vs Beta (BO3)')
        self.assertEqual([row['trade_mode'] for row in self.trades()], ['PAPER', 'PAPER'])

    def test_settled_original_no_longer_blocks_opposite_outcome(self):
        self.ingest('chat:1', 'magic')
        self.assertTrue(self.db.settle(self.trades()[0]['id'], 'yes'))
        self.ingest('chat:2', 'NIP')
        self.assertEqual([row['trade_mode'] for row in self.trades()], ['PAPER', 'PAPER'])

    def test_restart_reads_open_conflict_from_sqlite(self):
        self.ingest('chat:1', 'magic')
        reopened = Database(self.path, 1)
        restarted = PaperTrader(
            FakeClient([cs_event()], [self.magic, self.nip]), reopened, self.settings)
        self.trader = restarted
        self.db = reopened
        self.ingest('chat:2', 'NIP')
        self.assertEqual(self.trades()[1]['trade_mode'], 'SHADOW_ONLY')

    def test_conflicting_shadow_trade_still_settles(self):
        self.ingest('chat:1', 'magic')
        self.ingest('chat:2', 'NIP')
        for row in self.client.markets:
            row.update(status='settled', result='yes' if row['ticker'] == 'NIP' else 'no')
        self.assertEqual(self.trader.settle(), 2)
        shadow = next(row for row in self.trades() if row['trade_mode'] == 'SHADOW_ONLY')
        self.assertEqual(shadow['final_result'], 'WIN')
        self.assertIsNotNone(shadow['net_pnl'])

    def test_stronger_opposite_whale_flips_and_both_signals_settle(self):
        self.magic.update(yes_bid_dollars='.4900', yes_bid_size_fp='100')
        self.ingest('chat:1', 'magic', trader='RN1', win_rate=50, roi=50)
        self.ingest('chat:2', 'NIP', trader='CORGI777', win_rate=100, roi=300)
        old, new = self.trades()
        self.assertEqual((old['trade_mode'], old['portfolio_status']), ('PAPER', 'CLOSED'))
        self.assertEqual((new['trade_mode'], new['portfolio_status']), ('PAPER', 'OPEN'))
        self.assertEqual(old['portfolio_close_reason'],
                         'Stronger opposite whale exceeded reversal threshold')
        self.assertIsNotNone(old['portfolio_exit_price'])
        self.assertIsNotNone(old['portfolio_realized_pnl'])
        self.assertEqual(self.db.open_trades()[0]['id'], new['id'])
        self.assertIn('"action": "flip"', new['details'])
        records = query_trades(self.path)
        self.assertEqual({row['conflict_action'] for row in records}, {'flip'})
        self.assertIn('REVERSAL OPEN', {row['status'] for row in recent_activity(self.path)})
        self.assertEqual(self.db.portfolio_stats()['open_exposure'],
                         self.settings.fixed_stake)
        for row in self.client.markets:
            row.update(status='settled', result='yes' if row['ticker'] == 'NIP' else 'no')
        self.assertEqual(self.trader.settle(), 2)
        old, new = self.trades()
        self.assertEqual((old['final_result'], new['final_result']), ('LOSS', 'WIN'))
        self.assertEqual(old['portfolio_status'], 'CLOSED')

    def test_tiny_sample_whale_does_not_override_large_sample(self):
        self.seed_settled('Established', 20, 10)
        self.seed_settled('Tiny', 3, 0)
        self.magic.update(yes_bid_dollars='.4900', yes_bid_size_fp='100')
        self.ingest('chat:1', 'magic', trader='Established', win_rate=70, roi=100)
        self.ingest('chat:2', 'NIP', trader='Tiny', win_rate=100, roi=300)
        current, shadow = self.trades()[-2:]
        self.assertEqual(current['portfolio_status'], 'OPEN')
        self.assertEqual(shadow['trade_mode'], 'SHADOW_ONLY')
        self.assertIn('not clearly stronger', shadow['shadow_reason'])

    def test_stronger_reversal_is_blocked_without_exit_liquidity(self):
        self.ingest('chat:1', 'magic', trader='RN1', win_rate=50, roi=50)
        self.ingest('chat:2', 'NIP', trader='CORGI777', win_rate=100, roi=300)
        old, shadow = self.trades()
        self.assertEqual(old['portfolio_status'], 'OPEN')
        self.assertEqual(shadow['trade_mode'], 'SHADOW_ONLY')
        self.assertIn('cannot be exited', shadow['shadow_reason'])

    def test_opposite_signal_with_bad_slippage_cannot_trigger_reversal(self):
        self.magic.update(yes_bid_dollars='.4900', yes_bid_size_fp='100')
        self.nip.update(yes_ask_dollars='.5100')
        self.ingest('chat:1', 'magic', trader='RN1', win_rate=50, roi=50)
        self.ingest('chat:2', 'NIP', trader='CORGI777', win_rate=100, roi=300,
                    price='0.50')
        old, shadow = self.trades()
        self.assertEqual(old['portfolio_status'], 'OPEN')
        self.assertEqual(shadow['trade_mode'], 'SHADOW_ONLY')
        self.assertEqual(shadow['shadow_reason'],
                         'Positive slippage above strategy maximum')

    def test_reversal_cost_limit_blocks_economically_bad_flip(self):
        self.magic.update(yes_bid_dollars='.4900', yes_bid_size_fp='100')
        strict = Settings(database=self.path, max_reversal_cost=0)
        self.trader = PaperTrader(self.client, self.db, strict)
        self.ingest('chat:1', 'magic', trader='RN1', win_rate=50, roi=50)
        self.ingest('chat:2', 'NIP', trader='CORGI777', win_rate=100, roi=300)
        old, shadow = self.trades()
        self.assertEqual(old['portfolio_status'], 'OPEN')
        self.assertEqual(shadow['trade_mode'], 'SHADOW_ONLY')
        self.assertIn('fees/slippage exceed limit', shadow['shadow_reason'])

    def test_restart_can_flip_persisted_open_position(self):
        self.magic.update(yes_bid_dollars='.4900', yes_bid_size_fp='100')
        self.ingest('chat:1', 'magic', trader='RN1', win_rate=50, roi=50)
        self.db = Database(self.path, self.settings.starting_cash)
        self.trader = PaperTrader(
            FakeClient([cs_event()], [self.magic, self.nip]), self.db, self.settings)
        self.ingest('chat:2', 'NIP', trader='CORGI777', win_rate=100, roi=300)
        self.assertEqual([row['portfolio_status'] for row in self.trades()],
                         ['CLOSED', 'OPEN'])


if __name__ == '__main__':
    unittest.main()
