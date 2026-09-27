"""The existing Telethon subscription, with a durable inbox and one worker."""
import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from telethon import TelegramClient, events

from config import ROOT

WHALE_CHAT_ID = 8624141013
log = logging.getLogger(__name__)


async def persist_telegram_message(trader, message):
    """Commit a Telegram message to the inbox before any parsing or lookup."""
    chat_id = int(message.chat_id)
    message_id = int(message.id)
    inserted = await asyncio.to_thread(
        trader.db.ingest, f"{chat_id}:{message_id}",
        message.raw_text or '', message.date.timestamp(), trader.session_id,
        trader.strategy_version, trader.code_version, trader.backend_run_id,
        chat_id, message_id, trader.settings.strategy_mode)
    try:
        await asyncio.to_thread(
            trader.db.update_backend_status, last_telegram_alert=time.time())
    except Exception:
        # The inbox commit above is the source of truth. A transient status-panel
        # write must not misreport or retry that durable Telegram insert.
        log.exception('Saved Telegram alert %s but could not update heartbeat status',
                      message_id)
    if inserted:
        log.info("Saved Telegram alert %s", message_id)
    return inserted


async def backfill_telegram_messages(client, trader, chat_id):
    """Persist channel messages newer than the durable inbox high-water mark."""
    offset = await asyncio.to_thread(trader.db.telegram_offset, chat_id)
    saved = 0
    async for message in client.iter_messages(
            chat_id, min_id=offset['last_persisted_message_id'], reverse=True):
        saved += bool(await persist_telegram_message(trader, message))
    return saved


async def listen(trader):
    load_dotenv(ROOT / ".env")
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_id.isdigit() or not api_hash:
        raise ValueError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
    # Preserve the existing login session and channel filter.
    client = TelegramClient(str(ROOT / "whale_tracker_session"), int(api_id), api_hash)
    stopping = asyncio.Event()

    @client.on(events.NewMessage(chats=WHALE_CHAT_ID))
    async def new_message(event):
        try:
            await persist_telegram_message(trader, event)
        except Exception:
            log.exception("Could not persist Telegram alert %s", event.id)

    async def worker():
        # Startup recovery already refreshed every persisted open position.
        next_settlement = time.monotonic() + 300
        while not stopping.is_set():
            try:
                if time.monotonic() >= next_settlement:
                    await asyncio.to_thread(trader.settle)
                    next_settlement = time.monotonic() + 300
                if await asyncio.to_thread(trader.process_next):
                    continue
            except Exception:
                log.exception("Paper worker failed; will retry")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=1)
            except TimeoutError:
                pass

    async def heartbeat_worker():
        while not stopping.is_set():
            try:
                await asyncio.to_thread(trader.heartbeat)
            except Exception:
                log.exception('Could not update backend heartbeat')
            try:
                await asyncio.wait_for(
                    stopping.wait(), timeout=trader.settings.heartbeat_interval)
            except TimeoutError:
                pass

    await client.start()
    recovery = await asyncio.to_thread(trader.start_backend, WHALE_CHAT_ID)
    log.info('Recovered %s open trades', recovery['open_trades'])
    log.info('Last processed Telegram alert: %s', recovery['last_processed_message_id'])
    log.info('Settlement refresh complete; %s positions settled', recovery['settled'])
    print(f"Recovered {recovery['open_trades']} open trades", flush=True)
    print(f"Last processed Telegram alert: {recovery['last_processed_message_id']}", flush=True)
    print("Settlement refresh complete", flush=True)
    backfilled = await backfill_telegram_messages(client, trader, WHALE_CHAT_ID)
    if backfilled:
        log.info('Recovered %s Telegram alerts missed while offline', backfilled)
    task = asyncio.create_task(worker())
    heartbeat_task = asyncio.create_task(heartbeat_worker())
    log.info("Listening for whale alerts; PAPER ONLY")
    print("Listening for whale alerts | PAPER ONLY | Diagnostic logs: listener.log", flush=True)
    try:
        await client.run_until_disconnected()
    finally:
        stopping.set()
        await task
        await heartbeat_task
        await asyncio.to_thread(trader.stop_backend)
        await client.disconnect()


if __name__ == "__main__":
    from main import main
    main(["listen"])
