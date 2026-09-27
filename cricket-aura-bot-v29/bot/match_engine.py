"""
The core match engine.

Flow per ball:
1. Bot posts "X now your turn to bowl" in GROUP with a deep-link button
   ("DELIVER BALL") that opens the bot in DM with payload bowl_<chat_id>.
2. Bowler opens DM (only needs to actually click it once ever -- after
   that the bot remembers them and just DMs directly), bot sends a
   run-up clip + "send 1-6".
3. Bowler types a number in DM. Bot stores it, confirms in DM.
4. Bot posts "Ball delivered - X your turn to bat, send 1-6" in the
   GROUP (with a video clip). The BATTER replies with a number
   DIRECTLY IN THE GROUP -- no DM step for batting.
5. Once both numbers are in, bot resolves the ball:
     - same number -> OUT (wicket clip + message in group)
     - different    -> runs = bowler's number (+ occasional bonus)
6. Bot updates spell count; rotates bowler after N balls;
   brings in new batter if wicket fell; continues until all_out().
7. On all_out(), bot posts final scorecard.

NOTE ON STATE: `waiting_on` tracks whether we're waiting on the bowler
(via DM) or the batter (via group message) for the CURRENT ball.

NOTE ON known_dm_users: once a user successfully opens the bot in DM
(via the deep link) or the bot successfully sends them a DM, we record
their id in state.known_dm_users. From then on we skip the "please
start the bot in PM" nagging and just try the DM silently -- Telegram
still requires them to have started the bot at least once, but we
don't need to keep telling them that once we know it's already done.
"""

import asyncio
import logging
import random
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import Forbidden, BadRequest
from telegram.ext import ContextTypes

from . import config
from .media import send_clip, render_motm_card, fetch_profile_photo_bytes
from .state import (
    Lobby,
    Match,
    GamePhase,
    Player,
    active_lobbies,
    active_matches,
    user_active_chat,
    known_dm_users,
)

logger = logging.getLogger(__name__)


# ==================== MATCH START ====================

async def start_match(context: ContextTypes.DEFAULT_TYPE, chat_id: int, lobby: Lobby):
    # Idempotency guard: if a match is already running for this chat (e.g.
    # the lobby timer and /forcestart both fired for the same lobby in a
    # race), don't start a second one -- this was causing the very first
    # bowl prompt to be announced twice.
    if chat_id in active_matches:
        return

    # Batting order follows JOIN order (first to /joingame bats first),
    # not randomized -- matches how the lobby fills up.
    batting_order = list(lobby.players.keys())

    match = Match(
        chat_id=chat_id,
        deliveries_per_spell=lobby.deliveries_per_spell,
        game_style=lobby.game_style,
        batting_order=batting_order,
        players=lobby.players,
    )

    first_batter = batting_order[0]
    match.current_batter_id = first_batter

    # First bowler + queue also follow a fixed order anchored to
    # batting_order (see pick_next_bowler in state.py) rather than random.
    remaining = [uid for uid in batting_order if uid != first_batter]
    first_bowler = remaining[0]
    match.current_bowler_id = first_bowler
    match.bowler_queue = remaining[1:]

    active_matches[chat_id] = match
    for uid in batting_order:
        user_active_chat[uid] = chat_id
    active_lobbies.pop(chat_id, None)

    asyncio.create_task(match_timeout_task(context, chat_id))

    batter = match.players[first_batter]
    bowler = match.players[first_bowler]

    text = (
        "🏏 <b>SOLO CRICKET BEGINS!</b>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        f"👤 Players: {len(batting_order)}\n"
        f"🏃 First Batter: <b>{batter.mention}</b>\n"
        f"⚾ First Bowler: <i>{bowler.mention}</i>\n"
        "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n\n"
        f"⚪ {match.deliveries_per_spell} deliveries per spell | "
        f"{'Normal mode' if match.game_style == config.GAME_STYLE_NORMAL else 'Spam-Free mode'}\n"
        "Same number = OUT 💀"
    )
    await send_clip(context.bot, chat_id, config.MATCH_START_CLIPS, text)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 <b>{batter.mention}</b>, you're batting first!",
        parse_mode="HTML",
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"👤 <i>{bowler.mention}</i>, you bowl first!",
        parse_mode="HTML",
    )

    await send_next_bowl_prompt(context, match)


# ==================== BOWL PROMPT (bowler acts via DM) ====================

