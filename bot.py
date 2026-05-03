import os
import logging
import feedparser
import requests
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot
from telegram.constants import ParseMode
import asyncio
import pytz

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)

# ── Config (set via environment variables) ───────────────────────────────────
BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]   # from BotFather
CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]     # your chat / channel ID
SEND_HOUR   = int(os.getenv("SEND_HOUR", "9"))   # 24-hr UTC hour (default 9 AM)
TIMEZONE    = os.getenv("TIMEZONE", "UTC")

MAX_ARTICLES = 8   # max stories per digest

# ── RSS Feed Sources ──────────────────────────────────────────────────────────
FEEDS = [
    {"name": "CoinDesk",       "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt",        "url": "https://decrypt.co/feed"},
    {"name": "Cointelegraph",  "url": "https://cointelegraph.com/rss"},
    {"name": "The Block",      "url": "https://www.theblock.co/rss.xml"},
    {"name": "BeInCrypto",     "url": "https://beincrypto.com/feed/"},
]

# Keywords that qualify an article as AI-related
AI_KEYWORDS = [
    "artificial intelligence", " ai ", "machine learning", "deep learning",
    "large language model", "llm", "chatgpt", "gpt", "neural network",
    "generative ai", "ai agent", "ai token", "ai coin", "ai crypto",
    "predictive model", "algorithmic trading", "sentiment analysis",
    "ai-powered", "openai", "anthropic", "deepmind", "nvidia",
]

def is_ai_related(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in AI_KEYWORDS)

def fetch_articles() -> list[dict]:
    """Fetch and filter AI-related crypto articles from all RSS feeds."""
    articles = []
    for feed_meta in FEEDS:
        try:
            feed = feedparser.parse(feed_meta["url"])
            for entry in feed.entries[:20]:  # check top 20 per source
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", "")
                if is_ai_related(title, summary):
                    articles.append({
                        "source":  feed_meta["name"],
                        "title":   title.strip(),
                        "link":    link.strip(),
                        "summary": summary[:200].strip() if summary else "",
                    })
        except Exception as e:
            log.warning(f"Failed to fetch {feed_meta['name']}: {e}")

    # Deduplicate by link
    seen = set()
    unique = []
    for a in articles:
        if a["link"] not in seen:
            seen.add(a["link"])
            unique.append(a)

    return unique[:MAX_ARTICLES]

def build_message(articles: list[dict]) -> str:
    """Format articles into a Telegram-friendly HTML digest."""
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%B %d, %Y")

    if not articles:
        return (
            f"🤖📰 <b>AI × Crypto Daily — {today}</b>\n\n"
            "No AI-related crypto articles found today. Check back tomorrow!"
        )

    lines = [f"🤖📰 <b>AI × Crypto Daily — {today}</b>\n"]
    for i, a in enumerate(articles, 1):
        lines.append(
            f"{i}. <b><a href='{a['link']}'>{a['title']}</a></b>\n"
            f"   🔹 <i>{a['source']}</i>"
        )
    lines.append("\n─────────────────────")
    lines.append("📡 Powered by your AI Crypto Bot")
    return "\n".join(lines)

async def send_digest():
    """Fetch articles and send the digest to Telegram."""
    log.info("Fetching AI × Crypto articles...")
    articles = fetch_articles()
    log.info(f"Found {len(articles)} matching articles.")
    message  = build_message(articles)

    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    log.info("Digest sent successfully ✅")

def run_digest():
    """Sync wrapper for the async send_digest (called by APScheduler)."""
    asyncio.run(send_digest())

# ── Scheduler ─────────────────────────────────────────────────────────────────
def main():
    log.info(f"Bot starting — digest scheduled daily at {SEND_HOUR:02d}:00 {TIMEZONE}")

    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_digest,
        trigger="cron",
        hour=SEND_HOUR,
        minute=0,
        id="daily_digest",
    )
    scheduler.start()

    # Send an immediate digest on startup so you can verify it works
    log.info("Sending startup digest...")
    run_digest()

    # Keep the process alive
    try:
        while True:
            import time; time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("Bot stopped.")

if __name__ == "__main__":
    main()
