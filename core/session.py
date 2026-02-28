import uuid
from typing                import Tuple
from dataclasses           import dataclass, field
from core.types            import SessionState, GameResult, RequestType
from core.driver           import IDriver
from commands.dispatcher   import CommandDispatcher
from network.client_socket import IClientSocket


@dataclass
class MatchContext:
    p1_name      : str          = ""
    p2_name      : str          = ""
    total_matches: int          = 12
    current_match: int          = 1
    state        : SessionState = SessionState.IN_PROGRESS


class MatchSession:
    def __init__(self, 
                 driver    : IDriver, 
                 dispatcher: CommandDispatcher, 
                 socket    : IClientSocket):
        self._driver     = driver
        self._dispatcher = dispatcher
        self._socket     = socket
        self._context    = MatchContext()

    @property
    def context(self) -> MatchContext:
        return self._context

    def initialize(self) -> None:
        p1, p2 = self._driver.get_players_name()
        if not p1 or not p2:
            self._driver.send_message("Error: Cannot read player names. Please try again.")
            return
        self._context.p1_name = p1
        self._context.p2_name = p2

    def process_message(self, sender: str, text: str) -> None:
        if self._context.state == SessionState.COMPLETED:
            return
        if sender == '+':
            self._handle_result(text)  
        elif text.startswith('!'): 
            match text:
                case '!start':
                    self.initialize()
                case '!leave':
                    self.leave()
                case _:
                    self._dispatcher.dispatch(sender, text)

    def _handle_result(self, system_message: str) -> None:
        if 'player #1 wins' in system_message:
            score_p1, score_p2 = GameResult.WIN, GameResult.LOSS
        elif 'player #2 wins' in system_message:
            score_p1, score_p2 = GameResult.LOSS, GameResult.WIN
        elif 'draw' in system_message:
            score_p1, score_p2 = GameResult.DRAW, GameResult.DRAW
        else:
            return
        
        payload = {
            'type': RequestType.MATCH_RESULT.value,
            'meta': {
                'match_id': str(uuid.uuid4()),
            },
            'data': {
                'players': (self._context.p1_name, self._context.p2_name),
                'scores' : (score_p1.value, score_p2.value),
            }
        }

        self._socket.send_packet(payload)
        self._context.current_match += 1

        if self._context.current_match > self._context.total_matches:
            self.leave()
        
    def leave(self) -> None:
        self._context.state = SessionState.COMPLETED
        self._driver.send_message("bye")
        self._driver.leave_table()