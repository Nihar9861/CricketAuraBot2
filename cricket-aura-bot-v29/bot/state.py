"""
In-memory game state models.

One Lobby/Match exists per Telegram group (keyed by chat_id).
Everything lives in RAM for now (simple + fast for testing).
If the bot restarts, active games are lost — fine for v1.
"""

import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto


class GamePhase(Enum):
    LOBBY = auto()          # waiting for /joingame
    IN_PROGRESS = auto()    # match running
    FINISHED = auto()       # match over


@dataclass
class Player:
    user_id: int
    first_name: str
    username: str | None = None
    is_out: bool = False
    removed_for_timeout: bool = False
    runs_scored: int = 0
    balls_faced: int = 0
    fours: int = 0
    sixes: int = 0
    wickets_taken: int = 0

    @property
    def mention(self) -> str:
        # HTML mention, works even if user has no @username
        return f'<a href="tg://user?id={self.user_id}">{self.first_name}</a>'


@dataclass
class Lobby:
    chat_id: int
    created_by: int
    deliveries_per_spell: int = 3
    game_style: str = "normal"
    players: dict[int, Player] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    deadline: float = 0.0
    message_id: int | None = None   # lobby status message, for editing
    closed: bool = False

    def add_player(self, user_id: int, first_name: str, username: str | None) -> bool:
        if user_id in self.players:
            return False
        self.players[user_id] = Player(user_id=user_id, first_name=first_name, username=username)
        return True

    def remove_player(self, user_id: int) -> bool:
        if user_id in self.players:
            del self.players[user_id]
            return True
        return False

    @property
    def player_count(self) -> int:
        return len(self.players)

    def seconds_left(self) -> int:
        return max(0, int(self.deadline - time.time()))