async def send_next_bowl_prompt(context: ContextTypes.DEFAULT_TYPE, match: Match):
    match.waiting_on = "bowler"
    match.pending_bowler_number = None
    match.turn_started_at = time.time()
    match.turn_nudge_sent = False

    bowler = match.players[match.current_bowler_id]
    spell_no = match.balls_this_spell + 1

    deep_link = f"https://t.me/{config.BOT_USERNAME}?start=bowl_{match.chat_id}"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("↪ DELIVER BALL", url=deep_link)]])

    caption = (
        f"⚾ <i>{bowler.mention}</i> now your turn to bowl — send 1-6. "
        f"[ Spell {spell_no}/{match.deliveries_per_spell} ]"
    )
    await send_clip(context.bot, match.chat_id, config.BOWL_RUNUP_CLIPS, caption, reply_markup=keyboard)

    await _try_dm(
        context, match, user_id=match.current_bowler_id, player_name=bowler.mention,
        dm_text=f"⚾ Your turn to bowl — send a number 1-6.\n[ Spell {spell_no}/{match.deliveries_per_spell} ]",
    )


async def _try_dm(context: ContextTypes.DEFAULT_TYPE, match: Match, user_id: int, player_name: str, dm_text: str):
    """
    Sends a DM to the player. If they're already a known DM user, we just
    send it silently. If it's their first time (or the DM fails), we fall
    back to a group warning -- but only nag once per failure, not forever.
    """
    try:
        await context.bot.send_message(chat_id=user_id, text=dm_text)
        known_dm_users.add(user_id)
    except (Forbidden, BadRequest):
        # They haven't started the bot in PM yet (or blocked it).
        await context.bot.send_message(
            chat_id=match.chat_id,
            text=f"⚠️ Could not DM <i>{player_name}</i>. Please start the bot in PM, then tap DELIVER BALL again!",
            parse_mode="HTML",
        )


# ==================== BAT PROMPT (batter replies directly in GROUP) ====================

async def send_bat_prompt(context: ContextTypes.DEFAULT_TYPE, match: Match):
    match.waiting_on = "batter"
    match.turn_started_at = time.time()
    match.turn_nudge_sent = False

    batter = match.players[match.current_batter_id]
    spell_no = match.balls_this_spell + 1

    caption = (
        f"✅ Ball delivered — <b>{batter.mention}</b> your turn to bat. Send 1-6. "
        f"[ Spell {spell_no}/{match.deliveries_per_spell} ]"
    )
    # No deep-link / DM here -- batter answers right here in the group.
    await send_clip(context.bot, match.chat_id, config.BAT_FACING_CLIPS, caption)


# ==================== DM ENTRY (deep link, bowler only) ====================

async def handle_dm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User opened bot in DM via the DELIVER BALL deep link."""
    payload = context.args[0] if context.args else ""
    user_id = update.effective_user.id
    known_dm_users.add(user_id)   # they've now definitely started the bot in PM

    if "_" not in payload:
        await update.message.reply_text("👋 Welcome! Join a group match with /joingame to play.")
        return

    role, chat_id_str = payload.split("_", 1)
    try:
        chat_id = int(chat_id_str)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid link.")
        return

    match = active_matches.get(chat_id)
    if match is None:
        await update.message.reply_text("⚠️ That match has ended.")
        return

    if role == "bowl":
        if user_id != match.current_bowler_id:
            await update.message.reply_text("⚠️ It's not your turn to bowl.")
            return
        if match.waiting_on != "bowler":
            # They've already bowled this ball (or already got the prompt
            # via _try_dm) -- clicking DELIVER BALL again shouldn't re-post
            # the bot's start menu / a duplicate prompt.
            await update.message.reply_text("✅ Already got your delivery for this ball — head back to the group!")
            return
        if user_id in known_dm_users:
            # Known bowler re-clicking the button: they've already been
            # silently DMed the prompt by _try_dm, so don't duplicate it.
            return
        spell_no = match.balls_this_spell + 1
        # No video/gif here on purpose -- the run-up clip is shown in the
        # GROUP message only. The bowler's DM stays text-only.
        await update.message.reply_text(
            f"⚾ Now your turn to bowl — send 1-6. [ Spell {spell_no}/{match.deliveries_per_spell} ]"
        )
    else:
        await update.message.reply_text("⚠️ Unknown link.")


# ==================== DM NUMBER INPUT (bowler only) ====================

async def handle_dm_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from the DM text handler for any plain message in PRIVATE chat.
    Only the bowler's number arrives this way now. Returns True if the
    message was consumed as a game move, False otherwise.
    """
    user_id = update.effective_user.id
    known_dm_users.add(user_id)  # any message in DM proves they've started the bot

    text = (update.message.text or "").strip()
    if not text.isdigit():
        return False
    number = int(text)
    if not (1 <= number <= 6):
        await update.message.reply_text("⚠️ Please send a number between 1 and 6.")
        return True

    chat_id = user_active_chat.get(user_id)
    if chat_id is None:
        return False
    match = active_matches.get(chat_id)
    if match is None:
        return False

    if match.waiting_on != "bowler" or user_id != match.current_bowler_id:
        await update.message.reply_text("⚠️ It's not your turn to bowl right now.")
        return True

    if match.game_style == config.GAME_STYLE_SPAM_FREE and _is_spam(match, user_id, number):
        await update.message.reply_text(
            "🛡️ Spam-Free mode: you can't use the same number 3 times in a row. Pick a different number."
        )
        return True

    match.pending_bowler_number = number
    _record_bowler_number(match, user_id, number)

    spell_no = match.balls_this_spell + 1
    await update.message.reply_text(
        f"✅ Ball Delivered!\n⚾ Spell: {spell_no}/{match.deliveries_per_spell}\n\nHead back to the group!"
    )
    await send_bat_prompt(context, match)
    return True


