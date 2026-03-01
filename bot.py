"""
Media Channel Bot - Verifies channel membership before sending media links.

Setup:
1. Install: pip install python-telegram-bot==20.7
2. Create a bot via @BotFather and get your BOT_TOKEN
3. Fill in the CONFIG section below
4. Run: python media_bot.py

How it works:
- You post media info in your private channel with a button linking to the bot
- Users click the button → bot checks if they've joined required channels
- If not joined → bot tells them to join and provides join links
- If joined → bot sends streaming/purchase links for that media
"""
import os
import logging
import asyncio
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ─────────────────────────────────────────────
#  CONFIG — Fill these in before running
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Your private media channel (where you post movies/shows/games)
MEDIA_CHANNEL_ID = -1003618892531        # e.g. -1001234567890
MEDIA_CHANNEL_USERNAME = "+oqQmcy7LvqQ2ZjRk"    # without @

# Your public entertainment news channel
NEWS_CHANNEL_ID = -1003772399725
NEWS_CHANNEL_USERNAME = "WorldViewEntertainment"     # without @

# ─────────────────────────────────────────────
#  MEDIA DATABASE
#  Add entries here when you post new media.
#  key = a short slug you'll use in the deep link
# ─────────────────────────────────────────────
MEDIA_DB = {
    "dune2": {
        "title": "Dune: Part Two (2024)",
        "type": "Movie",
        "poster": "https://i.imgur.com/example.jpg",  # optional
        "links": [
            ("🎬 Stream on Max", "https://www.max.com"),
            ("🍿 Stream on Prime Video", "https://www.amazon.com/primevideo"),
            ("🛒 Buy on Apple TV", "https://tv.apple.com"),
            ("🛒 Buy on Google Play", "https://play.google.com/store/movies"),
        ],
    },
    "shogun": {
        "title": "Shōgun – Season 1 (2024)",
        "type": "TV Show",
        "links": [
            ("🎬 Stream on Hulu", "https://www.hulu.com"),
            ("🛒 Buy on Apple TV", "https://tv.apple.com"),
        ],
    },
    "ff7rebirth": {
        "title": "Final Fantasy VII Rebirth (2024)",
        "type": "Game",
        "links": [
            ("🎮 Buy on PlayStation Store", "https://store.playstation.com"),
            ("🖥️ Buy on Steam", "https://store.steampowered.com"),
        ],
    },
}

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

async def is_member(bot, user_id: int, channel_id: int) -> bool:
    """Return True if user is a member/admin/owner of the channel."""
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in (
            ChatMember.MEMBER,
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
        )
    except Exception:
        return False


def build_join_keyboard(media_slug: str) -> InlineKeyboardMarkup:
    """Keyboard shown when user hasn't joined required channels."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Media Channel", url=f"https://t.me/{MEDIA_CHANNEL_USERNAME}")],
        [InlineKeyboardButton("📰 Join News Channel",  url=f"https://t.me/{NEWS_CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ I've Joined — Check Again",
                              callback_data=f"check:{media_slug}")],
    ])


def build_links_keyboard(media_slug: str) -> InlineKeyboardMarkup:
    """Keyboard with streaming/purchase links."""
    media = MEDIA_DB.get(media_slug)
    if not media:
        return None
    buttons = [[InlineKeyboardButton(label, url=url)] for label, url in media["links"]]
    return InlineKeyboardMarkup(buttons)


# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Entry point. Called when user clicks the deep link from the channel post.
    Deep link format:  https://t.me/YourBot?start=dune2
    """
    user = update.effective_user
    args = context.args  # list of words after /start

    if not args:
        await update.message.reply_text(
            "👋 Welcome! Click a media link from the channel to get started."
        )
        return

    media_slug = args[0].lower()
    media = MEDIA_DB.get(media_slug)

    if not media:
        await update.message.reply_text("❌ Media not found. It may have been removed.")
        return

    # Check membership
    in_media  = await is_member(context.bot, user.id, MEDIA_CHANNEL_ID)
    in_news   = await is_member(context.bot, user.id, NEWS_CHANNEL_ID)

    if in_media and in_news:
        await send_media_links(update.message, media, media_slug)
    else:
        missing = []
        if not in_media: missing.append(f"@{MEDIA_CHANNEL_USERNAME}")
        if not in_news:  missing.append(f"@{NEWS_CHANNEL_USERNAME}")

        await update.message.reply_text(
            f"🔒 *Access Locked*\n\n"
            f"To receive links for *{media['title']}*, please join:\n"
            + "\n".join(f"  • {ch}" for ch in missing)
            + "\n\nThen tap the button below ✅",
            parse_mode="Markdown",
            reply_markup=build_join_keyboard(media_slug),
        )


async def check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when user taps 'I've Joined — Check Again'."""
    query = update.callback_query
    await query.answer()

    _, media_slug = query.data.split(":", 1)
    user = query.from_user
    media = MEDIA_DB.get(media_slug)

    if not media:
        await query.edit_message_text("❌ Media not found.")
        return

    in_media = await is_member(context.bot, user.id, MEDIA_CHANNEL_ID)
    in_news  = await is_member(context.bot, user.id, NEWS_CHANNEL_ID)

    if in_media and in_news:
        await query.edit_message_text(
            f"✅ *Verified!* Here are the links for *{media['title']}*:",
            parse_mode="Markdown",
            reply_markup=build_links_keyboard(media_slug),
        )
    else:
        missing = []
        if not in_media: missing.append(f"@{MEDIA_CHANNEL_USERNAME}")
        if not in_news:  missing.append(f"@{NEWS_CHANNEL_USERNAME}")

        await query.answer(
            "⚠️ You haven't joined all channels yet!", show_alert=True
        )
        await query.edit_message_text(
            f"🔒 *Still locked.*\n\nPlease join:\n"
            + "\n".join(f"  • {ch}" for ch in missing)
            + "\n\nThen try again ✅",
            parse_mode="Markdown",
            reply_markup=build_join_keyboard(media_slug),
        )


async def send_media_links(message, media: dict, media_slug: str):
    """Send the media info with streaming/purchase buttons."""
    text = (
        f"🎉 *{media['title']}*\n"
        f"📂 Type: {media['type']}\n\n"
        f"Choose where to watch or buy:"
    )
    await message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=build_links_keyboard(media_slug),
    )


# ─────────────────────────────────────────────
#  ADMIN COMMAND: /addmedia  (run in private chat with bot)
# ─────────────────────────────────────────────

async def add_media_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remind admin how to add media and get deep links."""
    text = (
        "📝 *How to add new media:*\n\n"
        "1. Edit `MEDIA_DB` in `media_bot.py`\n"
        "2. Restart the bot\n"
        "3. The deep link for a slug like `dune2` is:\n"
        f"   `https://t.me/{(await context.bot.get_me()).username}?start=dune2`\n\n"
        "Paste that link as a button in your channel post using:\n"
        "`@ButtonBot` or the channel post button feature."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addmedia", add_media_help))
    app.add_handler(CallbackQueryHandler(check_callback, pattern=r"^check:"))

    logger.info("Bot is running…")
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        webhook_url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}",
        secret_token="your-secret-string-here",
    )

if __name__ == "__main__":
    import asyncio
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()

