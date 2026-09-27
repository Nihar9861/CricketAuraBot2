"""
Handlers for the lobby phase: /start menu, delivery/style setup,
/joingame, /leavegame, /extend, /forcestart, /endgame, /restore.
"""

import asyncio
import io
import json
import logging
import time
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from . import config
from .state import Lobby, Match, GamePhase, active_lobbies, active_matches, user_active_chat, Player
from .match_engine import start_match
from .media import send_banner

logger = logging.getLogger(__name__)


async def _update_screen(query, text: str, keyboard=None, banner_image=None):
    """
    Updates the current callback message's text/caption.
    If banner_image is given and the file exists, swaps the photo too
    (via edit_message_media) -- otherwise just edits the caption or text
    in place, whichever the message currently is. Any failure (missing
    file or Telegram rejecting it) is logged and falls back to a plain
    text/caption edit so the bot never crashes over a bad image.
    """
    if banner_image is not None:
        path = Path(banner_image)
        if not path.is_file():
            logger.warning("Configured banner image not found, using text instead: %s", path.resolve())
        else:
            try:
                media = InputMediaPhoto(media=open(path, "rb"), caption=text, parse_mode="HTML")
                await query.edit_message_media(media=media, reply_markup=keyboard)
                return
            except TelegramError as e:
                logger.warning("Telegram rejected banner image %s (%s) -- falling back to text.", path, e)

    if query.message.caption is not None or query.message.photo:
        await query.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


# ---------------- /start ----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        # If a user /starts the bot in DM without a deep-link payload,
        # just greet them -- deep-link cases are handled separately below.
        if context.args:
            await handle_dm_deeplink(update, context)
            return
        await update.message.reply_text(
            "👋 Hi! Add me to a group and use /start there to set up a Cricket Aura match."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏏 Team", callback_data="mode:team"),
         InlineKeyboardButton("👤 Solo", callback_data="mode:solo")],
        [InlineKeyboardButton("🔨 Auction", callback_data="mode:auction")],
        [InlineKeyboardButton("❌ Cancel", callback_data="mode:cancel")],
    ])
    text = "🏏 <b>SELECT GAME MODE</b>"
    await send_banner(context.bot, update.effective_chat.id, config.START_BANNER_IMAGE, text, reply_markup=keyboard)


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]

    if choice == "cancel":
        await query.edit_message_text("❌ Cancelled.")
        return

    if choice in ("team", "auction"):
        msg = "🚧 This mode is coming soon! Try Solo for now."
        await _update_screen(query, msg)
        return

    # Solo mode chosen -> ask deliveries per spell
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"⚾ {n} ball{'s' if n != 1 else ''}", callback_data=f"deliveries:{n}")
         for n in config.DELIVERY_OPTIONS],
    ])
    text = (
        "👤 <b>SOLO MATCH SETUP</b>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        "How many deliveries should each bowler get before rotation?\n"
        f"The default is <b>{config.DEFAULT_DELIVERIES_PER_SPELL} balls</b>.\n\n"
        "Choose a delivery limit to continue."
    )
    await _update_screen(query, text, keyboard, banner_image=config.SOLO_BANNER_IMAGE)


async def deliveries_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    n = int(query.data.split(":")[1])
    context.chat_data["pending_deliveries"] = n

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Normal Mode", callback_data="style:normal"),
         InlineKeyboardButton("🛡️ Spam-Free", callback_data="style:spam_free")],
        [InlineKeyboardButton("⬅️ Change balls", callback_data="mode:solo")],
    ])
    text = (
        "👤 <b>SOLO MATCH SETUP</b>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        f"⚾ Deliveries per bowler: <b>{n}</b>\n\n"
        "Choose the game style.\n"
        "Spam-Free prevents a bowler from using the same number three times consecutively."
    )
    await _update_screen(query, text, keyboard)


async def style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    style = query.data.split(":")[1]
    deliveries = context.chat_data.get("pending_deliveries", config.DEFAULT_DELIVERIES_PER_SPELL)
    chat_id = query.message.chat_id

    lobby = Lobby(
        chat_id=chat_id,
        created_by=query.from_user.id,
        deliveries_per_spell=deliveries,
        game_style=style,
    )
    lobby.deadline = time.time() + config.LOBBY_DURATION_SECONDS
    active_lobbies[chat_id] = lobby

    text = (
        "👤 <b>SOLO MODE SELECTED</b>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        "↪ Join using /joingame\n"
        "✖ Leave using /leavegame\n"
        f"⏳ Lobby closes in <b>{config.LOBBY_DURATION_SECONDS // 60} minutes</b>.\n"
        f"⚡ Minimum <b>{config.MIN_PLAYERS} players</b> required to start.\n\n"
        "🔧 Admins: /extend 30 | /forcestart"
    )
    await _update_screen(query, text)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(f"👤 <b>SOLO MODE</b> — Join via /joingame\n"
              f"⏳ Closes in {config.LOBBY_DURATION_SECONDS // 60} min | "
              f"Min {config.MIN_PLAYERS} players"),
        parse_mode="HTML",
    )

    asyncio.create_task(lobby_timer_task(context, chat_id, lobby))


