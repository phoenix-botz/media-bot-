"""
╔══════════════════════════════════════════════════════════════╗
║                    MEDIA CHANNEL BOT                         ║
║                                                              ║
║  A Telegram bot that gates media links behind channel        ║
║  membership. Users must join your channels before they       ║
║  receive streaming/purchase links for movies, shows,         ║
║  and games.                                                  ║
║                                                              ║
║  FLOW:                                                       ║
║  1. You post media info in your private channel              ║
║  2. Each post has a button with a deep link to this bot      ║
║  3. Bot checks if user has joined both required channels     ║
║  4. If not → shows join buttons                              ║
║  5. If yes → sends streaming/purchase links from Sheet       ║
║                                                              ║
║  SETUP:                                                      ║
║  - Set environment variables on Render (see CONFIG below)    ║
║  - Add media rows to your Google Sheet                       ║
║  - Post in your channel with button: t.me/YourBot?start=slug ║
╚══════════════════════════════════════════════════════════════╝
"""

# ── Standard library ──────────────────────────────────────────
import os
import json
import logging
import asyncio

# ── Third-party ───────────────────────────────────────────────
import gspread
from google.oauth2.service_account import Credentials
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatMember,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ══════════════════════════════════════════════════════════════
#  LOGGING
#  Shows bot activity in Render logs. httpx is silenced because
#  it spams repetitive webhook request lines.
# ══════════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  CONFIG
#  All sensitive values are stored as environment variables on
#  Render — never hardcode tokens or credentials in code.
#
#  Required environment variables on Render:
#  ┌─────────────────────┬────────────────────────────────────┐
#  │ BOT_TOKEN           │ Token from @BotFather              │
#  │ GOOGLE_CREDENTIALS  │ Full JSON contents of credentials  │
#  │                     │ file from Google Cloud             │
#  └─────────────────────┴────────────────────────────────────┘
# ══════════════════════════════════════════════════════════════

# Bot token from @BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# ── Private media channel (where you post movies/shows/games) ─
# MEDIA_CHANNEL_ID: numeric ID — forward a message to @RawDataBot to get it
# MEDIA_CHANNEL_INVITE: the invite link for private channels (starts with +)
MEDIA_CHANNEL_ID     = -1003618892531
MEDIA_CHANNEL_INVITE = "https://t.me/+oqQmcy7LvqQ2ZjRk"

# ── Public entertainment news channel ─────────────────────────
# NEWS_CHANNEL_ID: numeric ID
# NEWS_CHANNEL_USERNAME: username without @
NEWS_CHANNEL_ID       = -1003772399725
NEWS_CHANNEL_USERNAME = "WorldViewEntertainment"

# ── Google Sheet ID ───────────────────────────────────────────
# Found in the sheet URL between /d/ and /edit
# Example URL: docs.google.com/spreadsheets/d/THIS_PART/edit
SHEET_ID = "1-6_smvQ3fJJNItkq0KCGPKrAhpiZk7K_aD6y6w8e4xw"   # ← REPLACE THIS

# ── Google Sheet column names (must match your sheet headers) ─
COL_SLUG       = "slug"
COL_TITLE      = "title"
COL_TYPE       = "type"
COL_LINK_LABEL = "link_label"
COL_LINK_URL   = "link_url"


# ══════════════════════════════════════════════════════════════
#  GOOGLE SHEETS
#  Reads media data from your Google Sheet instead of hardcoded
#  values, so you can add new media without touching code.
#
#  Sheet format (one row per link):
#  | slug    | title              | type  | link_label       | link_url             |
#  | dune2   | Dune: Part Two     | Movie | 🎬 Stream on Max | https://max.com      |
#  | dune2   | Dune: Part Two     | Movie | 🛒 Buy on iTunes | https://apple.com/tv |
# ══════════════════════════════════════════════════════════════

