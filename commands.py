import os
import json
import logging
import asyncio
from datetime import datetime
import pytz
from telegram import Bot
from telegram.constants import ParseMode

import bot as core  # reuse fetch_watchlist/fetch_gainers_losers/format_ticker/load_history/retry helpers

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

BOT_TOKEN   = core.BOT_TOKEN
TIMEZONE    = core.TIMEZONE
OFFSET_FILE = "telegram_offset.json"
MAX_LATEST  = 12

HELP_TEXT = (
    "🤖 <b>Crypto × AI Bot — Commands</b>\n\n"
    "/latest — today's digest so far\n"
    "/prices — live BTC, ETH, XRP, TRX, POL + top movers\n"
    "/help — show this message"
)

def load_offset() -> int:
    if not os.path.exists(OFFSET_FILE):
        return 0
    try:
        with open(OFFSET_FILE, "r") as f:
            return json.load(f).get("offset", 0)
    except Exception as e:
        log.warning(f"Failed to read offset, starting from 0: {e}")
        return 0

def save_offset(offset: int):
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except Exception as e:
        log.warning(f"Failed to save offset: {e}")

def build_latest_reply() -> str:
    history = core.load_history()
    titled = [e for e in history if e.get("title")]
    if not titled:
        return "No digest with article details has been sent yet — check back after the next scheduled run."

    today = datetime.now(pytz.timezone(TIMEZONE)).date().isoformat()
    todays = [e for e in titled if e.get("date") == today]
    if todays:
        label, entries = f"Today's digest ({today})", todays
    else:
        latest_date = max(e["date"] for e in titled)
        label, entries = f"Most recent digest ({latest_date})", [e for e in titled if e["date"] == latest_date]

    entries = sorted(entries, key=lambda e: e.get("score", 0), reverse=True)[:MAX_LATEST]
    lines = [f"🤖🪙 <b>{label}</b>\n"]
    for e in entries:
        lines.append(f"• <a href='{e['link']}'>{e['title']}</a> — <i>{e.get('source', '')}</i>")
    return "\n".join(lines)

async def build_prices_reply() -> str:
    try:
        watchlist = core.retry_call(core.fetch_watchlist, retries=3, label="watchlist")
    except Exception as e:
        log.warning(f"Watchlist fetch failed: {e}")
        watchlist = []
    try:
        gainers, losers = core.retry_call(core.fetch_gainers_losers, retries=3, label="gainers/losers")
    except Exception as e:
        log.warning(f"Gainers/losers fetch failed: {e}")
        gainers, losers = [], []

    ticker = core.format_ticker(watchlist, gainers, losers)
    if not ticker:
        return "Prices are temporarily unavailable — try again in a bit."
    return "💹 <b>Live Prices</b>\n\n" + ticker

async def handle_message(bot: Bot, message):
    """message is a telegram.Message object. We only ever reply in the same
    chat the command came from (the sender's DM) — never to the broadcast
    channel — and silently ignore anything that isn't a known command, so
    the bot doesn't engage with unrelated chatter."""
    text = (message.text or "").strip().lower()
    if not text.startswith("/"):
        return

    command = text.split()[0].split("@")[0]  # strip "@botname" suffix if present

    if command in ("/start", "/help"):
        reply = HELP_TEXT
    elif command == "/latest":
        reply = build_latest_reply()
    elif command == "/prices":
        reply = await build_prices_reply()
    else:
        return  # unrecognized command — no reply, avoid spam/noise

    await core.retry_async_call(
        bot.send_message, retries=2, label="command_reply",
        chat_id=message.chat_id, text=reply, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )

async def main():
    offset = load_offset()
    bot    = Bot(token=BOT_TOKEN)

    try:
        updates = await core.retry_async_call(
            bot.get_updates, retries=2, label="get_updates", offset=offset, timeout=0,
        )
    except Exception as e:
        # A single failed poll isn't worth alerting on — this runs every 5
        # minutes, so transient hiccups are routine, not incidents. Just
        # try again next cycle.
        log.error(f"get_updates failed, will retry next cycle: {e}")
        updates = []

    highest_id = offset
    for update in updates:
        highest_id = max(highest_id, update.update_id + 1)
        if update.message:  # channel posts arrive separately and are intentionally not handled
            try:
                await handle_message(bot, update.message)
            except Exception as e:
                log.warning(f"Failed to handle one message, continuing: {e}")

    log.info(f"Processed {len(updates)} update(s)" if updates else "No new messages.")
    save_offset(highest_id)  # always persist, so the file exists from run 1 onward

if __name__ == "__main__":
    asyncio.run(main())
