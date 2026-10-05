"""Unit tests of the server's packet handlers, driven with a fake socket and a fake repository."""
import unittest
from typing import Any, Dict, List, Tuple

from domain.ports          import ITeamRepository, MatchRecord, PairScoreChange
from domain.types          import GameResult
from network.messages      import RequestType, ResponseType
from network.ports         import IServerSocket
from serverapp.auth        import AuthHandler, AuthOptions
from serverapp.claims      import ClaimRegistry
from serverapp.link        import ClientLink
from serverapp.queries     import QueryHandlers
from serverapp.results     import ResultHandler, SetScoreHandler
from serverapp.sessions    import SessionRegistry
from serverapp.tables      import TableHandlers
from storage.errors        import DuplicateGameError, UnknownPlayerError

A, B = ("10.0.0.1", 1111), ("10.0.0.2", 2222)
TOKEN = "secret"


class FakeServerSocket(IServerSocket):
    def __init__(self):
        self.sent  : List[Tuple[Tuple[str, int], Dict[str, Any]]] = []
        self.closed: List[Tuple[str, int]] = []

    def start_listening(self) -> None: ...
    def stop(self) -> None: ...
    def broadcast(self, payload: Dict[str, Any]) -> None: ...

    def send_packet(self, client_addr, payload) -> None:
        self.sent.append((client_addr, payload))

    def close_client(self, client_addr) -> None:
        self.closed.append(client_addr)

    def last_data(self) -> Dict[str, Any]:
        return self.sent[-1][1]['data']

    def last_type(self) -> int:
        return self.sent[-1][1]['type']


class FakeRepo(ITeamRepository):
    def __init__(self):
        self.records: List[MatchRecord] = []
        self.changes: List[PairScoreChange] = []
        self.error: Exception = None

    def subscribe(self, observer) -> None: ...
    def unsubscribe(self, observer) -> None: ...
    def notify_all(self) -> None: ...

    def record_match(self, record: MatchRecord) -> int:
        if self.error:
            raise self.error
        self.records.append(record)
        return 7

    def pair_score(self, game_id: int) -> Dict[str, Any]:
        return {"games": 1, "complete": False}

    def set_pair_score(self, change: PairScoreChange) -> Dict[str, Any]:
        if self.error:
            raise self.error
        self.changes.append(change)
        return {"games": 4, "complete": False}

    def roster(self) -> List[Dict[str, Any]]:
        return [{"nickname": "wbca", "entrant_name": "Team A", "role": "main", "active": 1}]

    def snapshot(self) -> str:
        return "A 1 : 0 B"


def _result_packet(**overrides) -> Dict[str, Any]:
    data = {"players": ["wbca", "wbcb"], "scores": [GameResult.WIN.value, GameResult.LOSS.value]}
    return {"type": RequestType.MATCH_RESULT.value, "data": data, "meta": {"match_id": "m1"},
            **overrides}


class AuthHandlerTests(unittest.TestCase):
    def setUp(self):
        self.socket   = FakeServerSocket()
        self.sessions = SessionRegistry(lambda: 0.0, 45, 10)
        options = AuthOptions(TOKEN, "Cup", 15, max_failures=2, lockout_seconds=60,
                              clock=lambda: 0.0)
        self.auth = AuthHandler(self.sessions, ClientLink(self.socket), options)
        self.sessions.connect(A)

    def _auth(self, token: str) -> None:
        self.auth.on_auth(A, {"type": RequestType.AUTH.value,
                              "data": {"token": token, "bot_name": "bot1"}})

    def test_right_token_authenticates_and_answers_auth_ok(self):
        self._auth(TOKEN)
        self.assertTrue(self.sessions.is_authenticated(A))
        self.assertEqual(ResponseType.AUTH_OK.value, self.socket.last_type())
        self.assertEqual("Cup", self.socket.last_data()["tournament"])

    def test_wrong_token_is_refused_and_the_link_closed(self):
        self._auth("nope")
        self.assertFalse(self.sessions.is_authenticated(A))
        self.assertEqual("AUTH_FAILED", self.socket.last_data()["code"])
        self.assertEqual([A], self.socket.closed)

    def test_repeated_failures_lock_the_address_out(self):
        for _ in range(3):
            self._auth("nope")
        self.assertTrue(self.auth.is_locked(A[0]))
        self.assertFalse(self.auth.is_locked(B[0]))


