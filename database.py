"""SQLite inbox and paper ledger. Each write is committed atomically."""
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP
import json
import sqlite3
import time


class Database:
    def __init__(self, path, starting_cash):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
                    raw_text TEXT NOT NULL, received_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    reason TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}',
                    session_id TEXT NOT NULL DEFAULT 'legacy',
                    strategy_version TEXT NOT NULL DEFAULT 'legacy',
                    strategy_mode TEXT NOT NULL DEFAULT 'legacy',
                    code_version TEXT NOT NULL DEFAULT 'legacy',
                    backend_run_id TEXT NOT NULL DEFAULT 'legacy',
                    telegram_chat_id INTEGER, telegram_message_id INTEGER,
                    parsed_alert TEXT, market TEXT, market_type TEXT, trader TEXT,
                    whale_side TEXT, whale_price REAL, whale_amount REAL,
                    whale_win_rate REAL, whale_roi REAL,
                    match_status TEXT, match_reason TEXT,
                    matched_event_ticker TEXT, matched_market_ticker TEXT,
                    matched_market_title TEXT, yes_no_direction TEXT,
                    claimed_at REAL, processing_run_id TEXT, processed_at REAL,
                    recovery_of_alert_id INTEGER REFERENCES alerts(id)
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY, alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
                    ticker TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('yes', 'no')),
                    quantity REAL NOT NULL CHECK(quantity > 0), price INTEGER NOT NULL,
                    stake INTEGER NOT NULL, fee INTEGER NOT NULL, cost INTEGER NOT NULL, opened_at REAL NOT NULL,
                    closed_at REAL, payout INTEGER, result TEXT,
                    final_result TEXT, settlement_value INTEGER,
                    gross_pnl INTEGER, net_pnl INTEGER, roi REAL,
                    trade_mode TEXT NOT NULL DEFAULT 'PAPER',
                    shadow_reason TEXT NOT NULL DEFAULT '',
                    whale_win_rate REAL, whale_roi REAL, hours_to_close REAL,
                    resolution_time_source TEXT, effective_resolution_time TEXT,
                    can_close_early INTEGER,
                    session_id TEXT NOT NULL DEFAULT 'legacy',
                    strategy_version TEXT NOT NULL DEFAULT 'legacy',
                    strategy_mode TEXT NOT NULL DEFAULT 'legacy',
                    code_version TEXT NOT NULL DEFAULT 'legacy',
                    backend_run_id TEXT NOT NULL DEFAULT 'legacy',
                    is_recovered INTEGER NOT NULL DEFAULT 0,
                    recovery_original_alert_id INTEGER REFERENCES alerts(id),
                    event_ticker TEXT,
                    market_type TEXT,
                    winner_contract_key TEXT,
                    series_ticker TEXT,
                    portfolio_status TEXT NOT NULL DEFAULT 'OPEN',
                    portfolio_closed_at REAL,
                    portfolio_exit_price INTEGER,
                    portfolio_exit_fee INTEGER,
                    portfolio_exit_proceeds INTEGER,
                    portfolio_realized_pnl INTEGER,
                    portfolio_close_reason TEXT,
                    portfolio_close_details TEXT
                );
                CREATE TABLE IF NOT EXISTS backend_status (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    session_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    strategy_mode TEXT NOT NULL DEFAULT 'legacy',
                    code_version TEXT NOT NULL, backend_run_id TEXT NOT NULL DEFAULT 'legacy',
                    started_at REAL NOT NULL,
                    last_heartbeat REAL NOT NULL, last_telegram_alert REAL,
                    last_kalshi_lookup REAL, last_settlement_refresh REAL,
                    current_open_exposure INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_offsets (
                    chat_id INTEGER PRIMARY KEY,
                    last_persisted_message_id INTEGER NOT NULL DEFAULT 0,
                    last_processed_message_id INTEGER NOT NULL DEFAULT 0,
                    last_received_at REAL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backend_runs (
                    backend_run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
                    strategy_mode TEXT NOT NULL DEFAULT 'legacy',
                    code_version TEXT NOT NULL, started_at REAL NOT NULL,
                    stopped_at REAL, last_heartbeat REAL NOT NULL,
                    recovered_alerts INTEGER NOT NULL DEFAULT 0,
                    recovered_open_trades INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS recovery_attempts (
                    id INTEGER PRIMARY KEY,
                    original_alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
                    recovery_alert_id INTEGER UNIQUE REFERENCES alerts(id),
                    original_status TEXT NOT NULL,
                    original_reason TEXT NOT NULL DEFAULT '',
                    original_strategy_version TEXT NOT NULL DEFAULT 'legacy',
                    original_code_version TEXT NOT NULL DEFAULT 'legacy',
                    original_received_at REAL NOT NULL,
                    recovery_attempted_at REAL NOT NULL,
                    recovery_strategy_version TEXT NOT NULL,
                    recovery_code_version TEXT NOT NULL,
                    recovery_match_status TEXT,
                    recovery_trade_created INTEGER NOT NULL DEFAULT 0,
                    recovery_reason TEXT NOT NULL DEFAULT '',
                    recovery_outcome TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS alert_queue ON alerts(status, next_attempt);
            ''')
            self._migrate_alerts(db)
            self._migrate_trades(db)
            self._migrate_backend_status(db)
            self._migrate_backend_runs(db)
            self._backfill_telegram_offsets(db)
            db.execute('CREATE INDEX IF NOT EXISTS alert_telegram ON alerts(telegram_chat_id,telegram_message_id)')
            db.execute('CREATE INDEX IF NOT EXISTS alert_processing_run ON alerts(processing_run_id,status)')
            db.execute('CREATE INDEX IF NOT EXISTS trade_strategy ON trades(strategy_version, opened_at)')
            db.execute('CREATE INDEX IF NOT EXISTS trade_session ON trades(session_id, opened_at)')
            db.execute('CREATE INDEX IF NOT EXISTS trade_mode_state ON trades(trade_mode, payout)')
            db.execute('CREATE INDEX IF NOT EXISTS alert_recovery_source ON alerts(recovery_of_alert_id)')
            db.execute('CREATE INDEX IF NOT EXISTS trade_recovery ON trades(is_recovered, opened_at)')
            db.execute('CREATE INDEX IF NOT EXISTS trade_open_event ON trades(event_ticker,market_type,payout,trade_mode)')
            db.execute('''CREATE INDEX IF NOT EXISTS trade_portfolio_event
                ON trades(event_ticker,market_type,portfolio_status,trade_mode)''')
            db.execute("INSERT OR IGNORE INTO settings VALUES ('starting_cash', ?)", (starting_cash,))
            # One-time migration from the previous V1 default. Custom bankrolls
            # are never overwritten when the database is reopened.
            if starting_cash == 100_000_000:
                db.execute("UPDATE settings SET value=? WHERE key='starting_cash' AND value=?",
                           (starting_cash, 10_000_000))

    @staticmethod
    def _migrate_alerts(db):
        columns = {row['name'] for row in db.execute('PRAGMA table_info(alerts)')}
        additions = {
            'session_id': "TEXT NOT NULL DEFAULT 'legacy'",
            'strategy_version': "TEXT NOT NULL DEFAULT 'legacy'",
            'strategy_mode': "TEXT NOT NULL DEFAULT 'legacy'",
            'code_version': "TEXT NOT NULL DEFAULT 'legacy'",
            'backend_run_id': "TEXT NOT NULL DEFAULT 'legacy'",
            'telegram_chat_id': 'INTEGER', 'telegram_message_id': 'INTEGER',
            'parsed_alert': 'TEXT', 'market': 'TEXT', 'market_type': 'TEXT',
            'trader': 'TEXT', 'whale_side': 'TEXT', 'whale_price': 'REAL',
            'whale_amount': 'REAL', 'whale_win_rate': 'REAL', 'whale_roi': 'REAL',
            'match_status': 'TEXT', 'match_reason': 'TEXT',
            'matched_event_ticker': 'TEXT', 'matched_market_ticker': 'TEXT',
            'matched_market_title': 'TEXT', 'yes_no_direction': 'TEXT',
            'claimed_at': 'REAL', 'processing_run_id': 'TEXT', 'processed_at': 'REAL',
            'recovery_of_alert_id': 'INTEGER REFERENCES alerts(id)',
        }
        for name, kind in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE alerts ADD COLUMN {name} {kind}")

    @staticmethod
    def _migrate_trades(db):
        """Add reporting fields without replacing an existing paper ledger."""
        columns = {row['name'] for row in db.execute('PRAGMA table_info(trades)')}
        additions = {
            'stake': 'INTEGER',
            'final_result': 'TEXT',
            'settlement_value': 'INTEGER',
            'gross_pnl': 'INTEGER',
            'net_pnl': 'INTEGER',
            'roi': 'REAL',
            'trade_mode': "TEXT NOT NULL DEFAULT 'PAPER'",
            'shadow_reason': "TEXT NOT NULL DEFAULT ''",
            'whale_win_rate': 'REAL',
            'whale_roi': 'REAL',
            'hours_to_close': 'REAL',
            'resolution_time_source': 'TEXT',
            'effective_resolution_time': 'TEXT',
            'can_close_early': 'INTEGER',
            'session_id': "TEXT NOT NULL DEFAULT 'legacy'",
            'strategy_version': "TEXT NOT NULL DEFAULT 'legacy'",
            'strategy_mode': "TEXT NOT NULL DEFAULT 'legacy'",
            'code_version': "TEXT NOT NULL DEFAULT 'legacy'",
            'backend_run_id': "TEXT NOT NULL DEFAULT 'legacy'",
            'is_recovered': 'INTEGER NOT NULL DEFAULT 0',
            'recovery_original_alert_id': 'INTEGER REFERENCES alerts(id)',
            'event_ticker': 'TEXT',
            'market_type': 'TEXT',
            'winner_contract_key': 'TEXT',
            'series_ticker': 'TEXT',
            'portfolio_status': "TEXT NOT NULL DEFAULT 'OPEN'",
            'portfolio_closed_at': 'REAL',
            'portfolio_exit_price': 'INTEGER',
            'portfolio_exit_fee': 'INTEGER',
            'portfolio_exit_proceeds': 'INTEGER',
            'portfolio_realized_pnl': 'INTEGER',
            'portfolio_close_reason': 'TEXT',
            'portfolio_close_details': 'TEXT',
        }
        for name, kind in additions.items():
            if name not in columns:
                db.execute(f'ALTER TABLE trades ADD COLUMN {name} {kind}')
        db.execute('UPDATE trades SET stake=ROUND(quantity * price) WHERE stake IS NULL')
        # Older ledgers already have enough information to reconstruct binary
        # settlement accounting exactly.
        db.execute('''UPDATE trades SET
            settlement_value=CAST(payout / quantity AS INTEGER),
            gross_pnl=payout - stake,
            net_pnl=payout - cost,
            roi=CAST(payout - cost AS REAL) / stake,
            final_result=CASE
                WHEN lower(COALESCE(result, '')) IN ('void', 'cancelled', 'canceled') THEN 'VOID'
                WHEN payout - stake > 0 THEN 'WIN'
                WHEN payout - stake < 0 THEN 'LOSS'
                ELSE 'PUSH'
            END
            WHERE payout IS NOT NULL AND final_result IS NULL''')
        # Recover canonical event/contract identity from the already-persisted
        # match payload. Historical rows are never replaced.
        for row in db.execute('''SELECT t.id,a.details FROM trades t
            JOIN alerts a ON a.id=t.alert_id
            WHERE t.event_ticker IS NULL OR t.market_type IS NULL OR
                  t.series_ticker IS NULL'''):
            try:
                details = json.loads(row['details'] or '{}')
            except (TypeError, ValueError):
                details = {}
            match = details.get('match') or {}
            alert = details.get('alert') or {}
            market = match.get('market') or {}
            contract_key = str(
                market.get('yes_sub_title') or market.get('title') or '').strip().casefold()
            db.execute('''UPDATE trades SET event_ticker=COALESCE(event_ticker,?),
                market_type=COALESCE(market_type,?),
                winner_contract_key=COALESCE(winner_contract_key,?),
                series_ticker=COALESCE(series_ticker,?) WHERE id=?''',
                ((match.get('event') or {}).get('event_ticker'), alert.get('market_type'),
                 contract_key or None, (match.get('event') or {}).get('series_ticker'), row['id']))
        db.execute("""UPDATE trades SET portfolio_status=CASE
            WHEN trade_mode='SHADOW_ONLY' THEN 'SHADOW'
            WHEN payout IS NOT NULL THEN 'SETTLED'
            ELSE 'OPEN' END
            WHERE portfolio_status IS NULL OR portfolio_status='' OR
            (trade_mode='SHADOW_ONLY' AND portfolio_status='OPEN') OR
            (payout IS NOT NULL AND portfolio_status='OPEN')""")
        db.execute("""UPDATE trades SET portfolio_closed_at=closed_at
            WHERE portfolio_status='SETTLED' AND portfolio_closed_at IS NULL""")

    @staticmethod
    def _migrate_backend_status(db):
        columns = {row['name'] for row in db.execute('PRAGMA table_info(backend_status)')}
        if 'backend_run_id' not in columns:
            db.execute("ALTER TABLE backend_status ADD COLUMN backend_run_id TEXT NOT NULL DEFAULT 'legacy'")
        if 'strategy_mode' not in columns:
            db.execute("ALTER TABLE backend_status ADD COLUMN strategy_mode TEXT NOT NULL DEFAULT 'legacy'")

    @staticmethod
    def _migrate_backend_runs(db):
        columns = {row['name'] for row in db.execute('PRAGMA table_info(backend_runs)')}
        if 'strategy_mode' not in columns:
            db.execute("ALTER TABLE backend_runs ADD COLUMN strategy_mode TEXT NOT NULL DEFAULT 'legacy'")

    @staticmethod
    def _source_ids(source_key):
        parts = str(source_key).rsplit(':', 1)
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
        return None, None

    @classmethod
    def _backfill_telegram_offsets(cls, db):
        """Seed offsets from existing durable source keys without altering alerts."""
        offsets = {}
        for row in db.execute('SELECT source_key,received_at,status FROM alerts'):
            chat_id, message_id = cls._source_ids(row['source_key'])
            if chat_id is None:
                continue
            state = offsets.setdefault(chat_id, [0, 0, None])
            state[0] = max(state[0], message_id)
            if row['status'] not in ('pending', 'processing', 'retryable'):
                state[1] = max(state[1], message_id)
            state[2] = max(state[2] or 0, row['received_at'])
        now = time.time()
        for chat_id, (persisted, processed, received_at) in offsets.items():
            db.execute('''INSERT INTO telegram_offsets
                (chat_id,last_persisted_message_id,last_processed_message_id,last_received_at,updated_at)
                VALUES (?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET
                last_persisted_message_id=MAX(last_persisted_message_id,excluded.last_persisted_message_id),
                last_processed_message_id=MAX(last_processed_message_id,excluded.last_processed_message_id),
                last_received_at=MAX(COALESCE(last_received_at,0),COALESCE(excluded.last_received_at,0)),
                updated_at=excluded.updated_at''',
                (chat_id, persisted, processed, received_at, now))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=NORMAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _advance_offset(db, chat_id, message_id, received_at=None,
                        persisted=False, processed=False):
        if chat_id is None or message_id is None:
            return
        persisted_id = message_id if persisted else 0
        processed_id = message_id if processed else 0
        db.execute('''INSERT INTO telegram_offsets
            (chat_id,last_persisted_message_id,last_processed_message_id,last_received_at,updated_at)
            VALUES (?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET
            last_persisted_message_id=MAX(last_persisted_message_id,excluded.last_persisted_message_id),
            last_processed_message_id=MAX(last_processed_message_id,excluded.last_processed_message_id),
            last_received_at=MAX(COALESCE(last_received_at,0),COALESCE(excluded.last_received_at,0)),
            updated_at=excluded.updated_at''',
            (chat_id, persisted_id, processed_id, received_at, time.time()))

    def ingest(self, source_key, raw_text, received_at, session_id='legacy',
               strategy_version='legacy', code_version='legacy', backend_run_id='legacy',
               telegram_chat_id=None, telegram_message_id=None,
               strategy_mode='legacy'):
        source_chat, source_message = self._source_ids(source_key)
        telegram_chat_id = telegram_chat_id if telegram_chat_id is not None else source_chat
        telegram_message_id = (telegram_message_id if telegram_message_id is not None
                               else source_message)
        with self.connect() as db:
            result = db.execute('''INSERT OR IGNORE INTO alerts
                (source_key, raw_text, received_at, updated_at, session_id,
                 strategy_version, strategy_mode, code_version, backend_run_id,
                 telegram_chat_id,telegram_message_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (source_key, raw_text, received_at, time.time(), session_id,
                 strategy_version, strategy_mode, code_version, backend_run_id,
                 telegram_chat_id, telegram_message_id))
            self._advance_offset(db, telegram_chat_id, telegram_message_id, received_at,
                                 persisted=True)
            return result.rowcount == 1

    def get_or_create_session_id(self, default, configured=None):
        """Return one durable experiment session across ordinary backend restarts."""
        with self.connect() as db:
            row = db.execute("SELECT value FROM runtime_state WHERE key='experiment_session_id'").fetchone()
            if configured:
                value = configured
            elif row:
                value = row['value']
            else:
                previous = db.execute('''SELECT session_id FROM backend_status WHERE id=1
                    AND session_id != 'legacy' ''').fetchone()
                if previous is None:
                    previous = db.execute('''SELECT session_id FROM trades
                        WHERE session_id != 'legacy' ORDER BY opened_at DESC LIMIT 1''').fetchone()
                if previous is None:
                    previous = db.execute('''SELECT session_id FROM alerts
                        WHERE session_id != 'legacy' ORDER BY id DESC LIMIT 1''').fetchone()
                value = previous['session_id'] if previous else default
            db.execute('''INSERT INTO runtime_state(key,value,updated_at) VALUES ('experiment_session_id',?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
                (value, time.time()))
            return value

    def start_backend(self, session_id, strategy_version, code_version,
                      backend_run_id='legacy', recovered_alerts=0,
                      recovered_open_trades=0, strategy_mode='legacy'):
        now = time.time()
        exposure = self.portfolio_stats()['open_exposure']
        with self.connect() as db:
            db.execute('''INSERT INTO backend_status
                (id,session_id,strategy_version,strategy_mode,code_version,backend_run_id,started_at,last_heartbeat,
                 current_open_exposure) VALUES (1,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET session_id=excluded.session_id,
                strategy_version=excluded.strategy_version, code_version=excluded.code_version,
                strategy_mode=excluded.strategy_mode,
                backend_run_id=excluded.backend_run_id,
                started_at=excluded.started_at, last_heartbeat=excluded.last_heartbeat,
                current_open_exposure=excluded.current_open_exposure''',
                (session_id, strategy_version, strategy_mode, code_version,
                 backend_run_id, now, now, exposure))
            db.execute('''INSERT INTO backend_runs
                (backend_run_id,session_id,strategy_version,strategy_mode,code_version,started_at,last_heartbeat,
                 recovered_alerts,recovered_open_trades) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(backend_run_id) DO UPDATE SET
                last_heartbeat=excluded.last_heartbeat,
                strategy_mode=excluded.strategy_mode,
                recovered_alerts=excluded.recovered_alerts,
                recovered_open_trades=excluded.recovered_open_trades''',
                (backend_run_id, session_id, strategy_version, strategy_mode, code_version, now, now,
                 recovered_alerts, recovered_open_trades))

    def stop_backend(self, backend_run_id):
        with self.connect() as db:
            db.execute('UPDATE backend_runs SET stopped_at=?,last_heartbeat=? WHERE backend_run_id=?',
                       (time.time(), time.time(), backend_run_id))

    def update_backend_status(self, **values):
        allowed = {'last_heartbeat', 'last_telegram_alert', 'last_kalshi_lookup',
                   'last_settlement_refresh', 'current_open_exposure'}
        if not values or not set(values) <= allowed:
            raise ValueError('Invalid backend status field')
        assignments = ','.join(f'{name}=?' for name in values)
        with self.connect() as db:
            db.execute(f'UPDATE backend_status SET {assignments} WHERE id=1',
                       tuple(values.values()))
            if 'last_heartbeat' in values:
                db.execute('''UPDATE backend_runs SET last_heartbeat=? WHERE backend_run_id=(
                    SELECT backend_run_id FROM backend_status WHERE id=1)''',
                    (values['last_heartbeat'],))

    def backend_status(self):
        with self.connect() as db:
            row = db.execute('SELECT * FROM backend_status WHERE id=1').fetchone()
            return dict(row) if row else None

    def recover_processing(self):
        """A new backend owns the queue, so prior in-flight leases are orphaned."""
        with self.connect() as db:
            result = db.execute('''UPDATE alerts SET status='retryable',next_attempt=0,
                processing_run_id=NULL WHERE status='processing'
                AND recovery_of_alert_id IS NULL''')
            return result.rowcount

    def recovery_counts(self):
        """Return durable queue/position counts for startup diagnostics."""
        with self.connect() as db:
            return {
                'queued_alerts': db.execute("""SELECT COUNT(*) FROM alerts
                    WHERE status IN ('pending','retryable','processing')
                    AND recovery_of_alert_id IS NULL""").fetchone()[0],
                'open_trades': db.execute(
                    """SELECT COUNT(*) FROM trades WHERE portfolio_status='OPEN'
                    AND trade_mode='PAPER'""").fetchone()[0],
            }

    def claim(self, backend_run_id=None):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Recover an interrupted worker after a ten-minute lease.
            db.execute("""UPDATE alerts SET status='retryable' WHERE status='processing'
                AND recovery_of_alert_id IS NULL AND updated_at < ?""",
                       (now - 600,))
            row = db.execute('''SELECT * FROM alerts WHERE status IN ('pending', 'retryable')
                AND recovery_of_alert_id IS NULL
                AND next_attempt <= ? ORDER BY id LIMIT 1''', (now,)).fetchone()
            if row is None:
                return None
            db.execute('''UPDATE alerts SET status='processing', attempts=attempts+1,
                updated_at=?,claimed_at=?,processing_run_id=? WHERE id=?''',
                (now, now, backend_run_id, row['id']))
            result = dict(row)
            result['attempts'] += 1
            return result

    def claim_alert(self, alert_id, backend_run_id=None):
        """Claim one explicitly selected row without disturbing the live queue."""
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT * FROM alerts WHERE id=?
                AND status IN ('pending','retryable') AND next_attempt<=?''',
                             (alert_id, now)).fetchone()
            if row is None:
                return None
            updated = db.execute('''UPDATE alerts SET status='processing',attempts=attempts+1,
                updated_at=?,claimed_at=?,processing_run_id=? WHERE id=?
                AND status IN ('pending','retryable')''',
                                 (now, now, backend_run_id, alert_id))
            if not updated.rowcount:
                return None
            result = dict(row)
            result['attempts'] += 1
            return result

    def recovery_candidates(self, limit=None):
        """Return only historical matcher/coverage failures, oldest first."""
        statuses = ('unmatched', 'unsupported', 'unsupported_series', 'event_not_found',
                    'contract_not_found', 'name_alias_miss', 'line_not_found',
                    'SPORT_NOT_DETECTED', 'LEAGUE_NOT_DETECTED',
                    'UNSUPPORTED_MARKET_SCOPE', 'SERIES_NOT_CONFIGURED',
                    'NO_EVENTS_RETURNED', 'EVENT_PARTICIPANTS_MISMATCH',
                    'EVENT_DATE_MISMATCH', 'CONTRACT_NOT_FOUND', 'LINE_NOT_FOUND',
                    'AMBIGUOUS_MATCH')
        placeholders = ','.join('?' for _ in statuses)
        sql = f'''SELECT a.* FROM alerts a
            LEFT JOIN recovery_attempts recovery ON recovery.original_alert_id=a.id
            WHERE a.status IN ({placeholders})
            AND a.recovery_of_alert_id IS NULL
            AND NOT EXISTS (SELECT 1 FROM trades t WHERE t.alert_id=a.id)
            AND (recovery.id IS NULL OR recovery.recovery_outcome='pending')
            ORDER BY a.received_at,a.id'''
        params = list(statuses)
        if limit is not None:
            sql += ' LIMIT ?'
            params.append(int(limit))
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def prepare_recovery(self, original_alert_id, session_id, strategy_version,
                         strategy_mode, code_version, backend_run_id):
        """Create or reset a linked processing row while preserving the original."""
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            original = db.execute('SELECT * FROM alerts WHERE id=? AND recovery_of_alert_id IS NULL',
                                  (original_alert_id,)).fetchone()
            if original is None:
                return None
            attempt = db.execute('SELECT * FROM recovery_attempts WHERE original_alert_id=?',
                                 (original_alert_id,)).fetchone()
            if attempt and attempt['recovery_alert_id']:
                recovery_id = attempt['recovery_alert_id']
                recovery = db.execute('SELECT status FROM alerts WHERE id=?',
                                      (recovery_id,)).fetchone()
                if recovery and (recovery['status'] in ('traded', 'shadow_only') or
                                 db.execute('SELECT 1 FROM trades WHERE alert_id=?',
                                            (recovery_id,)).fetchone()):
                    return {'alert_id': recovery_id, 'process': False,
                            'outcome': attempt['recovery_outcome']}
                db.execute('''UPDATE alerts SET status='pending',attempts=0,next_attempt=0,
                    updated_at=?,received_at=?,reason='',details='{}',parsed_alert=NULL,
                    match_status=NULL,match_reason=NULL,matched_event_ticker=NULL,
                    matched_market_ticker=NULL,matched_market_title=NULL,yes_no_direction=NULL,
                    claimed_at=NULL,processing_run_id=NULL,processed_at=NULL,
                    session_id=?,strategy_version=?,strategy_mode=?,code_version=?,backend_run_id=?
                    WHERE id=?''',
                           (now, now, session_id, strategy_version, strategy_mode,
                            code_version, backend_run_id, recovery_id))
                db.execute('''UPDATE recovery_attempts SET recovery_attempted_at=?,
                    recovery_strategy_version=?,recovery_code_version=?,
                    recovery_match_status=NULL,recovery_trade_created=0,recovery_reason='',
                    recovery_outcome='pending',attempt_count=attempt_count+1,updated_at=?
                    WHERE original_alert_id=?''',
                           (now, strategy_version, code_version, now, original_alert_id))
                return {'alert_id': recovery_id, 'process': True, 'outcome': 'pending'}

            source_key = f'recovery:{original_alert_id}'
            inserted = db.execute('''INSERT INTO alerts
                (source_key,raw_text,received_at,updated_at,session_id,strategy_version,
                 strategy_mode,code_version,backend_run_id,recovery_of_alert_id)
                VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (source_key, original['raw_text'], now, now, session_id, strategy_version,
                 strategy_mode, code_version, backend_run_id, original_alert_id))
            recovery_id = inserted.lastrowid
            db.execute('''INSERT INTO recovery_attempts
                (original_alert_id,recovery_alert_id,original_status,original_reason,
                 original_strategy_version,original_code_version,original_received_at,
                 recovery_attempted_at,recovery_strategy_version,recovery_code_version,
                 recovery_outcome,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (original_alert_id, recovery_id, original['status'], original['reason'],
                 original['strategy_version'], original['code_version'], original['received_at'],
                 now, strategy_version, code_version, 'pending', now))
            return {'alert_id': recovery_id, 'process': True, 'outcome': 'pending'}

    def finalize_recovery(self, recovery_alert_id):
        """Copy the linked decision into the durable recovery audit row."""
        with self.connect() as db:
            alert = db.execute('SELECT * FROM alerts WHERE id=? AND recovery_of_alert_id IS NOT NULL',
                               (recovery_alert_id,)).fetchone()
            if alert is None:
                return None
            trade = db.execute('SELECT * FROM trades WHERE alert_id=?',
                               (recovery_alert_id,)).fetchone()
            reason = (trade['shadow_reason'] if trade and trade['trade_mode'] == 'SHADOW_ONLY'
                      else alert['reason']) or ''
            if alert['status'] in ('pending', 'processing', 'retryable'):
                outcome, created = 'pending', 0
            elif trade and trade['trade_mode'] == 'PAPER':
                outcome, created = 'paper_filled', 1
            elif trade and 'positive slippage' in reason.lower():
                outcome, created = 'price_now_worse', 0
            elif trade:
                outcome, created = 'shadow_only', 0
            elif alert['status'] in ('stale',) or any(
                    phrase in reason.lower() for phrase in
                    ('not open', 'no longer open', 'already ended', 'closed')):
                outcome, created = 'event_gone', 0
            elif alert['match_status'] in ('matched', 'MATCHED'):
                outcome, created = 'execution_rejected', 0
            elif alert['status'] in ('unmatched', 'unsupported', 'unsupported_series',
                                     'event_not_found', 'contract_not_found',
                                     'name_alias_miss', 'ambiguous_match',
                                     'SPORT_NOT_DETECTED', 'LEAGUE_NOT_DETECTED',
                                     'UNSUPPORTED_MARKET_SCOPE', 'SERIES_NOT_CONFIGURED',
                                     'NO_EVENTS_RETURNED', 'EVENT_PARTICIPANTS_MISMATCH',
                                     'EVENT_DATE_MISMATCH', 'CONTRACT_NOT_FOUND',
                                     'LINE_NOT_FOUND', 'AMBIGUOUS_MATCH'):
                outcome, created = 'still_unmatched', 0
            else:
                outcome, created = 'rejected', 0
            db.execute('''UPDATE recovery_attempts SET recovery_match_status=?,
                recovery_trade_created=?,recovery_reason=?,recovery_outcome=?,updated_at=?
                WHERE recovery_alert_id=?''',
                (alert['match_status'] or alert['status'], created, reason, outcome,
                 time.time(), recovery_alert_id))
            return outcome

    def recovery_summary(self):
        with self.connect() as db:
            counts = dict(db.execute('''SELECT recovery_outcome,COUNT(*)
                FROM recovery_attempts GROUP BY recovery_outcome''').fetchall())
            total = db.execute('SELECT COUNT(*) FROM recovery_attempts').fetchone()[0]
            # STALE means the exact event/contract/direction was recovered but
            # the contract was already closed, so it counts as a historical
            # rematch while remaining ineligible for entry.
            matched = db.execute("""SELECT COUNT(*) FROM recovery_attempts
                WHERE recovery_match_status IN ('matched','stale','MATCHED')""").fetchone()[0]
        return {
            'historical_alerts_reconsidered': total,
            'newly_matched': matched,
            'recovered_paper_trades': counts.get('paper_filled', 0),
            'price_worsened': counts.get('price_now_worse', 0),
            'opportunity_gone': counts.get('event_gone', 0),
            'still_unmatched': counts.get('still_unmatched', 0),
            'other_rejected': sum(value for key, value in counts.items()
                                  if key not in {'paper_filled', 'price_now_worse',
                                                 'event_gone', 'still_unmatched'}),
        }

    def checkpoint(self, alert_id, details, parsed=None, match=None):
        """Persist parse/match progress while an alert remains safely claimable."""
        values = {'details': json.dumps(details or {}, sort_keys=True), 'updated_at': time.time()}
        if parsed:
            values.update({
                'parsed_alert': json.dumps(parsed, sort_keys=True),
                'market': parsed.get('market'), 'market_type': parsed.get('market_type'),
                'trader': parsed.get('trader'), 'whale_side': parsed.get('side'),
                'whale_price': parsed.get('price'), 'whale_amount': parsed.get('amount'),
                'whale_win_rate': parsed.get('win_rate'), 'whale_roi': parsed.get('roi'),
            })
        if match:
            event = match.get('event') or {}
            market = match.get('market') or {}
            values.update({
                'match_status': match.get('status'), 'match_reason': match.get('reason'),
                'matched_event_ticker': event.get('event_ticker'),
                'matched_market_ticker': market.get('ticker'),
                'matched_market_title': market.get('title') or market.get('yes_sub_title'),
                'yes_no_direction': match.get('side'),
            })
        assignments = ','.join(f'{name}=?' for name in values)
        with self.connect() as db:
            db.execute(f'UPDATE alerts SET {assignments} WHERE id=? AND status=\'processing\'',
                       (*values.values(), alert_id))

    def finish(self, alert_id, status, reason, details=None, retry_delay=30):
        with self.connect() as db:
            terminal = status not in ('pending', 'processing', 'retryable')
            now = time.time()
            updated = db.execute('''UPDATE alerts SET status=?, reason=?, details=?, updated_at=?,
                next_attempt=?,processed_at=CASE WHEN ? THEN ? ELSE processed_at END
                WHERE id=? AND status NOT IN ('traded','shadow_only') ''',
                (status, reason, json.dumps(details or {}, sort_keys=True), now,
                 now + retry_delay, terminal, now, alert_id))
            if terminal and updated.rowcount:
                row = db.execute('SELECT telegram_chat_id,telegram_message_id,received_at FROM alerts WHERE id=?',
                                 (alert_id,)).fetchone()
                self._advance_offset(db, row['telegram_chat_id'], row['telegram_message_id'],
                                     row['received_at'], processed=True)

    def telegram_offset(self, chat_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM telegram_offsets WHERE chat_id=?', (chat_id,)).fetchone()
            return dict(row) if row else {'chat_id': chat_id,
                                          'last_persisted_message_id': 0,
                                          'last_processed_message_id': 0,
                                          'last_received_at': None}

    def balances(self, db):
        initial = db.execute("SELECT value FROM settings WHERE key='starting_cash'").fetchone()[0]
        totals = db.execute('''SELECT COALESCE(SUM(cost),0),
            COALESCE(SUM(CASE
                WHEN portfolio_status='CLOSED' THEN portfolio_exit_proceeds
                WHEN portfolio_status='SETTLED' THEN payout ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN portfolio_status='OPEN' THEN stake ELSE 0 END),0)
            FROM trades WHERE trade_mode='PAPER' ''').fetchone()
        return initial - totals[0] + totals[1], totals[2]

    def exposure_allows(self, stake, max_exposure):
        with self.connect() as db:
            exposure = db.execute('''SELECT COALESCE(SUM(stake),0) FROM trades
                WHERE portfolio_status='OPEN' AND trade_mode='PAPER' ''').fetchone()[0]
            return exposure + stake <= max_exposure

    @staticmethod
    def _performance_summary(rows):
        wins = sum(row['final_result'] == 'WIN' for row in rows)
        losses = sum(row['final_result'] == 'LOSS' for row in rows)
        decided = wins + losses
        stake = sum(row['stake'] or 0 for row in rows)
        pnl = sum(row['net_pnl'] or 0 for row in rows)
        return {
            'settled': len(rows), 'decided': decided, 'wins': wins, 'losses': losses,
            'win_rate': wins / decided * 100 if decided else None,
            'roi': pnl / stake * 100 if stake else None,
        }

    def whale_performance_profile(self, trader, market_type=None, series_ticker=None):
        """Return settled signal evidence without mixing in portfolio exits."""
        if not trader:
            return {'overall': self._performance_summary([]),
                    'recent': self._performance_summary([]),
                    'context': self._performance_summary([])}
        with self.connect() as db:
            rows = [dict(row) for row in db.execute('''SELECT t.final_result,t.net_pnl,
                t.stake,t.market_type,t.series_ticker,t.closed_at
                FROM trades t JOIN alerts a ON a.id=t.alert_id
                WHERE a.trader=? AND t.final_result IS NOT NULL
                ORDER BY t.closed_at DESC,t.id DESC''', (trader,))]
        context = [row for row in rows if
                   (not market_type or row['market_type'] == market_type) and
                   (not series_ticker or row['series_ticker'] == series_ticker)]
        return {
            'overall': self._performance_summary(rows),
            'recent': self._performance_summary(rows[:20]),
            'context': self._performance_summary(context),
        }

    @staticmethod
    def _winner_conflict_row(db, event_ticker, ticker, contract_key, side):
        """Return one clear opposing PAPER outcome for a canonical event.

        Different winner contracts conflict only when both positions buy YES.
        Opposite directions on the same winner contract always conflict. Other
        combinations are left to existing duplicate rules rather than guessed.
        """
        if not event_ticker or not contract_key or side not in ('yes', 'no'):
            return None
        key = str(contract_key).strip().casefold()
        rows = db.execute('''SELECT t.id,t.ticker,t.side,t.winner_contract_key,
            t.alert_id,t.quantity,t.cost,t.stake,t.fee,t.series_ticker,
            a.trader,a.whale_win_rate,a.whale_roi
            FROM trades t JOIN alerts a ON a.id=t.alert_id
            WHERE t.event_ticker=? AND t.market_type='game_winner'
            AND t.portfolio_status='OPEN' AND t.trade_mode='PAPER' ORDER BY t.id''',
                          (event_ticker,)).fetchall()
        for row in rows:
            existing_key = str(row['winner_contract_key'] or '').strip().casefold()
            same_contract = row['ticker'] == ticker or (existing_key and existing_key == key)
            conflicts = ((same_contract and row['side'] != side) or
                         (not same_contract and row['side'] == side == 'yes'))
            if conflicts:
                return {'trade_id': row['id'], 'alert_id': row['alert_id'],
                        'ticker': row['ticker'], 'direction': row['side'],
                        'contract': row['winner_contract_key'] or row['ticker'],
                        'quantity': row['quantity'], 'cost': row['cost'],
                        'stake': row['stake'], 'fee': row['fee'],
                        'series_ticker': row['series_ticker'],
                        'trader': row['trader'],
                        'whale_win_rate': row['whale_win_rate'],
                        'whale_roi': row['whale_roi']}
        return None

    def open_winner_conflict(self, event_ticker, ticker, contract_key, side):
        with self.connect() as db:
            return self._winner_conflict_row(
                db, event_ticker, ticker, contract_key, side)

    def enter(self, alert_id, ticker, side, quantity, price, fee, max_exposure,
              details, max_positions=80, stake=None, session_id='legacy',
              strategy_version='legacy', code_version='legacy', trade_mode='PAPER',
              shadow_reason='', whale_win_rate=None, whale_roi=None,
              hours_to_close=None, resolution_time_source=None,
              effective_resolution_time=None, can_close_early=None,
              backend_run_id='legacy', strategy_mode='legacy',
              event_ticker=None, market_type=None, winner_contract_key=None,
              series_ticker=None, conflict_resolution=None):
        if trade_mode not in ('PAPER', 'SHADOW_ONLY'):
            raise ValueError('Invalid trade mode')
        stake = int(stake if stake is not None else
                    (Decimal(str(quantity)) * price).to_integral_value(rounding=ROUND_HALF_UP))
        cost = stake + fee
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM trades WHERE alert_id=?', (alert_id,)).fetchone():
                return 'duplicate'
            cash, exposure = self.balances(db)
            open_count = db.execute("""SELECT COUNT(*) FROM trades
                WHERE portfolio_status='OPEN' AND trade_mode='PAPER'""").fetchone()[0]
            conflict = None
            flip = False
            if trade_mode == 'PAPER' and market_type == 'game_winner':
                conflict = self._winner_conflict_row(
                    db, event_ticker, ticker, winner_contract_key, side)
                resolution = conflict_resolution or {}
                flip = bool(conflict and resolution.get('action') == 'flip' and
                            resolution.get('existing_trade_id') == conflict['trade_id'])
                if flip:
                    exit_proceeds = int(resolution.get('exit_proceeds') or 0)
                    if exit_proceeds <= 0:
                        flip = False
                    else:
                        cash += exit_proceeds
                        exposure -= conflict['stake']
                        open_count -= 1
            # A shadow observation is only created when this signal would have
            # passed the current paper-risk gates. A valid flip evaluates those
            # gates after the simulated exit frees cash and exposure.
            if cash < cost:
                return 'cash limit'
            if exposure + stake > max_exposure:
                return 'exposure closed'
            if open_count >= max_positions:
                return 'open position limit'
            did_flip = False
            if conflict:
                resolution = conflict_resolution or {}
                details['conflict'] = {
                    'event_ticker': event_ticker,
                    'existing_trade_id': conflict['trade_id'],
                    'existing_ticker': conflict['ticker'],
                    'existing_contract': conflict['contract'],
                    'existing_direction': conflict['direction'],
                    'existing_trader': conflict['trader'],
                    'new_ticker': ticker, 'new_contract': winner_contract_key,
                    'new_direction': side,
                    **{key: value for key, value in resolution.items()
                       if key not in {'exit_market'}},
                }
                if flip:
                    now = time.time()
                    exit_proceeds = int(resolution['exit_proceeds'])
                    exit_fee = int(resolution['exit_fee'])
                    exit_price = int(resolution['exit_price'])
                    realized = exit_proceeds - conflict['cost']
                    close_reason = resolution.get('reason') or 'Stronger opposite whale reversal'
                    updated = db.execute('''UPDATE trades SET portfolio_status='CLOSED',
                        portfolio_closed_at=?,portfolio_exit_price=?,portfolio_exit_fee=?,
                        portfolio_exit_proceeds=?,portfolio_realized_pnl=?,
                        portfolio_close_reason=?,portfolio_close_details=?
                        WHERE id=? AND portfolio_status='OPEN' AND trade_mode='PAPER' ''',
                        (now, exit_price, exit_fee, exit_proceeds, realized, close_reason,
                         json.dumps(details['conflict'], sort_keys=True), conflict['trade_id']))
                    if updated.rowcount:
                        did_flip = True
                    else:
                        flip = False
                if not flip:
                    trade_mode = 'SHADOW_ONLY'
                    shadow_reason = (resolution.get('reason') or
                                     'Opposite side already open; new whale not clearly stronger')
                    details['conflict']['reason'] = shadow_reason
            if (trade_mode == 'PAPER' and
                    db.execute("""SELECT 1 FROM trades WHERE ticker=?
                        AND portfolio_status='OPEN' AND trade_mode='PAPER'""",
                               (ticker,)).fetchone()):
                return 'contract already held'
            alert_source = db.execute(
                'SELECT recovery_of_alert_id FROM alerts WHERE id=?', (alert_id,)).fetchone()
            recovery_original = alert_source['recovery_of_alert_id'] if alert_source else None
            db.execute('''INSERT INTO trades
                (alert_id,ticker,side,quantity,price,stake,fee,cost,opened_at,
                 session_id,strategy_version,code_version,trade_mode,shadow_reason,
                 whale_win_rate,whale_roi,hours_to_close,resolution_time_source,
                 effective_resolution_time,can_close_early,backend_run_id,strategy_mode,
                 is_recovered,recovery_original_alert_id,event_ticker,market_type,
                 winner_contract_key,series_ticker,portfolio_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (alert_id,ticker,side,float(quantity),price,stake,fee,cost,time.time(),
                 session_id,strategy_version,code_version,trade_mode,shadow_reason,
                 whale_win_rate,whale_roi,hours_to_close,resolution_time_source,
                 effective_resolution_time,
                 None if can_close_early is None else int(bool(can_close_early)),
                 backend_run_id, strategy_mode, int(recovery_original is not None),
                 recovery_original, event_ticker, market_type, winner_contract_key,
                 series_ticker, 'OPEN' if trade_mode == 'PAPER' else 'SHADOW'))
            alert_status = 'traded' if trade_mode == 'PAPER' else 'shadow_only'
            reason = ('paper flip' if did_flip else 'paper fill') if trade_mode == 'PAPER' else shadow_reason
            now = time.time()
            db.execute('''UPDATE alerts SET status=?, reason=?, details=?, updated_at=?,
                processed_at=? WHERE id=?''',
                       (alert_status, reason, json.dumps(details, sort_keys=True), now, now, alert_id))
            alert_row = db.execute('''SELECT telegram_chat_id,telegram_message_id,received_at
                FROM alerts WHERE id=?''', (alert_id,)).fetchone()
            self._advance_offset(db, alert_row['telegram_chat_id'], alert_row['telegram_message_id'],
                                 alert_row['received_at'], processed=True)
            return alert_status

    def open_trades(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM trades
                WHERE portfolio_status='OPEN' AND trade_mode='PAPER'""")]

    def unresolved_trades(self):
        """Signals awaiting exchange settlement, including flipped and shadow rows."""
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM trades WHERE payout IS NULL')]

    def settle(self, trade_id, result, yes_settlement_value=None):
        """Settle once from an exchange result and optional YES value.

        Values use the ledger's 1/10,000 dollar units. Binary yes/no results
        imply $1/$0. Other results require an explicit exchange settlement value.
        """
        market_result = (result or '').strip().lower()
        if market_result == 'yes':
            yes_value = 10000
        elif market_result == 'no':
            yes_value = 0
        else:
            if yes_settlement_value is None or not 0 <= yes_settlement_value <= 10000:
                return False
            yes_value = int(yes_settlement_value)
            market_result = market_result or 'scalar'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            trade = db.execute('SELECT * FROM trades WHERE id=? AND payout IS NULL',
                               (trade_id,)).fetchone()
            if trade is None:
                return False
            position_value = yes_value if trade['side'] == 'yes' else 10000 - yes_value
            payout = int((Decimal(str(trade['quantity'])) * position_value).to_integral_value(
                rounding=ROUND_HALF_UP))
            stake = trade['stake']
            gross_pnl = payout - stake
            net_pnl = payout - trade['cost']
            if market_result in ('void', 'cancelled', 'canceled'):
                final_result = 'VOID'
            elif gross_pnl > 0:
                final_result = 'WIN'
            elif gross_pnl < 0:
                final_result = 'LOSS'
            else:
                final_result = 'PUSH'
            closed_at = time.time()
            updated = db.execute('''UPDATE trades SET result=?, final_result=?, closed_at=?,
                settlement_value=?, payout=?, gross_pnl=?, net_pnl=?, roi=?,
                portfolio_status=CASE WHEN portfolio_status='OPEN' THEN 'SETTLED'
                                      ELSE portfolio_status END,
                portfolio_closed_at=CASE WHEN portfolio_status='OPEN' THEN ?
                                         ELSE portfolio_closed_at END
                WHERE id=? AND payout IS NULL''',
                (market_result, final_result, closed_at, position_value, payout,
                 gross_pnl, net_pnl, net_pnl / stake, closed_at, trade_id))
            return updated.rowcount == 1

    def trade_history(self):
        """Return every paper fill with its persisted alert context."""
        with self.connect() as db:
            return [dict(row) for row in db.execute('''SELECT
                t.*, a.source_key, a.raw_text, a.received_at, a.details,
                original.source_key AS original_source_key
                FROM trades t JOIN alerts a ON a.id=t.alert_id
                LEFT JOIN alerts original ON original.id=t.recovery_original_alert_id
                ORDER BY t.id''')]

    def portfolio_stats(self):
        """Return bankroll and premium-exposure values in ledger units."""
        with self.connect() as db:
            starting = db.execute(
                "SELECT value FROM settings WHERE key='starting_cash'").fetchone()[0]
            realized = db.execute('''SELECT COALESCE(SUM(CASE
                WHEN portfolio_status='CLOSED' THEN portfolio_realized_pnl
                WHEN portfolio_status='SETTLED' THEN net_pnl ELSE 0 END),0)
                FROM trades WHERE trade_mode='PAPER' ''').fetchone()[0]
            trades = [dict(row) for row in db.execute(
                """SELECT stake,opened_at,portfolio_closed_at,portfolio_status
                FROM trades WHERE trade_mode='PAPER'""")]
        current = sum(row['stake'] for row in trades if row['portfolio_status'] == 'OPEN')
        events = []
        for row in trades:
            events.append((row['opened_at'], 1, row['stake']))
            if row['portfolio_closed_at'] is not None:
                events.append((row['portfolio_closed_at'], 0, -row['stake']))
        exposure = peak = 0
        for _, _, change in sorted(events):
            exposure += change
            peak = max(peak, exposure)
        return {'starting_bankroll': starting,
                'realized_bankroll': starting + realized,
                'open_exposure': current, 'peak_open_exposure': peak}

    def alert_result(self, alert_id):
        """Read one persisted decision and its actual fill for console display."""
        with self.connect() as db:
            row = db.execute('SELECT * FROM alerts WHERE id=?', (alert_id,)).fetchone()
            trade = db.execute('SELECT * FROM trades WHERE alert_id=?', (alert_id,)).fetchone()
            return dict(row), dict(trade) if trade else None

    def report(self):
        with self.connect() as db:
            cash, exposure = self.balances(db)
            pnl = db.execute("""SELECT COALESCE(SUM(CASE
                WHEN portfolio_status='CLOSED' THEN portfolio_realized_pnl
                WHEN portfolio_status='SETTLED' THEN net_pnl ELSE 0 END),0)
                FROM trades WHERE trade_mode='PAPER'""").fetchone()[0]
            statuses = dict(db.execute('SELECT status,COUNT(*) FROM alerts GROUP BY status').fetchall())
            trades = [dict(row) for row in db.execute('SELECT * FROM trades ORDER BY id')]
            recent = [dict(row) for row in db.execute('''SELECT id, source_key, status, reason, attempts
                FROM alerts ORDER BY id DESC LIMIT 20''')]
            return {'cash_dollars': cash / 10000, 'open_cost_dollars': exposure / 10000,
                    'realized_pnl_dollars': pnl / 10000, 'alerts': statuses,
                    'recent_alerts': recent, 'trades': trades}
