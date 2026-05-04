import os
import json
import logging
import feedparser
import urllib.request
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode
import asyncio
import pytz

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID      = os.environ["TELEGRAM_CHAT_ID"]
TIMEZONE     = os.getenv("TIMEZONE", "Asia/Manila")
MAX_ARTICLES = 12
MARKET_SCAN  = 250  # scan top 250 coins for gainers/losers

# ── Fixed Watchlist (always shown at top) ─────────────────────────────────────
WATCHLIST = [
    {"symbol": "BTC", "id": "bitcoin"},
    {"symbol": "ETH", "id": "ethereum"},
    {"symbol": "XRP", "id": "ripple"},
    {"symbol": "TRX", "id": "tron"},
    {"symbol": "POL", "id": "matic-network"},
]

# ── RSS Feed Sources ──────────────────────────────────────────────────────────
FEEDS = [
    {"name": "CoinDesk",         "icon": "📰", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt",          "icon": "📰", "url": "https://decrypt.co/feed"},
    {"name": "Cointelegraph",    "icon": "📰", "url": "https://cointelegraph.com/rss"},
    {"name": "The Block",        "icon": "📰", "url": "https://www.theblock.co/rss.xml"},
    {"name": "BeInCrypto",       "icon": "📰", "url": "https://beincrypto.com/feed/"},
    {"name": "Coin Bureau",      "icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCqK_GSMbpiV8spgD3ZGloSw"},
    {"name": "DataDash",         "icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCCatR7nWbYrkVXdxXb4cGXw"},
    {"name": "Andrei Jikh",      "icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCF9IOB2TExg3QIBupFtBDxg"},
    {"name": "Two Minute Papers","icon": "🎥", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg"},
    {"name": "r/CryptoCurrency", "icon": "📱", "url": "https://www.reddit.com/r/CryptoCurrency/top/.rss?t=day"},
    {"name": "r/artificial",     "icon": "📱", "url": "https://www.reddit.com/r/artificial/top/.rss?t=day"},
    {"name": "r/singularity",    "icon": "📱", "url": "https://www.reddit.com/r/singularity/top/.rss?t=day"},
    {"name": "r/AIInFinance",    "icon": "📱", "url": "https://www.reddit.com/r/AIInFinance/top/.rss?t=day"},
]

AI_KEYWORDS = [
    "fetch.ai", "bittensor", "ocean protocol", "singularitynet", "render network",
    "numerai", "cortex", "matrix ai", "deepbrain chain", "alethea",
    "agix", "fet token", "rndr", "tao token", "near ai",
    "ai-powered defi", "ai trading bot", "ai smart contract", "ai crypto",
    "ai blockchain", "ai token", "ai coin", "ai protocol", "ai agent crypto",
    "ai in defi", "ai in web3", "ai web3", "ai nft", "ai dao",
    "machine learning crypto", "ml trading", "algorithmic crypto",
    "predictive crypto", "ai wallet", "ai exchange",
    "ai crypto fund", "ai crypto investment", "ai crypto startup",
    "ai crypto launch", "ai crypto raise", "ai crypto token launch",
    "crypto ai model", "on-chain ai", "decentralized ai",
    "ai layer", "ai network crypto", "neural network crypto",
    "llm blockchain", "gpt crypto", "ai miner", "ai mining",
]

# ── Price Functions ───────────────────────────────────────────────────────────
def p_fmt(price: float) -> str:
    return f"${price:,.2f}" if price >= 1 else f"${price:.4f}"

def c_fmt(change: float | None) -> tuple[str, str]:
    if change is None:
        return "⚪", "N/A"
    arrow = "🟢" if change >= 0 else "🔴"
    sign  = "+" if change >= 0 else ""
    return arrow, f"{sign}{change:.2f}%"

def fetch_watchlist() -> list[dict]:
    """Fetch live prices for BTC, ETH, XRP, TRX, POL."""
    ids = ",".join(t["id"] for t in WATCHLIST)
    url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AICryptoBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for t in WATCHLIST:
            info   = data.get(t["id"], {})
            price  = info.get("usd")
            change = info.get("usd_24h_change")
            if price is not None:
                arrow, c_str = c_fmt(change)
                results.append({
                    "symbol": t["symbol"],
                    "price":  p_fmt(price),
                    "change": c_str,
                    "arrow":  arrow,
                })
        return results
    except Exception as e:
        log.warning(f"Failed to fetch watchlist: {e}")
        return []

def fetch_gainers_losers() -> tuple[list[dict], list[dict]]:
    """Fetch top 5 gainers and top 5 losers from the top 250 coins by market cap."""
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc"
        f"&per_page={MARKET_SCAN}&page=1&price_change_percentage=24h"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AICryptoBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            coins = json.loads(resp.read())

        coins = [c for c in coins if c.get("price_change_percentage_24h") is not None]
        sorted_coins = sorted(coins, key=lambda c: c["price_change_percentage_24h"], reverse=True)

        def fmt(c: dict) -> dict:
            arrow, c_str = c_fmt(c["price_change_percentage_24h"])
            return {
                "symbol": c["symbol"].upper(),
                "price":  p_fmt(c["current_price"]),
                "change": c_str,
                "arrow":  arrow,
            }

        gainers = [fmt(c) for c in sorted_coins[:5]]
        losers  = [fmt(c) for c in sorted_coins[-5:][::-1]]
        return gainers, losers

    except Exception as e:
        log.warning(f"Failed to fetch gainers/losers: {e}")
        return [], []

def format_ticker(watchlist: list[dict], gainers: list[dict], losers: list[dict]) -> str:
    """Build the full price ticker block."""
    lines = []

    # Fixed watchlist always on top
    if watchlist:
        lines.append("💰 <b>PRICES</b>")
        for w in watchlist:
            lines.append(f"  {w['arrow']} <b>{w['symbol']}</b>  {w['price']}  <i>{w['change']}</i>")

    # Market movers below
    if gainers or losers:
        lines.append("")
        lines.append("📊 <b>MARKET MOVERS (24H)</b>")
        if gainers:
            lines.append("🟢 <b>Top Gainers</b>")
            for i, c in enumerate(gainers, 1):
                lines.append(f"  {i}. <b>{c['symbol']}</b>  {c['price']}  <i>{c['change']}</i>")
        if losers:
            lines.append("🔴 <b>Top Losers</b>")
            for i, c in enumerate(losers, 1):
                lines.append(f"  {i}. <b>{c['symbol']}</b>  {c['price']}  <i>{c['change']}</i>")

    return "\n".join(lines)

# ── Article Fetching ──────────────────────────────────────────────────────────
def is_ai_related(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in AI_KEYWORDS)

def extract_thumbnail(entry: dict) -> str | None:
    thumbnails = entry.get("media_thumbnail", [])
    if thumbnails:
        return thumbnails[0].get("url")
    for mc in entry.get("media_content", []):
        if "image" in mc.get("type", "") and mc.get("url"):
            return mc["url"]
    for enc in entry.get("enclosures", []):
        if "image" in enc.get("type", "") and enc.get("href"):
            return enc["href"]
    return None

def fetch_feed(feed_meta: dict) -> list:
    url = feed_meta["url"]
    if "reddit.com" in url:
        req = urllib.request.Request(url, headers={"User-Agent": "AICryptoBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return feedparser.parse(resp.read()).entries
    return feedparser.parse(url).entries

def fetch_articles() -> list[dict]:
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
                        "source":    feed_meta["name"],
                        "icon":      feed_meta["icon"],
                        "title":     title.strip(),
                        "link":      link.strip(),
                        "thumbnail": extract_thumbnail(entry),
                    })
        except Exception as e:
            log.warning(f"Failed to fetch {feed_meta['name']}: {e}")

    seen = set()
    unique = []
    for a in articles:
        if a["link"] not in seen:
            seen.add(a["link"])
            unique.append(a)
    return unique[:MAX_ARTICLES]

# ── Message Builder ───────────────────────────────────────────────────────────
def build_message(articles, watchlist, gainers, losers) -> str:
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%B %d, %Y")

    lines = [f"🤖🪙 <b>Crypto × AI Daily — {today}</b>"]
    lines.append("<i>Cryptocurrencies & platforms powered by AI</i>\n")

    ticker = format_ticker(watchlist, gainers, losers)
    if ticker:
        lines.append(ticker)

    lines.append("\n─────────────────────")

    if not articles:
        lines.append("\nNo updates on cryptocurrency using AI found today. Check back tomorrow!")
    else:
        news   = [a for a in articles if a["icon"] == "📰"]
        videos = [a for a in articles if a["icon"] == "🎥"]
        reddit = [a for a in articles if a["icon"] == "📱"]

        if news:
            lines.append("\n📰 <b>NEWS</b>")
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

def pick_cover_thumbnail(articles: list[dict]) -> str | None:
    for a in articles:
        if a["icon"] == "🎥" and a.get("thumbnail"):
            return a["thumbnail"]
    for a in articles:
        if a.get("thumbnail"):
            return a["thumbnail"]
    return None

# ── Send ──────────────────────────────────────────────────────────────────────
async def send_digest():
    log.info("Fetching data...")
    watchlist        = fetch_watchlist()
    gainers, losers  = fetch_gainers_losers()
    articles         = fetch_articles()
    log.info(f"Watchlist: {len(watchlist)} | Gainers: {len(gainers)} | Losers: {len(losers)} | Articles: {len(articles)}")

    message   = build_message(articles, watchlist, gainers, losers)
    thumbnail = pick_cover_thumbnail(articles)
    bot       = Bot(token=BOT_TOKEN)

    if thumbnail:
        try:
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=thumbnail,
                caption=message,
                parse_mode=ParseMode.HTML,
            )
            log.info("Digest with cover image sent ✅")
            return
        except Exception as e:
            log.warning(f"Photo send failed, falling back to text: {e}")

    await bot.send_message(
        chat_id=CHAT_ID,
        text=message,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    log.info("Digest (text only) sent ✅")

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Starting one-shot digest run...")
    asyncio.run(send_digest())
