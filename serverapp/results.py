"""Handlers of the packets that change scores: a game result and a score correction."""
import logging
from typing import Any, Callable, Dict

from domain.ports            import ITeamRepository, MatchRecord, PairScoreChange
from network.messages        import RequestType, ResponseType
from serverapp.link          import Addr, ClientLink
from serverapp.match_request import (MatchResultRequest, SetScoreRequest, match_id_of,
                                     parse_match_result, parse_set_score)
from serverapp.sessions      import SessionRegistry
from storage.errors          import (DuplicateGameError, MicroMatchFullError, NotFoundError,
                                     PlayerInactiveError, SameEntrantError, StorageError,
                                     UnknownPlayerError, ValidationError)

logger = logging.getLogger(__name__)

# most specific first: UnknownPlayerError is a NotFoundError
_ERROR_CODES = ((UnknownPlayerError, "UNKNOWN_PLAYER"), (PlayerInactiveError, "PLAYER_INACTIVE"),
                (SameEntrantError, "SAME_TEAM"), (MicroMatchFullError, "MICROMATCH_FULL"),
                (ValidationError, "BAD_RESULT"), (NotFoundError, "NOT_FOUND"))
_NO_TABLE_LABEL = "-"
_SET_SCORE      = RequestType.SET_SCORE.value   # marks errors that answer a !set


def _code_of(exc: StorageError) -> str:
    return next((code for cls, code in _ERROR_CODES if isinstance(exc, cls)), "REJECTED")


class ResultHandler:
    """Records the game results the bots report."""

    def __init__(self, repo: ITeamRepository, sessions: SessionRegistry, link: ClientLink) -> None:
        self._repo     = repo
        self._sessions = sessions
        self._link     = link

    def on_match_result(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Validates and stores a MATCH_RESULT, then answers MATCH_ACK or ERROR."""
        try:
            request = parse_match_result(packet)
        except ValueError as exc:
            match_id = match_id_of(packet)
            logger.info("result %s rejected (BAD_PACKET): %s", match_id, exc)
            return self._link.error(addr, "BAD_PACKET", str(exc), match_id=match_id)
        self._record(addr, request)

    def _record(self, addr: Addr, request: MatchResultRequest) -> None:
        bot_name = self._sessions.name_of(addr)
        try:
            game_id = self._repo.record_match(MatchRecord(
                request.players[0], request.results[0], request.players[1], request.results[1],
                match_id=request.match_id, table_no=request.table_no, bot_name=bot_name))
            duplicate = False
            table_label = _NO_TABLE_LABEL if request.table_no is None else request.table_no
            logger.info("result %s accepted from bot '%s': table %s, %s vs %s",
                        request.match_id, bot_name, table_label, *request.players)
        except DuplicateGameError as exc:           # a retry: confirm again, record nothing
            game_id, duplicate = exc.game_id, True
            logger.info("result %s duplicate from bot '%s': confirmed again",
                        request.match_id, bot_name)
        except StorageError as exc:
            code = _code_of(exc)
            logger.info("result %s rejected (%s): %s", request.match_id, code, exc)
            return self._link.error(addr, code, str(exc), match_id=request.match_id)
        self._link.reply(addr, ResponseType.MATCH_ACK, match_id=request.match_id,
                         duplicate=duplicate, **self._repo.pair_score(game_id))


class SetScoreHandler:
    """Applies the score corrections that tournament admins type with `!set`."""

    def __init__(self, repo: ITeamRepository, link: ClientLink,
                 is_admin: Callable[[str], bool]) -> None:
        self._repo     = repo
        self._link     = link
        self._is_admin = is_admin

    def on_set_score(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Validates a SET_SCORE, checks the sender is an admin and applies it."""
        try:
            request = parse_set_score(packet)
        except ValueError as exc:
            logger.info("set-score request rejected (BAD_PACKET): %s", exc)
            return self._link.error(addr, "BAD_PACKET", str(exc), request=_SET_SCORE)
        if not self._is_admin(request.sender):
            logger.warning("set-score request from '%s' refused: not an admin", request.sender)
            return self._link.error(addr, "NOT_ADMIN",
                                    f"'{request.sender}' is not a tournament admin",
                                    request=_SET_SCORE)
        self._apply(addr, request)

    def _apply(self, addr: Addr, request: SetScoreRequest) -> None:
        change = PairScoreChange(*request.players, *request.points, actor=request.sender)
        try:
            score = self._repo.set_pair_score(change)
        except StorageError as exc:
            code = _code_of(exc)
            logger.info("set-score by '%s' rejected (%s): %s", request.sender, code, exc)
            return self._link.error(addr, code, str(exc), request=_SET_SCORE)
        logger.info("score of %s and %s set to %s by '%s'", *request.players, request.points,
                    request.sender)
        self._link.reply(addr, ResponseType.SCORE_SET, **score)
