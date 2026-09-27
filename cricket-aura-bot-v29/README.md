# Cricket Aura Bot — Solo Mode

A Telegram group cricket game: 1-6 number guessing, bowler vs batter,
same number = OUT, no overs limit — plays until everyone's out.

## How it works

1. `/start` in a group → pick **Solo** mode.
2. Choose deliveries-per-spell (1/3/6 balls) and game style (Normal / Spam-Free).
3. Players `/joingame` (min 3, max 8). Lobby auto-closes after 2 minutes,
   or admins can `/extend 30` / `/forcestart`.
4. Match starts: random batter + bowler assigned.
5. Each ball:
   - **Bowler** gets a **DELIVER BALL** button in the group → opens bot DM → sends 1-6 **in DM**.
   - **Batter** then replies with a number **directly in the group chat** (no DM step for batting).
   - Same number → batter is OUT.
   - Different → runs = bowler's number.
6. Bowler rotates every N balls (deliveries per spell).
7. Match ends when every player has been dismissed. Final scorecard posted.

Once a player has opened the bot in DM one time (ever), the bot remembers
them and won't ask them to "start the bot in PM" again in future matches.

## Setup (local testing in VS Code)

1. **Create your bot**: talk to [@BotFather](https://t.me/BotFather) on Telegram,
   `/newbot`, copy the token it gives you.

2. **Clone/open this folder in VS Code.**

3. **Create a virtual environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate      # Windows: venv\Scripts\activate
   ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Configure your `.env`**:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and fill in:
   ```
   BOT_TOKEN=your_token_from_botfather
   BOT_USERNAME=YourBotUsername   # no @, must match your bot's actual username
   ```

6. **Add your video clips, GIFs, and banners** (optional but recommended):
   - Drop `.mp4` / `.gif` files (and `.jpg`/`.png` banners) into the `media/` folder.
   - Open `bot/config.py`, find the relevant list (e.g. `BOWL_RUNUP_CLIPS`,
     `RUNS_CLIPS[4]` for boundaries, `WICKET_CLIPS`, `START_BANNER_IMAGE`, etc.)
     and un-comment the example line, changing the filename to match yours:
     ```python
     BOWL_RUNUP_CLIPS = [
         MEDIA_DIR / "bowl_run_up_1.mp4",
         MEDIA_DIR / "bowl_run_up_2.gif",
     ]
     ```
   - Both `.mp4` and `.gif` work in the same list — the bot auto-detects
     which one it is and sends it correctly (video vs animation).
   - `START_BANNER_IMAGE` and `SOLO_BANNER_IMAGE` take a single jpg/png
     path (not a list) — these are the cover images shown on the
     `/start` mode-select screen and the Solo setup screens.
   - Leave any list empty `[]` or a banner as `None` to skip it —
     the bot just uses text instead, nothing breaks.

7. **Run it**:
   ```bash
   python -m bot.main
   ```

8. **Add the bot to a Telegram group**, make sure it can send messages
   (and ideally is an admin so it can read all messages if you later
   enable group privacy mode restrictions), then run `/start` in the group.

9. **IMPORTANT**: since only the **bowler** interacts via DM (the batter
   replies in the group), every player who might bowl should open the
   bot in **DM and press Start** at least once before the match,
   otherwise the bot can't message them privately (Telegram restriction,
   not a bug — matches the "Could not DM X. Please start the bot in PM!"
   warning you'll see in the group if someone forgets). Once a player
   has done this one time, the bot remembers them permanently and won't
   ask again in future matches.

## Deploying to Railway

1. Push this project to a GitHub repo (`.env` is gitignored — don't commit it).
2. On [Railway](https://railway.app), **New Project → Deploy from GitHub repo**.
3. Select this repo.
4. In Railway's **Variables** tab, add:
   - `BOT_TOKEN`
   - `BOT_USERNAME`
5. Railway will detect `railway.json` / `Procfile` and run
   `python -m bot.main` automatically as a worker (no web port needed —
   this bot uses polling, not webhooks).
6. Check the **Deployments → Logs** tab — you should see
   `Cricket Aura bot starting (polling mode)...`

   If you later add video clips, remember Railway's filesystem is
   ephemeral on redeploy — commit your `media/` files to the repo
   (adjust `.gitignore` if needed) so they persist across deploys.

## Project structure

```
cricket-bot/
├── bot/
│   ├── config.py         # all settings + your media file paths go here
│   ├── state.py          # Lobby / Match / Player data models
│   ├── media.py          # random clip picker + sender
│   ├── lobby.py          # /start, /joingame, /leavegame, /extend, /forcestart
│   ├── match_engine.py   # core game loop: deep-link DM flow, scoring, wickets
│   └── main.py           # entry point, registers all handlers
├── media/                # put your .mp4 clips here
├── requirements.txt
├── .env.example
├── Procfile              # Railway process definition
├── railway.json          # Railway build/deploy config
└── README.md
```

## Known limitations

- **In-memory state**: if the bot restarts, any match in progress is lost
  unless you'd already run `/endgame` → **End & Get Backup** and saved the
  JSON file — `/restore` (attach or reply to that file) brings it back.
- **One match per group at a time.**
- Team mode and Auction mode buttons exist but reply "coming soon" —
  not built yet, as requested for this first version.
- Turn timeout: if a player doesn't act within `TURN_TIMEOUT_SECONDS`
  (60s by default), they're automatically removed from the match and
  the game continues with whoever's left. A "still thinking" nudge is
  sent at `TURN_NUDGE_AT_SECONDS` (30s) first.
