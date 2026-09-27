"""CLI entry point. Importing any module never connects to Telegram."""
import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import time

from config import ROOT, Settings
from console_output import print_summary
from database import Database
from kalshi import KalshiClient
from simulator import PaperTrader
from versioning import git_code_version, new_backend_run_id


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kalshi paper tracker (no real orders)")
    parser.add_argument("command", choices=("listen", "replay", "report", "settle"), nargs="?", default="listen")
    parser.add_argument("--file", type=Path, help="One UTF-8 five-line alert for replay")
    parser.add_argument("--db", type=Path, default=Settings().database)
    args = parser.parse_args(argv)
    if args.command == "replay" and args.file is None:
        parser.error("replay requires --file")
    handlers = None
    if args.command == "listen":
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        handlers = [logging.FileHandler(ROOT / 'listener.log', encoding='utf-8'), console]
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings(database=args.db)
    database = Database(settings.database, settings.starting_cash)
    client = KalshiClient(settings)
    backend_run_id = new_backend_run_id()
    session_id = database.get_or_create_session_id(
        backend_run_id, configured=settings.experiment_session_id)
    code_version = git_code_version()
    trader = PaperTrader(client, database, settings,
                         on_result=print_summary if args.command == 'listen' else None,
                         on_exposure_change=(lambda message: print(message, flush=True))
                         if args.command == 'listen' else None,
                         session_id=session_id,
                         strategy_version=settings.strategy_version,
                         code_version=code_version,
                         backend_run_id=backend_run_id)
    try:
        if args.command == "listen":
            from telegram_listener import listen
            asyncio.run(listen(trader))
        elif args.command == "replay":
            text = args.file.read_text(encoding="utf-8-sig")
            # Replay uses current markets. A stable hash prevents duplicate replay fills.
            database.ingest("replay:" + hashlib.sha256(text.encode()).hexdigest(), text, time.time(),
                            session_id, settings.strategy_version, code_version, backend_run_id,
                            strategy_mode=settings.strategy_mode)
            while trader.process_next():
                pass
            print(json.dumps(database.report(), indent=2))
        elif args.command == "settle":
            print(f"Settled {trader.settle()} paper positions")
        else:
            print(json.dumps(database.report(), indent=2))
    except KeyboardInterrupt:
        logging.info("Paper tracker stopped")
    finally:
        client.close()


if __name__ == "__main__":
    main()