@dataclass
class Match:
    chat_id: int
    deliveries_per_spell: int
    game_style: str
    batting_order: list[int]            # user_ids in batting order
    players: dict[int, Player]          # user_id -> Player
    phase: GamePhase = GamePhase.IN_PROGRESS

    current_batter_id: int | None = None
    current_bowler_id: int | None = None
    bowler_queue: list[int] = field(default_factory=list)   # rotation pool of non-batting players

    balls_this_spell: int = 0
    total_score: int = 0
    wickets: int = 0
    total_balls_bowled: int = 0   # running count across the whole match, for the overs display

    # tracks last N numbers a bowler used, for Spam-Free mode enforcement
    bowler_recent_numbers: dict[int, list[int]] = field(default_factory=dict)

    turn_started_at: float = field(default_factory=time.time)
    turn_nudge_sent: bool = False   # avoid spamming the 30s "still thinking" nudge every poll
    waiting_on: str = "bowler"   # "bowler" or "batter" -- whose input we need next
    pending_bowler_number: int | None = None

    def overs_str(self) -> str:
        completed = self.total_balls_bowled // 6
        rem = self.total_balls_bowled % 6
        return f"{completed}.{rem}"

    def next_batter_index(self) -> int | None:
        """
        Returns the user_id of the next player who should come in to bat:
        not out, not the player who just got out (current_batter_id), and
        crucially NOT the player currently bowling -- a player can never
        bat and bowl to themselves at the same time. Returns None if no
        such player exists (i.e. only the current bowler is left not out --
        match should end with them retired not out).
        """
        for uid in self.batting_order:
            p = self.players[uid]
            if not p.is_out and uid != self.current_batter_id and uid != self.current_bowler_id:
                return uid
        return None

    def all_out(self) -> bool:
        return all(p.is_out for p in self.players.values())

    def pick_next_bowler(self) -> int | None:
        """
        Rotate to the next bowler who isn't the current batter, in a fixed
        alternating order (NOT re-shuffled each time the queue empties).
        Being out as a BATTER does not stop someone from bowling -- "out"
        only ends a player's innings, not their ability to bowl -- so the
        only person ever excluded here is whoever is currently batting
        (a player can't bowl to themselves). Removed-for-timeout players
        are the one exception: they're gone from the match entirely.
        The order is anchored to batting_order so that e.g. with 3 players
        {A, B, C}, once A is batting, B and C strictly alternate B, C, B, C...
        until A is out; when B starts batting, it's C, A, C, A...; etc.
        """
        eligible = [uid for uid in self.batting_order
                    if uid != self.current_batter_id and not self.players[uid].removed_for_timeout]
        if not eligible:
            return None

        # Rebuild the queue in a fixed order anchored to batting_order,
        # starting right after whoever bowled last, whenever it runs out
        # or contains someone no longer eligible.
        self.bowler_queue = [uid for uid in self.bowler_queue if uid in eligible]
        if not self.bowler_queue:
            if self.current_bowler_id in self.batting_order:
                start = self.batting_order.index(self.current_bowler_id)
                ordered = self.batting_order[start + 1:] + self.batting_order[:start + 1]
            else:
                ordered = self.batting_order
            self.bowler_queue = [uid for uid in ordered if uid in eligible]

        return self.bowler_queue.pop(0)

    # ---------- Backup / restore (JSON) ----------

    def to_backup_dict(self) -> dict:
        """Full snapshot of this match, restorable via Match.from_backup_dict."""
        return {
            "chat_id": self.chat_id,
            "deliveries_per_spell": self.deliveries_per_spell,
            "game_style": self.game_style,
            "batting_order": self.batting_order,
            "players": {
                str(uid): {
                    "user_id": p.user_id,
                    "first_name": p.first_name,
                    "username": p.username,
                    "is_out": p.is_out,
                    "removed_for_timeout": p.removed_for_timeout,
                    "runs_scored": p.runs_scored,
                    "balls_faced": p.balls_faced,
                    "fours": p.fours,
                    "sixes": p.sixes,
                    "wickets_taken": p.wickets_taken,
                }
                for uid, p in self.players.items()
            },
            "phase": self.phase.name,
            "current_batter_id": self.current_batter_id,
            "current_bowler_id": self.current_bowler_id,
            "bowler_queue": self.bowler_queue,
            "balls_this_spell": self.balls_this_spell,
            "total_score": self.total_score,
            "wickets": self.wickets,
            "total_balls_bowled": self.total_balls_bowled,
            "bowler_recent_numbers": {str(k): v for k, v in self.bowler_recent_numbers.items()},
            "waiting_on": self.waiting_on,
            "pending_bowler_number": self.pending_bowler_number,
        }

    @staticmethod
    def from_backup_dict(data: dict) -> "Match":
        players = {}
        for uid_str, pdata in data["players"].items():
            uid = int(uid_str)
            players[uid] = Player(
                user_id=pdata["user_id"],
                first_name=pdata["first_name"],
                username=pdata.get("username"),
                is_out=pdata.get("is_out", False),
                removed_for_timeout=pdata.get("removed_for_timeout", False),
                runs_scored=pdata.get("runs_scored", 0),
                balls_faced=pdata.get("balls_faced", 0),
                fours=pdata.get("fours", 0),
                sixes=pdata.get("sixes", 0),
                wickets_taken=pdata.get("wickets_taken", 0),
            )

        match = Match(
            chat_id=data["chat_id"],
            deliveries_per_spell=data["deliveries_per_spell"],
            game_style=data["game_style"],
            batting_order=data["batting_order"],
            players=players,
            phase=GamePhase[data.get("phase", "IN_PROGRESS")],
            current_batter_id=data.get("current_batter_id"),
            current_bowler_id=data.get("current_bowler_id"),
            bowler_queue=data.get("bowler_queue", []),
            balls_this_spell=data.get("balls_this_spell", 0),
            total_score=data.get("total_score", 0),
            wickets=data.get("wickets", 0),
            total_balls_bowled=data.get("total_balls_bowled", 0),
            bowler_recent_numbers={int(k): v for k, v in data.get("bowler_recent_numbers", {}).items()},
            waiting_on=data.get("waiting_on", "bowler"),
            pending_bowler_number=data.get("pending_bowler_number"),
        )
        return match


# ---------- Global registries ----------
# chat_id -> Lobby
active_lobbies: dict[int, Lobby] = {}

# chat_id -> Match
active_matches: dict[int, Match] = {}

# user_id -> chat_id  (so we know, when a DM arrives, which group match it belongs to)
user_active_chat: dict[int, int] = {}

# user_ids who have successfully opened the bot in DM at least once
# (so we stop nagging them with "please start the bot in PM" every single time)
known_dm_users: set[int] = set()