def _record_bowler_number(match: Match, bowler_id: int, number: int):
    history = match.bowler_recent_numbers.setdefault(bowler_id, [])
    history.append(number)
    match.bowler_recent_numbers[bowler_id] = history[-3:]


def _is_spam(match: Match, bowler_id: int, number: int) -> bool:
    history = match.bowler_recent_numbers.get(bowler_id, [])
    return len(history) >= 2 and history[-1] == number and history[-2] == number


# ==================== GROUP NUMBER INPUT (batter only) ====================

async def handle_group_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called from the GROUP text handler for any plain message.
    Only the batter's number is accepted this way. Returns True if the
    message was consumed as a game move, False otherwise (so other group
    messages / chat are left alone).
    """
    chat_id = update.effective_chat.id
    match = active_matches.get(chat_id)
    if match is None or match.phase != GamePhase.IN_PROGRESS:
        return False

    if match.waiting_on != "batter":
        return False  # not batting time -- ignore, don't spam the group

    user_id = update.effective_user.id
    if user_id != match.current_batter_id:
        return False  # someone else chatting -- ignore silently

    text = (update.message.text or "").strip()
    if not text.isdigit():
        return False
    number = int(text)
    if not (1 <= number <= 6):
        await update.message.reply_text("⚠️ Please send a number between 1 and 6.")
        return True

    await resolve_ball(context, match, number)
    return True


# ==================== BALL RESOLUTION ====================

async def resolve_ball(context: ContextTypes.DEFAULT_TYPE, match: Match, batter_number: int):
    bowler_number = match.pending_bowler_number
    batter = match.players[match.current_batter_id]
    bowler = match.players[match.current_bowler_id]

    match.balls_this_spell += 1
    match.total_balls_bowled += 1
    batter.balls_faced += 1

    if bowler_number == batter_number:
        # WICKET
        match.wickets += 1
        batter.is_out = True
        bowler.wickets_taken += 1
        caption = f"💀 Pressure cracked! <b>{batter.mention}</b> is dismissed. The bowler celebrates."
        await send_clip(context.bot, match.chat_id, config.WICKET_CLIPS, caption)

        if batter.runs_scored == 0:
            # DUCK -- out without scoring, send a second separate clip+caption.
            duck_line = random.choice(config.DUCK_COMMENTARY)
            duck_caption = f"🦆 <b>Duck!</b>\n<i>{batter.mention}</i> scores 0. {duck_line}"
            await send_clip(context.bot, match.chat_id, config.DUCK_CLIPS, duck_caption)

        if match.all_out():
            await end_match(context, match)
            return

        next_batter_id = match.next_batter_index()
        if next_batter_id is None:
            # Only the current bowler is left not-out. Being out as a
            # batter doesn't stop someone from bowling, so any previously
            # dismissed player can take over bowling right now, freeing
            # up the current bowler to come in and bat. The match should
            # only actually end once EVERYONE has batted (all_out()).
            if match.all_out():
                await end_match(context, match)
                return

            last_man_id = match.current_bowler_id
            # Anyone else in the batting order can bowl to them now,
            # regardless of whether that player is already out.
            new_bowler_id = next(
                uid for uid in match.batting_order if uid != last_man_id
            )
            match.current_bowler_id = new_bowler_id
            match.current_batter_id = last_man_id
            match.balls_this_spell = 0

            new_batter = match.players[last_man_id]
            # NOTE: only announce the new batter here -- the new
            # bowler's "now your turn to bowl" prompt is sent by
            # send_next_bowl_prompt() below (with the run-up clip),
            # so we don't repeat it as plain text too.
            await context.bot.send_message(
                chat_id=match.chat_id,
                text=(
                    f"↪ <i>{new_batter.mention}</i> is the last player standing — "
                    f"steps onto the crease!"
                ),
                parse_mode="HTML",
            )
            await send_next_bowl_prompt(context, match)
            return

        match.current_batter_id = next_batter_id
        new_batter = match.players[next_batter_id]

        await context.bot.send_message(
            chat_id=match.chat_id,
            text=f"↪ <i>{new_batter.mention}</i> is the new batter — steps onto the crease.",
            parse_mode="HTML",
        )
    else:
        runs = batter_number
        bonus = ""
        # small chance of a flavour "bonus" event, purely cosmetic, matches
        # the "Bonus runs! Lucky break!" flavour text seen in the reference screenshots
        if random.random() < 0.12:
            bonus = "\n🎯 Bonus runs! Lucky break!"
        runs_before = batter.runs_scored
        match.total_score += runs
        batter.runs_scored += runs
        if runs == 4:
            batter.fours += 1
        elif runs == 6:
            batter.sixes += 1

        commentary = random.choice(config.RUN_COMMENTARY.get(runs, [""]))
        caption = f"🏏 {runs} Run(s)!{bonus}\n<i>{commentary}</i>" if commentary else f"🏏 {runs} Run(s)!{bonus}"
        await send_clip(context.bot, match.chat_id, config.RUNS_CLIPS.get(runs, []), caption)

        # MILESTONE: fires once, the exact ball a batter's score crosses
        # 50 or 100 for the first time this innings (not re-triggered on
        # every ball afterwards since we check the before/after boundary).
        if runs_before < 100 <= batter.runs_scored:
            line = random.choice(config.CENTURY_COMMENTARY)
            milestone_caption = f"💯 <b>{batter.mention}</b> brings up a CENTURY!\n<i>{line}</i>"
            await send_clip(context.bot, match.chat_id, config.CENTURY_CLIPS, milestone_caption)
        elif runs_before < 50 <= batter.runs_scored:
            line = random.choice(config.FIFTY_COMMENTARY)
            milestone_caption = f"🎉 <b>{batter.mention}</b> brings up a FIFTY!\n<i>{line}</i>"
            await send_clip(context.bot, match.chat_id, config.FIFTY_CLIPS, milestone_caption)

    # Rotate bowler if spell is finished (and match not over)
    if match.balls_this_spell >= match.deliveries_per_spell:
        match.balls_this_spell = 0
        next_bowler = match.pick_next_bowler()
        if next_bowler is None:
            # This only happens if literally no one besides the current
            # batter exists in the match at all (e.g. a 1-player match) --
            # being out as a batter no longer excludes someone from
            # bowling, so this should never fire in normal play.
            await end_match(context, match)
            return
        match.current_bowler_id = next_bowler
        # NOTE: no standalone "now your turn to bowl" text here -- that
        # announcement is already made by send_next_bowl_prompt() below,
        # via the run-up clip's caption. Sending it here too was causing
        # the bowl prompt to appear twice for the same ball.

    await send_next_bowl_prompt(context, match)


# ==================== LIVE SCORE COMMAND ====================

async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /score -- shows the current live scorecard for the match in this chat."""
    chat_id = update.effective_chat.id
    match = active_matches.get(chat_id)
    if match is None:
        await update.message.reply_text("⚠️ No match is currently running in this chat.")
        return

    header = (
        f"🏏 <b>SOLO SCORE</b>\n\n"
        f"<b>Score:</b> {match.total_score}/{match.wickets}   "
        f"<b>Overs:</b> {match.overs_str()}   "
        f"<b>Players:</b> {len(match.batting_order)}\n\n"
    )

    rows = []
    for uid in match.batting_order:
        p = match.players[uid]
        rows.append(
            f"<b>{p.mention}</b>\n"
            f"R: {p.runs_scored}  B: {p.balls_faced}  4s: {p.fours}  6s: {p.sixes}  W: {p.wickets_taken}"
        )

    text = header + "\n\n".join(rows)
    await update.message.reply_text(text, parse_mode="HTML")


