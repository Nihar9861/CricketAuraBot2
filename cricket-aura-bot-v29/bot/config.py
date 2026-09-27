"""
Central configuration: timers, limits, and MEDIA FILE PATHS.

HOW TO ADD YOUR CLIPS (video or GIF):
--------------------------------------
1. Create a folder called `media/` in the project root (same level as bot/).
   It already exists in this project -- just drop files into it.
2. Drop your files in there, e.g.:
     media/bowl_run_up_1.mp4
     media/wicket_1.gif
   Both .mp4 and .gif work -- the bot auto-detects which one it is and
   sends it the right way (video vs animation).
3. Fill in the paths below by UN-COMMENTING the example lines and
   changing the filename to match yours. You can list MULTIPLE clips
   per event -- the bot picks one at random each time so it doesn't
   feel repetitive. Example:
       BOWL_RUNUP_CLIPS = [
           MEDIA_DIR / "bowl_run_up_1.mp4",
           MEDIA_DIR / "bowl_run_up_2.gif",
       ]
4. Leave a list empty [] if you don't have a clip for that event yet --
   the bot will just skip sending video and use text only.

HOW TO ADD BANNER IMAGES (jpg/png, static):
--------------------------------------------
Same media/ folder. Set START_BANNER_IMAGE / SOLO_BANNER_IMAGE at the
bottom of this file to a MEDIA_DIR / "yourfile.jpg" path. Leave as
None to skip (plain text menu instead).
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_DIR = BASE_DIR / "media"

# ---------- Telegram bot identity ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")  # without @

# ---------- "LEGACY PLAYZONE" button shown on the final scorecard ----------
# Put your group / channel invite link here (or set the env var). Leave as
# "" to skip showing the button entirely.
LEGACY_PLAYZONE_URL = os.getenv("LEGACY_PLAYZONE_URL", "")  # e.g. "https://t.me/your_group"

# ---------- MOTM (Man of the Match) calculation ----------
# Combined score = runs_scored + wickets_taken * MOTM_WICKET_WEIGHT.
# 20 means one wicket is worth about as much as scoring 20 runs.
MOTM_WICKET_WEIGHT = 20

# ---------- Lobby settings ----------
LOBBY_DURATION_SECONDS = 120          # 2 minutes, matches screenshots
LOBBY_EXTEND_SECONDS = 30             # /extend 30
MIN_PLAYERS = 3
MAX_PLAYERS = 8
LOBBY_NUDGE_AT_SECONDS_LEFT = [60, 30, 10]  # "1 minute left", "30 seconds gone" etc.

# ---------- Delivery / spell settings ----------
DELIVERY_OPTIONS = [1, 3, 6]          # balls per bowler before rotation
DEFAULT_DELIVERIES_PER_SPELL = 3

# ---------- Match end condition ----------
# No overs limit. Match ends only when every joined player has been
# dismissed once (last player standing has no one left to bowl to,
# so they're auto-retired and the innings ends).

# ---------- Turn timeout ----------
TURN_TIMEOUT_SECONDS = 60             # player is removed from the match if they don't act in time
TURN_NUDGE_AT_SECONDS = 30            # send a "still thinking" nudge in group
TIMEOUT_POLL_INTERVAL_SECONDS = 5     # how often the background task checks for timeouts

# ---------- Game modes ----------
GAME_STYLE_NORMAL = "normal"
GAME_STYLE_SPAM_FREE = "spam_free"

# ---------- Media: bowling run-up clips (sent in bowler's DM before they pick) ----------
BOWL_RUNUP_CLIPS = [
    MEDIA_DIR / "bowling_1.mp4",
]

# ---------- Media: batting stance / facing delivery clips (posted in group) ----------
BAT_FACING_CLIPS = [
    MEDIA_DIR / "batting_1.mp4",
]

# ---------- Media: runs scored clips, keyed by run value ----------
# You can add multiple per key; bot picks randomly. Mix .mp4 and .gif freely.
RUNS_CLIPS = {
    0: [
        # MEDIA_DIR / "dot_ball_1.mp4",
        # MEDIA_DIR / "dot_ball_2.mp4",
    ],
    1: [
        MEDIA_DIR / "run_1a.mp4",
        MEDIA_DIR / "run_1b.mp4",
        # MEDIA_DIR / "run_1c.mp4",
    ],
    2: [
        MEDIA_DIR / "run_2a.mp4",
        MEDIA_DIR / "run_2b.mp4",
        # MEDIA_DIR / "run_2c.mp4",
    ],
    3: [
        MEDIA_DIR / "run_3a.mp4",
        MEDIA_DIR / "run_3b.mp4",
        # MEDIA_DIR / "run_3c.mp4",
    ],
    4: [
        MEDIA_DIR / "run_4a.mp4",
        MEDIA_DIR / "run_4b.mp4",
        # MEDIA_DIR / "run_4c.mp4",
    ],
    5: [
        MEDIA_DIR / "run_5a.mp4",
        MEDIA_DIR / "run_5b.mp4",
        # MEDIA_DIR / "run_5c.mp4",
    ],
    6: [
        MEDIA_DIR / "run_6a.mp4",
        MEDIA_DIR / "run_6b.mp4",
        # MEDIA_DIR / "run_6c.mp4",
    ],
}

# ---------- Media: wicket / dismissal clips ----------
WICKET_CLIPS = [
    MEDIA_DIR / "wicket_1.mp4",
]

# ---------- Media: duck (out for 0 runs) clips -- sent as a SECOND,
# separate message right after the normal wicket clip, only when the
# batter is dismissed without scoring any runs this innings. ----------
DUCK_CLIPS = [
    MEDIA_DIR / "duck_1.mp4",
]

# Random flavour lines shown with the duck clip -- one is picked per duck.
DUCK_COMMENTARY = [
    "Tough day at the office 😬",
    "Didn't even get off the mark!",
    "Gone without troubling the scorers.",
    "Straight back to the pavilion.",
    "That one will sting.",
]

# ---------- Media: milestone clips -- sent as a SEPARATE message right
# after the normal run-scoring clip, the ball a batter's score crosses
# 50 or 100 for the first time this innings. ----------
FIFTY_CLIPS = [
    MEDIA_DIR / "fifty_1.mp4",
]
CENTURY_CLIPS = [
    MEDIA_DIR / "century_1.mp4",
]

FIFTY_COMMENTARY = [
    "Fifty up! Just getting started.",
    "Half-century! Finding his rhythm now.",
    "Brings up a well-deserved fifty!",
    "50 on the board — the innings is building nicely.",
]
CENTURY_COMMENTARY = [
    "A magnificent century! Take a bow.",
    "Three figures! What an innings.",
    "Century! This one will be remembered.",
    "100 up — an absolute masterclass.",
]

# ---------- Commentary lines shown with each run-scoring shot ----------
# One is picked at random per ball so it doesn't feel repetitive.
RUN_COMMENTARY = {
    0: [
        "Dot ball! Tight bowling, no run for the batter.",
        "Defended solidly, no run this time.",
        "Beaten! But no run added.",
        "Good areas from the bowler, batter can't get it away.",
    ],
    1: [
        "Nudged into the gap, easy single.",
        "Quick single, good running between the wickets.",
        "Tucked away for one.",
        "Worked off the pads, they'll take the single.",
    ],
    2: [
        "Driven into the gap, they come back for two!",
        "Good placement, two runs added.",
        "Well timed, the fielder can't cut it off in time.",
        "Two more on the board, steady building.",
    ],
    3: [
        "Excellent running, three runs taken!",
        "Placed it well and hustled through for three.",
        "Fielder chases hard but they'd already crossed twice.",
        "Smart cricket — three runs in the bank.",
    ],
    4: [
        "FOUR! Cracked through the line beautifully.",
        "That's a boundary! Great shot.",
        "Finds the gap and races away for four!",
        "Superb timing — straight to the fence.",
    ],
    5: [
        "Five runs! Overthrow chaos out there.",
        "Rare five — brilliant running plus an extra.",
        "That's an unusual five, but they'll take it!",
    ],
    6: [
        "SIX! That's out of the park!",
        "Maximum! Absolutely smashed.",
        "Into the stands! Huge hit.",
        "SIX runs! What a strike.",
    ],
}

# ---------- Media: match start / innings clips ----------
MATCH_START_CLIPS = [
    # MEDIA_DIR / "match_start.mp4",
]
NEW_BATTER_CLIPS = [
    # MEDIA_DIR / "new_batter.mp4",
]

# ---------- Banner images for /start menu and Solo setup (jpg/png, static) ----------
START_BANNER_IMAGE = MEDIA_DIR / "start_banner.jpeg"
SOLO_BANNER_IMAGE = MEDIA_DIR / "solo_banner.jpeg"
