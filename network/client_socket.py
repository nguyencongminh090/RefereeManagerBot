import socket
from typing import Callable, Dict, Any, Optional


class ClientSocket:
    def __init__(self, host: str, port: int, on_receive_callback: Callable[[Dict], None]):
        self.__host               : str                     = host
        self.__port               : int                     = port
        self.__sock               : Optional[socket.socket] = None
        self.__running            : bool                    = False
        self.__on_receive_callback: Callable[[Dict], None]  = on_receive_callback

    def connect(self):
        ...

    def disconnect(self):
        ...

    def _reconnect(self):
        ...

    def _run_loop(self):
        while self.__running:
            ...            

    def send_packet(self, payload: Dict[str, Any]):
        ...