async def lobby_timer_task(context: ContextTypes.DEFAULT_TYPE, chat_id: int, lobby: Lobby):
    """Background task that nudges the group and auto-closes the lobby."""
    nudge_points = sorted(config.LOBBY_NUDGE_AT_SECONDS_LEFT, reverse=True)
    while True:
        await asyncio.sleep(5)
        current_lobby = active_lobbies.get(chat_id)
        if current_lobby is not lobby or lobby.closed:
            return  # lobby replaced or already closed/started

        left = lobby.seconds_left()

        for point in list(nudge_points):
            if left <= point:
                nudge_points.remove(point)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(f"⏳ <b>{'1 minute' if point == 60 else f'{point} seconds'} left</b> to join!\n"
                          f"Players: {lobby.player_count} | Need: {config.MIN_PLAYERS}\n"
                          f"📣 /joingame"),
                    parse_mode="HTML",
                )

        if left <= 0:
            lobby.closed = True
            if lobby.player_count >= config.MIN_PLAYERS:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏳ Lobby closed! Starting with {lobby.player_count} players.",
                )
                # Re-check in case /forcestart already started this exact
                # lobby while the send_message above was in flight.
                if chat_id not in active_matches:
                    await start_match(context, chat_id, lobby)
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(f"⚠️ Not enough players joined ({lobby.player_count}/{config.MIN_PLAYERS}). "
                          f"Match cancelled. Use /start to try again."),
                )
                active_lobbies.pop(chat_id, None)
            return


# ---------------- /joingame ----------------

async def joingame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = active_lobbies.get(chat_id)

    if lobby is None or lobby.closed:
        await update.message.reply_text("⚠️ No open lobby right now. Use /start to create one.")
        return

    user = update.effective_user
    if user.id in lobby.players:
        await update.message.reply_text("⚠️ You're already in the lobby.")
        return

    if lobby.player_count >= config.MAX_PLAYERS:
        await update.message.reply_text(f"⚠️ Lobby is full ({config.MAX_PLAYERS} players max).")
        return

    lobby.add_player(user.id, user.first_name, user.username)
    name = lobby.players[user.id].mention

    if lobby.player_count >= config.MIN_PLAYERS:
        await update.message.reply_text(
            f"✅ <b>{name}</b> joined! ({lobby.player_count} players) — ready to start! ✅",
            parse_mode="HTML",
        )
    else:
        needed = config.MIN_PLAYERS - lobby.player_count
        await update.message.reply_text(
            f"✅ <b>{name}</b> joined! ({lobby.player_count} players) — {needed} more needed!",
            parse_mode="HTML",
        )


# ---------------- /leavegame ----------------

