"""Builds the JSON-ready snapshot the dashboard page shows; reads only, never writes."""
from dataclasses import dataclass
from datetime    import datetime, timezone
from typing      import Any, Dict, List, Optional

from domain.ports   import IBotStatusSource
from domain.types   import GameResult, Scoring
from storage        import StandingRow, TournamentStore
from storage.models import RankingRules

_DRAW_TEXT     = "draw"
_P1_WINS_TEXT  = "p1 wins"
_P2_WINS_TEXT  = "p2 wins"
_AUDIT_FIELDS  = ("at", "actor", "action", "entity", "entity_id")


@dataclass(frozen=True)
class DashboardOptions:
    """How the snapshot is scored and how much of each list it carries.

    Attributes:
        scoring: Points per game.
        ranking: Standings order; None uses the store defaults.
        recent_games: Rows in the recent-games list.
        audit_entries: Rows in the latest-changes list.
        can_edit: Whether the page may offer the edit actions.
    """
    scoring      : Scoring
    ranking      : Optional[RankingRules]
    recent_games : int
    audit_entries: int
    can_edit     : bool


class DashboardState:
    """Collects standings, games, bots, problems and audit entries of one tournament."""

    def __init__(self, store: TournamentStore, tournament_id: int, bots: IBotStatusSource,
                 options: DashboardOptions) -> None:
        self._store         = store
        self._tournament_id = tournament_id
        self._bots          = bots
        self._options       = options

    def snapshot(self) -> Dict[str, Any]:
        """Returns the current state as plain dicts, lists, numbers and strings."""
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tournament"  : self._tournament(),
            "standings"   : self._standings(),
            "recent_games": self._recent_games(),
            "bots"        : self._bot_rows(),
            "can_edit"    : self._options.can_edit,
            "entrants"    : self._entrants(),
            "sudden_death": self._sudden_death(),
            "problems"    : self._store.validate(self._tournament_id),
            "audit"       : self._audit(),
        }

    def _tournament(self) -> Dict[str, Any]:
        row = self._store.get_tournament(self._tournament_id)
        return {"name": row["name"], "format": row["format"]}

    def _standings(self) -> List[Dict[str, Any]]:
        rows = self._store.standings(self._tournament_id, self._options.scoring,
                                     self._options.ranking)
        return [standing_row(row) for row in rows]

    def _recent_games(self) -> List[Dict[str, Any]]:
        games = self._store.list_games(self._tournament_id, include_voided=True)
        newest_first = reversed(games[-self._options.recent_games:])
        return [_game_row(game) for game in newest_first]

    def _entrants(self) -> List[Dict[str, Any]]:
        return [{"id": e["id"], "name": e["name"]}
                for e in self._store.list_entrants(self._tournament_id)]

    def _sudden_death(self) -> List[Dict[str, Any]]:
        ids = {e["name"]: e["id"] for e in self._entrants()}
        return [{"winner": d["winner"], "loser": d["loser"], "winner_id": ids[d["winner"]],
                 "loser_id": ids[d["loser"]]}
                for d in self._store.list_sudden_death(self._tournament_id)]

    def _bot_rows(self) -> List[Dict[str, Any]]:
        return [{"name": bot.name, "address": bot.address, "tables": list(bot.tables),
                 "idle_seconds": bot.idle_seconds} for bot in self._bots.bots()]

    def _audit(self) -> List[Dict[str, Any]]:
        entries = self._store.audit_log(self._options.audit_entries)
        return [{key: entry[key] for key in _AUDIT_FIELDS} for entry in entries]


def standing_row(row: StandingRow) -> Dict[str, Any]:
    return {"rank": row.rank, "tied": row.tied, "name": row.name, "country": row.country,
            "games": row.games, "wins": row.wins, "draws": row.draws, "losses": row.losses,
            "points": row.points, "match_points": row.match_points}


def _game_row(game: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": game["id"], "at": game["played_at"], "table_no": game["table_no"],
            "bot": game["bot_name"], "p1": game["p1"], "p2": game["p2"],
            "result": result_text(game["p1_result"]), "voided": bool(game["voided"])}


def result_text(p1_result: int) -> str:
    if p1_result == GameResult.DRAW.value:
        return _DRAW_TEXT
    return _P1_WINS_TEXT if p1_result == GameResult.WIN.value else _P2_WINS_TEXT
