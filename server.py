import time
from typing                import Dict, Any
from network.server_socket import TcpServerSocket
from core.player           import InMemoryTeamRepository, IScoreObserver
from core.types            import RequestType, ResponseType


class Server(IScoreObserver):
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.__repo = InMemoryTeamRepository()
        self.__repo.subscribe(self)
        self.__load_backup_state()

        self.__socket = TcpServerSocket(
            host=self.host,
            port=self.port,
            on_receive_callback=self._handle_client_message
        )

    def start(self):
        self.__socket.start_listening()
        self._run_main_loop()

    def stop(self):
        self.__backup_state()
        self.__socket.stop()

    def _run_main_loop(self):
        try:
            while True:
                time.sleep(300)
                self.__backup_state()
        except KeyboardInterrupt:
            self.stop()

    def on_score_updated(self, snapshot: str) -> None:
        self.__socket.broadcast({
            'type': ResponseType.BROADCAST.value,
            'text': snapshot
        })

    def _handle_client_message(self, client_addr, packet: Dict[str, Any]):
        packet_type = packet.get('type')
        if packet_type == RequestType.MATCH_RESULT.value:
            data = packet.get('data')
            ...
        elif packet_type == RequestType.SCORE_QUERY.value:
            board_text = self.__repo.snapshot()
            self.__socket.send_packet(client_addr, {
                'type': ResponseType.SCORE_DATA.value,
                'text': board_text
            })
        ...
    
    def __load_backup_state(self):
        ...

    def __backup_state(self):
        ...