async def leavegame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = active_lobbies.get(chat_id)
    user = update.effective_user

    if lobby is None or lobby.closed:
        await update.message.reply_text("⚠️ No open lobby right now.")
        return

    if user.id in lobby.players:
        mention = lobby.players[user.id].mention
        lobby.remove_player(user.id)
        await update.message.reply_text(
            f"👋 <b>{mention}</b> left the lobby. ({lobby.player_count} players)",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text("⚠️ You're not in the lobby.")


# ---------------- /extend (admin) ----------------

async def extend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = active_lobbies.get(chat_id)

    if lobby is None or lobby.closed:
        await update.message.reply_text("⚠️ No open lobby right now.")
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("⚠️ Only group admins can extend the lobby.")
        return

    seconds = config.LOBBY_EXTEND_SECONDS
    if context.args:
        try:
            seconds = int(context.args[0])
        except ValueError:
            pass

    lobby.deadline += seconds
    await update.message.reply_text(
        f"⏰ Lobby extended by {seconds}s.\nNew time remaining: {lobby.seconds_left()}s"
    )


# ---------------- /forcestart (admin) ----------------

async def forcestart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    lobby = active_lobbies.get(chat_id)

    if lobby is None or lobby.closed:
        await update.message.reply_text("⚠️ No open lobby right now.")
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("⚠️ Only group admins can force start.")
        return

    if lobby.player_count < config.MIN_PLAYERS:
        await update.message.reply_text(
            f"⚠️ Not enough players!\nYou have {lobby.player_count}, need at least {config.MIN_PLAYERS}.\n"
            f"Tell more people to /joingame!"
        )
        return

    lobby.closed = True
    await update.message.reply_text(f"⚡ Force starting with {lobby.player_count} players!")
    await start_match(context, chat_id, lobby)


async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == ChatType.PRIVATE:
        return True
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ("administrator", "creator")


# ---------------- /endgame (admin) ----------------

async def endgame_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    match = active_matches.get(chat_id)

    if match is None:
        await update.message.reply_text("⚠️ No match is currently running in this chat.")
        return

    if not await _is_admin(update, context):
        await update.message.reply_text("⚠️ Only group admins can end the game.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 End Completely", callback_data="endgame:complete"),
            InlineKeyboardButton("💾 End & Get Backup", callback_data="endgame:backup"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="endgame:cancel")],
    ])

    text = (
        "⚠️ <b>END GAME?</b>\n\n"
        "🛑 <b>End Completely</b>\n"
        "Stop the match without creating a backup.\n\n"
        "📄 <b>End & Backup</b>\n"
        "Stop the match and save a restorable JSON copy.\n\n"
        "Choose an option below."
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def endgame_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    action = query.data.split(":", 1)[1]

    if not await _is_admin(update, context):
        await query.answer("⚠️ Only group admins can do this.", show_alert=True)
        return

    match = active_matches.get(chat_id)
    if match is None:
        await query.answer()
        await query.edit_message_text("⚠️ No match is currently running in this chat.")
        return

    if action == "cancel":
        await query.answer("Cancelled.")
        await query.edit_message_text("✅ Match continues — nothing was changed.")
        return

    if action == "backup":
        backup_json = json.dumps(match.to_backup_dict(), indent=2)
        file_bytes = io.BytesIO(backup_json.encode("utf-8"))
        file_bytes.name = f"cricket_backup_{chat_id}_{int(time.time())}.json"
        await context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(file_bytes, filename=file_bytes.name),
            caption="💾 Match backup saved. Use /restore (replying to this file, or with it attached) to resume later.",
        )

    await query.answer()
    await query.edit_message_text("🛑 Match ended by admin.")

    for uid in match.batting_order:
        user_active_chat.pop(uid, None)
    active_matches.pop(chat_id, None)


# ---------------- /restore (admin) ----------------

async def restore_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await _is_admin(update, context):
        await update.message.reply_text("⚠️ Only group admins can restore a match.")
        return

    if chat_id in active_matches:
        await update.message.reply_text("⚠️ A match is already running in this chat. End it first before restoring.")
        return

    doc = None
    if update.message.document is not None:
        doc = update.message.document
    elif update.message.reply_to_message and update.message.reply_to_message.document:
        doc = update.message.reply_to_message.document

    if doc is None:
        await update.message.reply_text(
            "⚠️ Send /restore attached with the backup .json file, or reply to the backup file with /restore."
        )
        return

    if not doc.file_name.endswith(".json"):
        await update.message.reply_text("⚠️ That doesn't look like a backup JSON file.")
        return

    try:
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        data = json.loads(bytes(raw).decode("utf-8"))
        match = Match.from_backup_dict(data)
    except Exception as e:
        logger.warning("Failed to restore match backup: %s", e)
        await update.message.reply_text("⚠️ Couldn't read that backup file — it may be corrupted or invalid.")
        return

    match.chat_id = chat_id
    match.phase = GamePhase.IN_PROGRESS
    active_matches[chat_id] = match
    for uid in match.batting_order:
        user_active_chat[uid] = chat_id

    from .match_engine import send_next_bowl_prompt, send_bat_prompt, match_timeout_task
    asyncio.create_task(match_timeout_task(context, chat_id))

    batter = match.players[match.current_batter_id]
    bowler = match.players[match.current_bowler_id]
    await update.message.reply_text(
        f"✅ Match restored!\n"
        f"Score: {match.total_score}/{match.wickets} ({match.overs_str()} overs)\n"
        f"🏃 Batting: <b>{batter.mention}</b>\n"
        f"⚾ Bowling: <i>{bowler.mention}</i>",
        parse_mode="HTML",
    )

    if match.waiting_on == "bowler":
        await send_next_bowl_prompt(context, match)
    else:
        await send_bat_prompt(context, match)


# ---------------- DM deep-link entry point ----------------

async def handle_dm_deeplink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when a user opens the bot in DM via the DELIVER BALL deep link."""
    from .match_engine import handle_dm_start
    await handle_dm_start(update, context)
