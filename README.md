# 🤖 AI × Crypto Daily Bot

A Telegram bot that delivers a daily digest of the latest AI-related news in the cryptocurrency space, pulled from top crypto RSS feeds.

---

## 📦 Project Structure

```
ai_crypto_bot/
├── bot.py            # Main bot logic
├── requirements.txt  # Python dependencies
├── Procfile          # For Railway / Render deployment
├── .env.example      # Environment variable template
└── README.md
```

---

## 🚀 Deployment Guide

### Step 1 — Get Your Bot Token

If you haven't already:
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Run `/newbot` and follow the prompts
3. Copy the **bot token** you receive

### Step 2 — Get Your Chat ID

1. Start a conversation with your bot (send it any message)
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id": XXXXXXXXX}` — that number is your **Chat ID**

> To send to a **channel**: add the bot as an admin, then use `@yourchannel` as the Chat ID.

### Step 3 — Deploy to Railway (Recommended Free Option)

1. Push this project to a GitHub repository
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub**
3. Select your repo
4. Go to **Variables** and add:

   | Key | Value |
   |-----|-------|
   | `TELEGRAM_BOT_TOKEN` | your token from BotFather |
   | `TELEGRAM_CHAT_ID` | your chat/channel ID |
   | `SEND_HOUR` | `9` (or any hour in 24h UTC) |
   | `TIMEZONE` | e.g. `Asia/Manila` |

5. Railway will auto-detect the `Procfile` and start the worker

### Step 3 (Alternative) — Deploy to Render

1. Push this project to GitHub
2. Go to [render.com](https://render.com) → **New** → **Background Worker**
3. Connect your repo, set **Build Command**: `pip install -r requirements.txt`
4. Set **Start Command**: `python bot.py`
5. Add the same environment variables as above

---

## 🕘 Scheduling Notes

- `SEND_HOUR` is in **UTC** (0–23). Adjust for your timezone:
  - Philippines (PHT, UTC+8): set `SEND_HOUR=1` to get it at 9 AM local
  - New York (EST, UTC-5): set `SEND_HOUR=14` to get it at 9 AM local
- The bot also sends an **immediate digest on startup** so you can confirm everything works

---

## 📰 News Sources

| Source | Feed |
|--------|------|
| CoinDesk | RSS |
| Decrypt | RSS |
| Cointelegraph | RSS |
| The Block | RSS |
| BeInCrypto | RSS |

AI-related articles are filtered by keywords including: `AI`, `LLM`, `machine learning`, `ChatGPT`, `neural network`, `AI token`, `generative AI`, and more.

---

## ⚙️ Customization

- **Add sources**: Add entries to the `FEEDS` list in `bot.py`
- **Add keywords**: Extend the `AI_KEYWORDS` list in `bot.py`
- **Change max articles**: Set `MAX_ARTICLES` in `bot.py` (default: 8)
- **Change send time**: Update `SEND_HOUR` env variable
