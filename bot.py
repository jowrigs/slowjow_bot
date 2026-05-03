import os
import logging
import feedparser
from datetime import datetime
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

# ── Config (set via environment variables / GitHub Secrets) ──────────────────
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]   # from BotFather
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]     # your chat / channel ID
TIMEZONE  = os.getenv("TIMEZONE", "Asia/Manila")

MAX_ARTICLES = 12  # max stories per digest

# ── RSS Feed Sources ──────────────────────────────────────────────────────────
FEEDS = [
    # Crypto News
    {"name": "CoinDesk",         "icon": "📰", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt",          "icon": "📰", "url": "https://decrypt.co/feed"},
    {"name": "Cointelegraph",    "icon": "📰", "url": "https://cointelegraph.com/rss"},
    {"name": "The Block",        "icon": "📰", "url": "https://www.theblock.co/rss.xml"},
    {"name": "BeInCrypto",       "icon": "📰", "url": "https://beincrypto.com/feed/"},
    # YouTube Channels
    {"name": "Coin Bureau",      "icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCqK_GSMbpiV8spgD3ZGloSw"},
    {"name": "DataDash",         "icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCCatR7nWbYrkVXdxXb4cGXw"},
    {"name": "Andrei Jikh",      "icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCF9IOB2TExg3QIBupFtBDxg"},
    {"name": "Two Minute Papers","icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg"},
    # Reddit
    {"name": "r/CryptoCurrency", "icon": "📱", "url": "https://www.reddit.com/r/CryptoCurrency/top/.rss?t=day"},
    {"name": "r/artificial",     "icon": "📱", "url": "https://www.reddit.com/r/artificial/top/.rss?t=day"},
    {"name": "r/singularity",    "icon": "📱", "url": "https://www.reddit.com/r/singularity/top/.rss?t=day"},
    {"name": "r/AIInFinance",    "icon": "📱", "url": "https://www.reddit.com/r/AIInFinance/top/.rss?t=day"},
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

def fetch_feed(feed_meta: dict) -> list:
    """Fetch a single RSS feed, handling Reddit's user-agent requirement."""
    url = feed_meta["url"]
    if "reddit.com" in url:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "AICryptoBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            import feedparser
            return feedparser.parse(resp.read()).entries
    return feedparser.parse(url).entries

def fetch_articles() -> list[dict]:
    """Fetch and filter AI-related crypto articles from all RSS feeds."""
    articles = []
    for feed_meta in FEEDS:
        try:
            entries = fetch_feed(feed_meta)
            for entry in entries[:20]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", "")
                if is_ai_related(title, summary):
                    articles.append({
                        "source":  feed_meta["name"],
                        "icon":    feed_meta["icon"],
                        "title":   title.strip(),
                        "link":    link.strip(),
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

    # Group by icon type for a structured digest
    news    = [a for a in articles if a["icon"] == "📰"]
    videos  = [a for a in articles if a["icon"] == "🎥"]
    reddit  = [a for a in articles if a["icon"] == "📱"]

    lines = [f"🤖 <b>AI × Crypto Daily — {today}</b>\n"]

    if news:
        lines.append("📰 <b>NEWS</b>")
        for a in news:
            lines.append(f"• <a href='{a['link']}'>{a['title']}</a> — <i>{a['source']}</i>")

    if videos:
        lines.append("\n🎥 <b>YOUTUBE</b>")
        for a in videos:
            lines.append(f"• <a href='{a['link']}'>{a['title']}</a> — <i>{a['source']}</i>")

    if reddit:
        lines.append("\n📱 <b>REDDIT</b>")
        for a in reddit:
            lines.append(f"• <a href='{a['link']}'>{a['title']}</a> — <i>{a['source']}</i>")

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

# ── Entry point (GitHub Actions runs this once and exits) ─────────────────────
if __name__ == "__main__":
    log.info("Starting one-shot digest run...")
    asyncio.run(send_digest())
