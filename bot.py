import os
import json
import time
import logging
import re
import feedparser
import urllib.request
from datetime import datetime, timedelta
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
MARKET_SCAN  = 250   # scan top 250 coins for gainers/losers

HISTORY_FILE          = "sent_history.json"
HISTORY_RETENTION_DAYS = 14
MAX_ARTICLE_AGE_DAYS   = 5   # ignore stale entries still lingering in a feed

# Last-resort cover image when no article has an extractable thumbnail —
# committed to this repo and served for free via raw.githubusercontent.com,
# since the repo is public. Guarantees every digest has a header image.
FALLBACK_COVER_IMAGE = "https://raw.githubusercontent.com/jowrigs/slowjow_bot/main/cover.png"

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
    {"name": "CoinDesk",         "category": "news",  "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt",          "category": "news",  "url": "https://decrypt.co/feed"},
    {"name": "Cointelegraph",    "category": "news",  "url": "https://cointelegraph.com/rss"},
    {"name": "The Block",        "category": "news",  "url": "https://www.theblock.co/rss.xml"},
    {"name": "BeInCrypto",       "category": "news",  "url": "https://beincrypto.com/feed/"},
    {"name": "Coin Bureau",      "category": "video", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCqK_GSMbpiV8spgD3ZGloSw"},
    {"name": "DataDash",         "category": "video", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCCatR7nWbYrkVXdxXb4cGXw"},
    {"name": "Andrei Jikh",      "category": "video", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCF9IOB2TExg3QIBupFtBDxg"},
    {"name": "Two Minute Papers","category": "video", "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbfYPyITQ-7l4upoX8nvctg"},
    {"name": "r/CryptoCurrency", "category": "reddit", "url": "https://www.reddit.com/r/CryptoCurrency/top/.rss?t=day"},
    {"name": "r/artificial",     "category": "reddit", "url": "https://www.reddit.com/r/artificial/top/.rss?t=day"},
    {"name": "r/singularity",    "category": "reddit", "url": "https://www.reddit.com/r/singularity/top/.rss?t=day"},
    {"name": "r/AIInFinance",    "category": "reddit", "url": "https://www.reddit.com/r/AIInFinance/top/.rss?t=day"},
]

# Two ways an article qualifies: it names a known AI-crypto project outright,
# or it mentions an AI term AND a crypto term together (word-boundary regex,
# so "ai" doesn't false-match inside "claim"/"said", and "coin" doesn't
# false-match inside "coincidence"/"coined").
NAMED_AI_CRYPTO_PROJECTS = [
    "fetch.ai", "bittensor", "ocean protocol", "singularitynet", "render network",
    "numerai", "cortex network", "deepbrain chain", "alethea ai",
    r"\bagix\b", r"\bfet\b", r"\brndr\b", r"\btao\b", "near ai",
]
AI_TERMS = [
    r"\bai\b", "artificial intelligence", "machine learning", r"\bllm\b",
    "neural network", "generative ai", "ai agent", "ai model",
    "deep learning", "large language model",
]
CRYPTO_TERMS = [
    "crypto", "bitcoin", "ethereum", "blockchain", r"\btoken\b",
    r"\bcoin\b", "stablecoin", "web3", "defi", r"\bnft\b", r"\bdao\b",
    r"\bwallet\b", "crypto exchange", "token exchange",
]

WEEKLY_RECAP_TOP_N = 8

# ── Retry helpers ─────────────────────────────────────────────────────────────
def retry_call(fn, *args, retries=3, base_delay=2, label="call", **kwargs):
    """Run fn with linear backoff. Re-raises the last error if all attempts fail."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            log.warning(f"[{label}] attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(base_delay * attempt)
    raise last_exc

async def retry_async_call(fn, *args, retries=3, base_delay=2, label="call", **kwargs):
    """Async version of retry_call."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            log.warning(f"[{label}] attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                await asyncio.sleep(base_delay * attempt)
    raise last_exc

# ── Dedup history (committed back to the repo each run) ──────────────────────
def load_history() -> list[dict]:
    """Load history and prune to the retention window. Returns list of {link, date}."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        log.warning(f"Failed to read history, starting fresh: {e}")
        return []

    cutoff = datetime.now(pytz.UTC).date() - timedelta(days=HISTORY_RETENTION_DAYS)
    pruned = []
    for entry in data.get("sent", []):
        try:
            if datetime.fromisoformat(entry["date"]).date() >= cutoff:
                pruned.append(entry)
        except Exception:
            continue
    return pruned

def save_history(pruned_history: list[dict], new_articles: list[dict]):
    """new_articles: the article dicts actually sent this run (link/title/source/score)."""
    today = datetime.now(pytz.UTC).date().isoformat()
    new_entries = [
        {
            "link":   a["link"],
            "date":   today,
            "title":  a.get("title", ""),
            "source": a.get("source", ""),
            "score":  a.get("score", 0),
        }
        for a in new_articles
    ]
    merged = pruned_history + new_entries
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump({"sent": merged}, f, indent=2)
        log.info(f"History saved: {len(merged)} links retained (last {HISTORY_RETENTION_DAYS}d)")
    except Exception as e:
        log.warning(f"Failed to save history: {e}")

# ── Price helpers ─────────────────────────────────────────────────────────────
def p_fmt(price: float) -> str:
    return f"${price:,.2f}" if price >= 1 else f"${price:.4f}"

def c_fmt(change: float | None) -> tuple[str, str]:
    if change is None:
        return "–", "N/A"
    arrow = "▲" if change >= 0 else "▼"
    sign  = "+" if change >= 0 else ""
    return arrow, f"{sign}{change:.2f}%"

def fetch_watchlist() -> list[dict]:
    """Fetch live prices for BTC, ETH, XRP, TRX, POL. Raises on failure — caller retries."""
    ids = ",".join(t["id"] for t in WATCHLIST)
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
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
            results.append({"symbol": t["symbol"], "price": p_fmt(price), "change": c_str, "arrow": arrow})
    return results

def fetch_gainers_losers() -> tuple[list[dict], list[dict]]:
    """Fetch top 5 gainers/losers from the top N coins by market cap. Raises on failure."""
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc"
        f"&per_page={MARKET_SCAN}&page=1&price_change_percentage=24h"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "AICryptoBot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        coins = json.loads(resp.read())

    coins = [c for c in coins if c.get("price_change_percentage_24h") is not None]
    sorted_coins = sorted(coins, key=lambda c: c["price_change_percentage_24h"], reverse=True)

    def fmt(c: dict) -> dict:
        arrow, c_str = c_fmt(c["price_change_percentage_24h"])
        return {"symbol": c["symbol"].upper(), "price": p_fmt(c["current_price"]), "change": c_str, "arrow": arrow}

    gainers = [fmt(c) for c in sorted_coins[:5]]
    losers  = [fmt(c) for c in sorted_coins[-5:][::-1]]
    return gainers, losers

def format_ticker(watchlist: list[dict], gainers: list[dict], losers: list[dict]) -> str:
    lines = []
    if watchlist:
        lines.append("<b>PRICES</b>")
        for w in watchlist:
            lines.append(f"  {w['arrow']}  <b>{w['symbol']}</b>  {w['price']}  <i>{w['change']}</i>")
    if gainers or losers:
        lines.append("")
        lines.append("<b>MARKET MOVERS · 24H</b>")
        if gainers:
            lines.append("<b>Gainers</b>")
            for i, c in enumerate(gainers, 1):
                lines.append(f"  {i}. <b>{c['symbol']}</b>  {c['price']}  <i>{c['change']}</i>")
        if losers:
            lines.append("<b>Losers</b>")
            for i, c in enumerate(losers, 1):
                lines.append(f"  {i}. <b>{c['symbol']}</b>  {c['price']}  <i>{c['change']}</i>")
    return "\n".join(lines)

# ── Article fetching ───────────────────────────────────────────────────────────
def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)

def is_ai_related(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    if _matches_any(NAMED_AI_CRYPTO_PROJECTS, text):
        return True
    return _matches_any(AI_TERMS, text) and _matches_any(CRYPTO_TERMS, text)

def relevance_score(title: str, summary: str) -> int:
    """Higher = more clearly about the crypto x AI intersection. Used to
    order each section and to pick the weekly recap's top stories."""
    text = (title + " " + summary).lower()
    score = 3 if _matches_any(NAMED_AI_CRYPTO_PROJECTS, text) else 0
    score += sum(1 for p in AI_TERMS if re.search(p, text))
    score += sum(1 for p in CRYPTO_TERMS if re.search(p, text))
    return score

def is_recent(entry: dict, max_age_days: int = MAX_ARTICLE_AGE_DAYS) -> bool:
    parsed = entry.get("published_parsed")
    if not parsed:
        return True  # can't verify age — don't drop on a guess
    try:
        published = datetime(*parsed[:6], tzinfo=pytz.UTC)
        return (datetime.now(pytz.UTC) - published).days <= max_age_days
    except Exception:
        return True

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
    # Fallback: some feeds only embed an <img> inside the HTML summary/content,
    # with no structured media tag at all — pull the first one out directly.
    html = entry.get("summary", "") or ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)
    return None

def fetch_feed(feed_meta: dict) -> list:
    """Fetch one RSS feed. Raises on failure — caller retries."""
    url = feed_meta["url"]
    if "reddit.com" in url:
        req = urllib.request.Request(url, headers={"User-Agent": "AICryptoBot/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return feedparser.parse(resp.read()).entries
    return feedparser.parse(url).entries

def fetch_articles(already_sent: set[str]) -> list[dict]:
    articles = []
    for feed_meta in FEEDS:
        try:
            entries = retry_call(fetch_feed, feed_meta, retries=2, label=feed_meta["name"])
            for entry in entries[:20]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                link    = entry.get("link", "").strip()
                if not link or link in already_sent:
                    continue
                if not is_ai_related(title, summary):
                    continue
                if not is_recent(entry):
                    continue
                articles.append({
                    "source":    feed_meta["name"],
                    "category":  feed_meta["category"],
                    "title":     title.strip(),
                    "link":      link,
                    "thumbnail": extract_thumbnail(entry),
                    "score":     relevance_score(title, summary),
                })
        except Exception as e:
            log.warning(f"Failed to fetch {feed_meta['name']} after retries: {e}")

    seen, unique = set(), []
    for a in articles:
        if a["link"] not in seen:
            seen.add(a["link"])
            unique.append(a)
    unique.sort(key=lambda a: a["score"], reverse=True)
    return unique[:MAX_ARTICLES]

# ── Message builder ────────────────────────────────────────────────────────────
def build_message(articles, watchlist, gainers, losers) -> str:
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%B %d, %Y")

    lines = ["<b>CRYPTO × AI DAILY</b>"]
    lines.append(f"<i>{today} · Cryptocurrencies &amp; platforms powered by AI</i>\n")

    ticker = format_ticker(watchlist, gainers, losers)
    if ticker:
        lines.append(ticker)

    lines.append("\n─────────────────────")

    if not articles:
        lines.append("\nNo new cryptocurrency-using-AI updates since your last digest. Check back tomorrow!")
    else:
        news   = [a for a in articles if a["category"] == "news"]
        videos = [a for a in articles if a["category"] == "video"]
        reddit = [a for a in articles if a["category"] == "reddit"]

        if news:
            lines.append("\n<b>NEWS</b>")
            for a in news:
                lines.append(f"• <a href='{a['link']}'>{a['title']}</a> — <i>{a['source']}</i>")
        if videos:
            lines.append("\n<b>YOUTUBE</b>")
            for a in videos:
                lines.append(f"• <a href='{a['link']}'>{a['title']}</a> — <i>{a['source']}</i>")
        if reddit:
            lines.append("\n<b>REDDIT</b>")
            for a in reddit:
                lines.append(f"• <a href='{a['link']}'>{a['title']}</a> — <i>{a['source']}</i>")

    lines.append("\n─────────────────────")
    lines.append("<i>Crypto × AI Daily</i>")
    return "\n".join(lines)

def build_weekly_recap(history_raw: list[dict]) -> str:
    """Top stories from the trailing 7 days of persisted history. Empty string if none qualify."""
    cutoff = datetime.now(pytz.UTC).date() - timedelta(days=7)
    week_entries = []
    for e in history_raw:
        try:
            if datetime.fromisoformat(e["date"]).date() >= cutoff and e.get("title"):
                week_entries.append(e)
        except Exception:
            continue
    if not week_entries:
        return ""

    week_entries.sort(key=lambda e: e.get("score", 0), reverse=True)
    top = week_entries[:WEEKLY_RECAP_TOP_N]
    sources = {e.get("source", "?") for e in week_entries}

    lines = ["\n─────────────────────"]
    lines.append("<b>THIS WEEK</b>")
    lines.append(f"<i>{len(week_entries)} stories across {len(sources)} sources</i>\n")
    for e in top:
        lines.append(f"• <a href='{e['link']}'>{e['title']}</a> — <i>{e.get('source', '')}</i>")
    return "\n".join(lines)

def pick_cover_thumbnail(articles: list[dict]) -> str:
    """Always returns a usable image URL — YouTube first, then any article
    thumbnail, then the static fallback banner as a last resort."""
    for a in articles:
        if a["category"] == "video" and a.get("thumbnail"):
            return a["thumbnail"]
    for a in articles:
        if a.get("thumbnail"):
            return a["thumbnail"]
    return FALLBACK_COVER_IMAGE

def build_short_caption() -> str:
    """A fixed, deliberately short caption for the photo message — small and
    constant by design, so it can never approach Telegram's 1024-char cap.
    The full digest always follows as its own text message."""
    today = datetime.now(pytz.timezone(TIMEZONE)).strftime("%B %d, %Y")
    return f"<b>CRYPTO × AI DAILY</b>  ·  {today}"

# ── Send ──────────────────────────────────────────────────────────────────────
async def send_digest():
    history_raw   = load_history()
    already_sent  = {e["link"] for e in history_raw}

    log.info("Fetching data...")

    try:
        watchlist = retry_call(fetch_watchlist, retries=3, label="watchlist")
    except Exception as e:
        log.warning(f"Watchlist unavailable after retries, continuing without it: {e}")
        watchlist = []

    try:
        gainers, losers = retry_call(fetch_gainers_losers, retries=3, label="gainers/losers")
    except Exception as e:
        log.warning(f"Gainers/losers unavailable after retries, continuing without it: {e}")
        gainers, losers = [], []

    articles = fetch_articles(already_sent)
    log.info(f"Watchlist: {len(watchlist)} | Gainers: {len(gainers)} | New articles: {len(articles)}")

    message = build_message(articles, watchlist, gainers, losers)

    is_sunday = datetime.now(pytz.timezone(TIMEZONE)).weekday() == 6  # Monday=0 ... Sunday=6
    if is_sunday:
        recap = build_weekly_recap(history_raw + [
            {"link": a["link"], "title": a["title"], "source": a["source"], "score": a["score"]}
            for a in articles
        ])
        if recap:
            message += "\n" + recap

    thumbnail = pick_cover_thumbnail(articles)  # never None — falls back to a static banner
    bot       = Bot(token=BOT_TOKEN)

    # Always two messages: a photo with a short, fixed-length caption (can
    # never exceed Telegram's 1024-char cap, so no length branching needed),
    # then the full digest as its own text message (4096-char cap, comfortable
    # even with the Sunday recap appended).
    try:
        await retry_async_call(
            bot.send_photo, retries=3, label="send_photo",
            chat_id=CHAT_ID, photo=thumbnail, caption=build_short_caption(), parse_mode=ParseMode.HTML,
        )
        log.info("Cover image sent ✅")
    except Exception as e:
        log.warning(f"Cover image failed after retries, continuing without it: {e}")

    await retry_async_call(
        bot.send_message, retries=3, label="send_message",
        chat_id=CHAT_ID, text=message, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )
    log.info("Digest sent ✅")

    # Only persist dedup state after a confirmed successful send
    save_history(history_raw, articles)

async def send_failure_alert(error: Exception):
    """Best-effort notification so a broken run doesn't fail silently."""
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "<b>Crypto × AI Daily — send failed</b>\n"
                f"<i>{type(error).__name__}: {str(error)[:200]}</i>\n"
                "Check the GitHub Actions log for details."
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception as alert_error:
        log.error(f"Also failed to send failure alert: {alert_error}")

async def main():
    try:
        await send_digest()
    except Exception as e:
        log.error(f"Digest run failed: {e}")
        await send_failure_alert(e)
        raise  # keep the Actions run marked failed for visibility

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Starting one-shot digest run...")
    asyncio.run(main())