class ResultHandlerTests(unittest.TestCase):
    def setUp(self):
        self.socket   = FakeServerSocket()
        self.repo     = FakeRepo()
        self.sessions = SessionRegistry(lambda: 0.0, 45, 10)
        self.sessions.connect(A)
        self.sessions.authenticate(A, "bot1")
        self.handler = ResultHandler(self.repo, self.sessions, ClientLink(self.socket))

    def test_valid_result_is_recorded_and_acknowledged(self):
        self.handler.on_match_result(A, _result_packet())
        self.assertEqual("bot1", self.repo.records[0].bot_name)
        self.assertEqual(ResponseType.MATCH_ACK.value, self.socket.last_type())
        self.assertFalse(self.socket.last_data()["duplicate"])

    def test_duplicate_is_acknowledged_again(self):
        self.repo.error = DuplicateGameError("dup", 7)
        self.handler.on_match_result(A, _result_packet())
        self.assertEqual(ResponseType.MATCH_ACK.value, self.socket.last_type())
        self.assertTrue(self.socket.last_data()["duplicate"])

    def test_unknown_player_gets_an_error_with_the_match_id(self):
        self.repo.error = UnknownPlayerError("who")
        self.handler.on_match_result(A, _result_packet())
        self.assertEqual("UNKNOWN_PLAYER", self.socket.last_data()["code"])
        self.assertEqual("m1", self.socket.last_data()["match_id"])

    def test_malformed_packet_gets_bad_packet(self):
        self.handler.on_match_result(A, _result_packet(meta={}))
        self.assertEqual("BAD_PACKET", self.socket.last_data()["code"])
        self.assertEqual([], self.repo.records)


class SetScoreHandlerTests(unittest.TestCase):
    def setUp(self):
        self.socket  = FakeServerSocket()
        self.repo    = FakeRepo()
        self.handler = SetScoreHandler(self.repo, ClientLink(self.socket),
                                       lambda name: name == "wbcadmin")

    def _packet(self, sender: str) -> Dict[str, Any]:
        return {"type": RequestType.SET_SCORE.value,
                "data": {"sender": sender, "players": ["wbca", "wbcb"], "scores": [2, 1.5]}}

    def test_admin_changes_the_score(self):
        self.handler.on_set_score(A, self._packet("wbcadmin"))
        self.assertEqual(ResponseType.SCORE_SET.value, self.socket.last_type())
        self.assertEqual("wbcadmin", self.repo.changes[0].actor)

    def test_non_admin_is_refused_and_nothing_changes(self):
        self.handler.on_set_score(A, self._packet("someone"))
        self.assertEqual("NOT_ADMIN", self.socket.last_data()["code"])
        self.assertEqual([], self.repo.changes)


class TableHandlerTests(unittest.TestCase):
    def setUp(self):
        self.socket   = FakeServerSocket()
        self.sessions = SessionRegistry(lambda: 0.0, 45, 10)
        self.claims   = ClaimRegistry()
        for addr, name in ((A, "bot1"), (B, "bot2")):
            self.sessions.connect(addr)
            self.sessions.authenticate(addr, name)
        self.tables = TableHandlers(self.claims, self.sessions, ClientLink(self.socket))

    @staticmethod
    def _packet(table_no: Any) -> Dict[str, Any]:
        return {"type": RequestType.TABLE_CLAIM.value, "data": {"table_no": table_no}}

    def test_first_claim_is_granted_second_bot_is_denied(self):
        self.tables.on_claim(A, self._packet(5))
        self.assertEqual(ResponseType.CLAIM_OK.value, self.socket.last_type())
        self.tables.on_claim(B, self._packet(5))
        self.assertEqual(ResponseType.CLAIM_DENIED.value, self.socket.last_type())
        self.assertEqual("bot1", self.socket.last_data()["held_by"])

    def test_non_numeric_table_is_a_bad_packet(self):
        self.tables.on_claim(A, self._packet("5"))
        self.assertEqual("BAD_PACKET", self.socket.last_data()["code"])

    def test_release_by_a_non_owner_is_an_error(self):
        self.tables.on_claim(A, self._packet(5))
        self.tables.on_release(B, self._packet(5))
        self.assertEqual("NOT_OWNER", self.socket.last_data()["code"])
        self.assertEqual(A, self.claims.owner(5))


class QueryHandlerTests(unittest.TestCase):
    def test_score_and_roster_queries_are_answered(self):
        socket  = FakeServerSocket()
        queries = QueryHandlers(FakeRepo(), ClientLink(socket))
        queries.on_score_query(A, {})
        self.assertEqual("A 1 : 0 B", socket.sent[-1][1]["text"])
        queries.on_roster_query(A, {})
        self.assertEqual([{"name": "wbca", "team": "Team A", "role": "main", "active": True}],
                         socket.last_data()["players"])


if __name__ == "__main__":
    unittest.main()
