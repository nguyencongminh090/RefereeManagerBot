import time
from typing                import Dict, Any
from network.server_socket import ServerSocket
from core.player           import TeamManager
from core.types            import PacketType


class Server:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.__team_manager = TeamManager()
        self.__load_backup_state()

        self.__socket = ServerSocket(
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

    def _handle_client_message(self, client_addr, packet: Dict[str, Any]):
        packet_type = packet.get('type')
        if packet_type == PacketType.MATCH_RESULT.value:
            data = packet.get('data')
            ...
        elif packet_type == PacketType.SCORE_REQUEST.value:
            board_text = self.__team_manager.notify()
            self.__socket.send_packet(client_addr, {
                'type': PacketType.SCORE_RESPONSE.value,
                'text': board_text
            })
        ...
    
    def __load_backup_state(self):
        ...

    def __backup_state(self):
        ...


    