# ==================== MATCH END ====================

async def end_match(context: ContextTypes.DEFAULT_TYPE, match: Match):
    match.phase = GamePhase.FINISHED

    # MOTM: best combined performer -- runs scored plus wickets taken,
    # weighted so a wicket counts as roughly as valuable as ~20 runs
    # (tweak MOTM_WICKET_WEIGHT in config.py if you want a different balance).
    motm_id = max(
        match.batting_order,
        key=lambda uid: match.players[uid].runs_scored
        + match.players[uid].wickets_taken * config.MOTM_WICKET_WEIGHT,
    )
    motm = match.players[motm_id]

    # Best Batter: most runs (ties broken by fewer balls faced -- faster innings wins)
    batter_id = max(
        match.batting_order,
        key=lambda uid: (match.players[uid].runs_scored, -match.players[uid].balls_faced),
    )
    best_batter = match.players[batter_id]

    # Best Bowler: most wickets (ties broken by fewest runs conceded off their spells --
    # we don't track runs-conceded per bowler yet, so ties just keep batting-order priority)
    bowler_id = max(match.batting_order, key=lambda uid: match.players[uid].wickets_taken)
    best_bowler = match.players[bowler_id]

    # ---- Render and send the image card first ----
    try:
        motm_photo, batter_photo, bowler_photo = await asyncio.gather(
            fetch_profile_photo_bytes(context.bot, motm.user_id),
            fetch_profile_photo_bytes(context.bot, best_batter.user_id),
            fetch_profile_photo_bytes(context.bot, best_bowler.user_id),
        )
        card = await render_motm_card(
            bot=context.bot,
            motm_name=motm.first_name,
            motm_photo_bytes=motm_photo,
            batter_name=best_batter.first_name,
            batter_photo_bytes=batter_photo,
            batter_stats={
                "R": best_batter.runs_scored, "B": best_batter.balls_faced,
                "6s": best_batter.sixes, "4s": best_batter.fours,
            },
            bowler_name=best_bowler.first_name,
            bowler_photo_bytes=bowler_photo,
            bowler_stats={
                "R": best_bowler.runs_scored, "O": f"{best_bowler.balls_faced // 6}.{best_bowler.balls_faced % 6}",
                "W": best_bowler.wickets_taken, "E": "-",
            },
        )
        if card is not None:
            await context.bot.send_photo(chat_id=match.chat_id, photo=InputFile(card, filename=card.name))
    except Exception as e:
        logger.warning("Failed to render/send MOTM card, continuing with text scorecard: %s", e)

    # ---- Text scorecard (sent after the image, as before) ----
    header = (
        "🏆 <b>SOLO FINAL SCORECARD</b>\n\n"
        f"<b>Runs:</b> {match.total_score}   "
        f"<b>Overs:</b> {match.overs_str()}   "
        f"<b>Players:</b> {len(match.batting_order)}\n\n"
    )

    rows = []
    for uid in match.batting_order:
        p = match.players[uid]
        rows.append(
            f"<b>{p.mention}</b>\n"
            f"R: {p.runs_scored}  B: {p.balls_faced}  4s: {p.fours}  6s: {p.sixes}  W: {p.wickets_taken}"
        )

    motm_line = (
        f"\n👑 <b>MOTM:</b> {motm.mention} — "
        f"{motm.runs_scored} runs • {motm.wickets_taken} wicket{'s' if motm.wickets_taken != 1 else ''}"
    )

    text = header + "\n\n".join(rows) + "\n" + motm_line

    reply_markup = None
    if config.LEGACY_PLAYZONE_URL:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏏 AURA PLAYZONE", url=config.LEGACY_PLAYZONE_URL)]
        ])

    await context.bot.send_message(
        chat_id=match.chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup
    )

    for uid in match.batting_order:
        user_active_chat.pop(uid, None)
    active_matches.pop(match.chat_id, None)


