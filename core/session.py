import uuid
from typing                import Tuple
from core.types            import SessionState, GameResult, PacketType
from core.driver           import Driver
from commands.dispatcher   import CommandDispatcher
from network.client_socket import ClientSocket


class MatchSession:
    def __init__(self, 
                 driver_wrapper    : Driver, 
                 command_dispatcher: CommandDispatcher, 
                 socket_client     : ClientSocket):
        self.driver_wrapper     = driver_wrapper
        self.command_dispatcher = command_dispatcher
        self.socket_client      = socket_client
        self.state              = SessionState.IN_PROGRESS

        self.total_matches      = 12
        self.current_match      = 1

        self.p1_name            = ""
        self.p2_name            = ""

    def get_players_name(self) -> Tuple[str, str]:
        p1_name, p2_name = self.driver_wrapper.get_players_name()
        if not p1_name or not p2_name:
            self.driver_wrapper.send_message("Error: Cannot read player names. Please try again.")
            return
        return p1_name, p2_name

    def initialize_players(self):
        p1_name, p2_name = self.get_players_name()
        if not p1_name or not p2_name:
            return
        self.p1_name = p1_name
        self.p2_name = p2_name

    def process_chat_event(self, sender: str, text: str):
        if self.state == SessionState.COMPLETED:
            return
        if sender == '+':
            self._handle_game_result(text)  
        elif text.startswith('!'): 
            match text:
                case '!start':
                    self.initialize_players()
                case '!leave':
                    self.leave()
                case _:
                    self.command_dispatcher.dispatch(sender, text)

    def _handle_game_result(self, system_message: str):
        if 'player #1 wins' in system_message:
            score_p1, score_p2 = GameResult.WIN, GameResult.LOSS
        elif 'player #2 wins' in system_message:
            score_p1, score_p2 = GameResult.LOSS, GameResult.WIN
        elif 'draw' in system_message:
            score_p1, score_p2 = GameResult.DRAW, GameResult.DRAW
        else:
            return
        
        payload = {
            'type': PacketType.MATCH_RESULT.value,
            'meta': {
                'match_id': str(uuid.uuid4()),
            },
            'data': {
                'players': (self.p1_name, self.p2_name),
                'scores' : (score_p1.value, score_p2.value),
            }
        }

        self.socket_client.send_packet(payload)
        
    def leave(self):
        self.state = SessionState.COMPLETED
        self.driver_wrapper.send_message("bye")