def get_google_sheet():
    """
    Authenticate with Google and return the first sheet.
    Uses the GOOGLE_CREDENTIALS environment variable which
    should contain the full JSON from your service account key file.
    """
    creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(
        creds_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1


def get_media(slug: str) -> dict | None:
    """
    Look up a media entry by its slug in the Google Sheet.

    Returns a dict like:
        {
            "title": "Dune: Part Two (2024)",
            "type": "Movie",
            "links": [("🎬 Stream on Max", "https://max.com"), ...]
        }
    Returns None if the slug is not found or the sheet is unreachable.
    """
    try:
        sheet = get_google_sheet()
        rows = sheet.get_all_records()

        title      = None
        media_type = None
        links      = []

        for row in rows:
            if row[COL_SLUG].lower() == slug.lower():
                title      = row[COL_TITLE]
                media_type = row[COL_TYPE]
                links.append((row[COL_LINK_LABEL], row[COL_LINK_URL]))

        if not title:
            logger.warning(f"Slug not found in sheet: '{slug}'")
            return None

        return {"title": title, "type": media_type, "links": links}

    except Exception as e:
        logger.error(f"Google Sheet error for slug '{slug}': {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  MEMBERSHIP CHECK
#  The bot must be an Admin in both channels for this to work.
# ══════════════════════════════════════════════════════════════

async def is_channel_member(bot, user_id: int, channel_id: int) -> bool:
    """
    Returns True if the user is a member, admin, or owner of the channel.
    Returns False if they haven't joined or if the check fails.
    """
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in (
            ChatMember.MEMBER,
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
        )
    except Exception as e:
        logger.warning(f"Membership check failed for user {user_id} in {channel_id}: {e}")
        return False


# ══════════════════════════════════════════════════════════════
#  KEYBOARDS
#  Inline button layouts sent with bot messages.
# ══════════════════════════════════════════════════════════════

def join_keyboard(media_slug: str) -> InlineKeyboardMarkup:
    """
    Shown when the user hasn't joined one or both channels.
    Includes join buttons and a 'Check Again' button.
    """
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Media Channel", url=MEDIA_CHANNEL_INVITE)],
        [InlineKeyboardButton("📰 Join News Channel",  url=f"https://t.me/{NEWS_CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ I've Joined — Check Again", callback_data=f"check:{media_slug}")],
    ])