# ==================== TIMEOUT NUDGE + AUTO-REMOVE (background task, spawned per-match) ====================

async def check_turn_timeout(context: ContextTypes.DEFAULT_TYPE, match: Match):
    """
    Call periodically for a running match.
    - At TURN_NUDGE_AT_SECONDS (30s): sends a one-time "still thinking" nudge.
    - At TURN_TIMEOUT_SECONDS (60s): removes whoever's turn it is (bowler or
      batter) from the match entirely, and hands the match to whoever's left.
    """
    elapsed = time.time() - match.turn_started_at

    if elapsed >= config.TURN_TIMEOUT_SECONDS:
        await _remove_player_for_timeout(context, match)
        return

    if elapsed >= config.TURN_NUDGE_AT_SECONDS and not match.turn_nudge_sent:
        match.turn_nudge_sent = True
        if match.waiting_on == "bowler":
            name = match.players[match.current_bowler_id].mention
        else:
            name = match.players[match.current_batter_id].mention

        await context.bot.send_message(
            chat_id=match.chat_id,
            text=f"🏏 30 seconds gone.\n<i>{name}</i> still thinking... This is cricket 😭",
            parse_mode="HTML",
        )


async def _remove_player_for_timeout(context: ContextTypes.DEFAULT_TYPE, match: Match):
    """
    Removes whoever's turn it currently is (bowler or batter) for not
    acting within TURN_TIMEOUT_SECONDS, and keeps the match going with
    the players who are left -- mirrors how a wicket is handled, since
    "removed" and "out" both just mean this player is done for the match.
    """
    if match.waiting_on == "bowler":
        removed = match.players[match.current_bowler_id]
    else:
        removed = match.players[match.current_batter_id]

    removed.is_out = True
    removed.removed_for_timeout = True

    await context.bot.send_message(
        chat_id=match.chat_id,
        text=(
            f"⏱️ <b>{removed.mention}</b> didn't respond within "
            f"{config.TURN_TIMEOUT_SECONDS} seconds and has been removed from the match."
        ),
        parse_mode="HTML",
    )

    if match.waiting_on == "bowler":
        # Someone else has to take over bowling right now -- the current
        # batter obviously can't, so this reuses the same eligible-bowler
        # logic as a normal spell rotation.
        match.balls_this_spell = 0
        next_bowler = match.pick_next_bowler()
        if next_bowler is None:
            await end_match(context, match)
            return
        match.current_bowler_id = next_bowler
        match.turn_nudge_sent = False
        await send_next_bowl_prompt(context, match)
    else:
        # Batter removed -- same as being out: bring in the next batter,
        # or end the match if nobody's left to bat.
        if match.all_out():
            await end_match(context, match)
            return
        next_batter_id = match.next_batter_index()
        if next_batter_id is None:
            # Only the current bowler is left not-out (see resolve_ball's
            # identical case for the full reasoning) -- being out doesn't
            # stop someone from bowling, so bring in the current bowler
            # to bat and hand bowling to anyone else still in the match.
            if match.all_out():
                await end_match(context, match)
                return
            last_man_id = match.current_bowler_id
            new_bowler_id = next(
                uid for uid in match.batting_order
                if uid != last_man_id and not match.players[uid].removed_for_timeout
            )
            match.current_bowler_id = new_bowler_id
            match.current_batter_id = last_man_id
            match.balls_this_spell = 0
            match.turn_nudge_sent = False
            new_batter = match.players[last_man_id]
            await context.bot.send_message(
                chat_id=match.chat_id,
                text=f"↪ <i>{new_batter.mention}</i> is the last player standing — steps onto the crease!",
                parse_mode="HTML",
            )
            await send_next_bowl_prompt(context, match)
            return

        match.current_batter_id = next_batter_id
        match.turn_nudge_sent = False
        new_batter = match.players[next_batter_id]
        await context.bot.send_message(
            chat_id=match.chat_id,
            text=f"↪ <i>{new_batter.mention}</i> is the new batter — steps onto the crease.",
            parse_mode="HTML",
        )
        await send_next_bowl_prompt(context, match)


async def match_timeout_task(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Background loop, one per active match, polling for turn timeouts."""
    while True:
        await asyncio.sleep(config.TIMEOUT_POLL_INTERVAL_SECONDS)
        match = active_matches.get(chat_id)
        if match is None or match.phase != GamePhase.IN_PROGRESS:
            return
        try:
            await check_turn_timeout(context, match)
        except Exception as e:
            logger.warning("Error in match_timeout_task for chat %s: %s", chat_id, e)
