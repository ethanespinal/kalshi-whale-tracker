"""Refresh paper settlements, print research results, and export the ledger."""
import argparse
import logging
from pathlib import Path

from config import ROOT, Settings
from database import Database
from kalshi import KalshiClient
from reporting import export_csv, format_report, report_rows
from simulator import PaperTrader


def main(argv=None):
    parser = argparse.ArgumentParser(description='Paper-trading research record')
    parser.add_argument('--db', type=Path, default=Settings().database)
    parser.add_argument('--csv', type=Path, default=ROOT / 'paper_trading_results.csv')
    parser.add_argument('--no-settle', action='store_true',
                        help='Build the record without checking open Kalshi tickers')
    versions = parser.add_mutually_exclusive_group()
    versions.add_argument('--strategy-version',
                          help='Report one strategy version (default: current configured version)')
    versions.add_argument('--all-versions', action='store_true',
                          help='Include every strategy version in the printed report')
    args = parser.parse_args(argv)
    settings = Settings(database=args.db)
    database = Database(args.db, settings.starting_cash)

    if not args.no_settle:
        client = KalshiClient(settings)
        try:
            PaperTrader(client, database, settings).settle()
        finally:
            client.close()

    all_rows = report_rows(database.trade_history())
    selected_version = None if args.all_versions else (args.strategy_version or settings.strategy_version)
    rows = all_rows if selected_version is None else [
        row for row in all_rows if row['strategy_version'] == selected_version]
    scope = 'all versions' if selected_version is None else selected_version
    print(f'Strategy scope: {scope}')
    print(f'Configured strategy mode: {settings.strategy_mode}')
    print(f'Max allowed slippage: ${settings.max_allowed_slippage:.2f}')
    print(format_report(rows, database.portfolio_stats()))
    recovery = database.recovery_summary()
    print('\nHISTORICAL ALERT RECOVERY')
    print(f"Reconsidered: {recovery['historical_alerts_reconsidered']}")
    print(f"Newly matched: {recovery['newly_matched']}")
    print(f"Recovered paper trades: {recovery['recovered_paper_trades']}")
    print(f"Price worsened: {recovery['price_worsened']}")
    print(f"Opportunity gone: {recovery['opportunity_gone']}")
    print(f"Still unmatched: {recovery['still_unmatched']}")
    output = export_csv(all_rows, args.csv.resolve())
    print(f'\nCSV saved to: {output}')


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    main()
