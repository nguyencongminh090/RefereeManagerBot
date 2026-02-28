import time
from typing                import Dict, Any, Optional
from commands.dispatcher   import CommandDispatcher
from network.client_socket import TcpClientSocket
from core.driver           import SeleniumDriver
from core.session          import MatchSession
from core.types            import ResponseType, SessionState


class Client:
    def __init__(self, host: str, port: int):

        self.__driver     = SeleniumDriver()
        self.__socket     = TcpClientSocket(
            host                = host, 
            port                = port, 
            on_receive_callback = self._handle_server_message
        )
        self.__dispatcher = CommandDispatcher(self.__driver, self.__socket)
        self.__session: Optional[MatchSession] = None
        self._setup_commands()

    def _handle_server_message(self, packet: Dict[str, Any]):
        packet_type = packet.get("type")
        
        if packet_type == ResponseType.SCORE_DATA.value:
            self.__driver.send_message(f"{packet.get('text', '')}")
        elif packet_type == ResponseType.BROADCAST.value:
            self.__driver.send_message(f"{packet.get('text')}")

    def _setup_commands(self):
        ...
    
    def start(self):
        self.__socket.connect()
        self.__driver.open_site()
        self.__driver.login(...)
        self.__driver.goto_lobby()

        self._run_main_loop()

    def stop(self):
        self.__socket.disconnect()
        self.__driver.quit()

    def _run_main_loop(self):
        try:
            while True:
                if self.__session is None:
                    inviter = self.__driver.check_for_invitation()
                    if inviter:
                        self.__driver.accept_invitation()
                        self.__session = MatchSession(
                            self.__driver,
                            self.__dispatcher,
                            self.__socket
                        )
                else:
                    new_messages = self.__driver.receive_messages()
                    for sender, text in new_messages:
                        self.__session.process_message(sender, text)
                    if self.__session.context.state == SessionState.COMPLETED:
                        self.__session = None
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.stop()