def links_keyboard(media_slug: str) -> InlineKeyboardMarkup | None:
    """
    Shown after membership is verified.
    Contains one button per streaming/purchase link from the sheet.
    """
    media = get_media(media_slug)
    if not media:
        return None
    buttons = [
        [InlineKeyboardButton(label, url=url)]
        for label, url in media["links"]
    ]
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════════════════════
#  HANDLERS
#  These functions respond to user interactions.
# ══════════════════════════════════════════════════════════════

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when a user opens the bot, with or without a deep link.

    Without deep link (/start):
        Shows a welcome message.

    With deep link (/start dune2):
        Checks channel membership, then either:
        - Shows join buttons if not a member
        - Sends media links if member of both channels
    """
    user = update.effective_user
    args = context.args  # words after /start, e.g. ["dune2"]

    # No slug — user opened bot directly without a deep link
    if not args:
        await update.message.reply_text(
            "👋 Welcome!\n\n"
            "To get streaming links, click a media post button in the channel."
        )
        return

    media_slug = args[0].lower()
    media = get_media(media_slug)

    # Slug not found in Google Sheet
    if not media:
        await update.message.reply_text(
            "❌ Media not found. It may have been removed.\n"
            "Check the channel for updated posts."
        )
        return

    # Check if user has joined both channels
    in_media_channel = await is_channel_member(context.bot, user.id, MEDIA_CHANNEL_ID)
    in_news_channel  = await is_channel_member(context.bot, user.id, NEWS_CHANNEL_ID)

    if in_media_channel and in_news_channel:
        # User qualifies — send the links
        await send_links(update.message, media, media_slug)
    else:
        # Build list of channels the user still needs to join
        missing = []
        if not in_media_channel: missing.append("📢 Media Channel")
        if not in_news_channel:  missing.append("📰 News Channel")

        await update.message.reply_text(
            f"🔒 *Access Locked*\n\n"
            f"To get links for *{media['title']}*, please join:\n"
            + "\n".join(f"  • {ch}" for ch in missing)
            + "\n\nThen tap *I've Joined* below ✅",
            parse_mode="Markdown",
            reply_markup=join_keyboard(media_slug),
        )


async def handle_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when a user taps 'I've Joined — Check Again'.
    Re-checks membership and either unlocks or prompts again.
    """
    query = update.callback_query
    await query.answer()  # removes the loading spinner on the button

    # Extract the slug from callback data (format: "check:dune2")
    _, media_slug = query.data.split(":", 1)
    user  = query.from_user
    media = get_media(media_slug)

    if not media:
        await query.edit_message_text("❌ Media not found.")
        return

    in_media_channel = await is_channel_member(context.bot, user.id, MEDIA_CHANNEL_ID)
    in_news_channel  = await is_channel_member(context.bot, user.id, NEWS_CHANNEL_ID)

    if in_media_channel and in_news_channel:
        # Membership confirmed — replace the locked message with links
        await query.edit_message_text(
            f"✅ *Verified!* Here are the links for *{media['title']}*:",
            parse_mode="Markdown",
            reply_markup=links_keyboard(media_slug),
        )
    else:
        # Still not a member — show alert popup and keep join buttons
        missing = []
        if not in_media_channel: missing.append("📢 Media Channel")
        if not in_news_channel:  missing.append("📰 News Channel")

        await query.answer("⚠️ You haven't joined all channels yet!", show_alert=True)
        await query.edit_message_text(
            f"🔒 *Still locked.*\n\nYou still need to join:\n"
            + "\n".join(f"  • {ch}" for ch in missing)
            + "\n\nJoin and tap the button again ✅",
            parse_mode="Markdown",
            reply_markup=join_keyboard(media_slug),
        )


async def handle_debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command: /debug <slug>
    Tests the Google Sheet connection for a specific slug.
    Example: /debug dune2

    Remove or restrict this command once everything is working.
    """
    args = context.args
    slug = args[0].lower() if args else "test"

    try:
        media = get_media(slug)
        if media:
            await update.message.reply_text(
                f"✅ Sheet connection works!\n\n"
                f"Found: *{media['title']}*\n"
                f"Type: {media['type']}\n"
                f"Links: {len(media['links'])}",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"⚠️ Sheet connected but slug *'{slug}'* not found.\n"
                f"Check your sheet for typos.",
                parse_mode="Markdown",
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Sheet error:\n{str(e)}")


# ══════════════════════════════════════════════════════════════
#  SEND LINKS
#  Shared helper used by both handle_start and handle_check_callback.
# ══════════════════════════════════════════════════════════════

async def send_links(message, media: dict, media_slug: str):
    """Send the media title, type, and streaming/purchase buttons."""
    await message.reply_text(
        f"🎉 *{media['title']}*\n"
        f"📂 {media['type']}\n\n"
        f"Choose where to watch or buy:",
        parse_mode="Markdown",
        reply_markup=links_keyboard(media_slug),
    )


# ══════════════════════════════════════════════════════════════
#  MAIN
#  Registers all handlers and starts the webhook server.
#  Render automatically provides PORT and RENDER_EXTERNAL_HOSTNAME.
# ══════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Register command and callback handlers
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("debug", handle_debug))
    app.add_handler(CallbackQueryHandler(handle_check_callback, pattern=r"^check:"))

    logger.info("Bot is starting…")

    # Run as a webhook (required for Render — polling would cause the
    # service to spin down due to inactivity on Render's free tier)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=BOT_TOKEN,
        webhook_url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{BOT_TOKEN}",
        secret_token="mymediabot2879",  # ← change to any random string
    )


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    main()
