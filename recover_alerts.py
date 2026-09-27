"""Explicit, bounded recovery of historical matcher failures into paper trading."""
import argparse
import logging
from pathlib import Path

from config import Settings
from database import Database
from kalshi import KalshiClient
from simulator import PaperTrader
from versioning import git_code_version, new_backend_run_id


def recover(database, trader, settings, session_id, code_version, limit=100):
    candidates = database.recovery_candidates(None if limit == 0 else limit)
    processed = 0
    for original in candidates:
        prepared = database.prepare_recovery(
            original['id'], session_id, settings.strategy_version,
            settings.strategy_mode, code_version, trader.backend_run_id)
        if not prepared or not prepared['process']:
            continue
        trader.process_alert_id(prepared['alert_id'])
        processed += 1
    return processed, database.recovery_summary()


def print_summary(summary):
    print('\nHISTORICAL ALERT RECOVERY')
    print(f"Historical alerts reconsidered: {summary['historical_alerts_reconsidered']}")
    print(f"Newly matched: {summary['newly_matched']}")
    print(f"Recovered paper trades: {summary['recovered_paper_trades']}")
    print(f"Rejected — price now worse: {summary['price_worsened']}")
    print(f"Rejected — opportunity gone: {summary['opportunity_gone']}")
    print(f"Still unmatched: {summary['still_unmatched']}")
    print(f"Other current-rule rejections: {summary['other_rejected']}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Re-evaluate historical matcher failures with current paper-only rules')
    parser.add_argument('--db', type=Path, default=Settings().database)
    parser.add_argument('--limit', type=int, default=100,
                        help='Maximum historical alerts per run; 0 means all (default: 100)')
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error('--limit must be nonnegative')

    settings = Settings(database=args.db)
    database = Database(args.db, settings.starting_cash)
    code_version = git_code_version()
    backend_run_id = 'recovery-' + new_backend_run_id()
    session_id = database.get_or_create_session_id(
        backend_run_id, configured=settings.experiment_session_id)
    client = KalshiClient(settings)
    trader = PaperTrader(
        client, database, settings, session_id=session_id,
        strategy_version=settings.strategy_version, code_version=code_version,
        backend_run_id=backend_run_id)
    try:
        processed, summary = recover(
            database, trader, settings, session_id, code_version, args.limit)
        print(f'Processed in this run: {processed}')
        print_summary(summary)
    finally:
        client.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    main()
