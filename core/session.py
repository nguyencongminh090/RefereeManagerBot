from typing                import Tuple
from core.types            import SessionState, GameResult
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

    def start_session(self):
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
        elif text.startswith('/'): 
            self.command_dispatcher.dispatch(sender, text)

    def _handle_game_result(self, system_message: str):
        result      = None
        winner      = None
        if 'player #1 wins' in system_message:
            result      = GameResult.WIN
            winner      = self.p1_name
        elif 'player #2 wins' in system_message:
            result      = GameResult.WIN
            winner      = self.p2_name
        elif 'draw' in system_message:
            result = GameResult.DRAW
        else:
            return
        # TODO: Update score
        
    def leave(self):
        self.state = SessionState.COMPLETED
        self.driver_wrapper.send_message("bye")