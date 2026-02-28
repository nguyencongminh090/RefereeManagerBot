import socket
from abc    import ABC, abstractmethod
from typing import Callable, Dict, Any, Optional


class IClientSocket(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def send_packet(self, payload: Dict[str, Any]) -> None: ...


class TcpClientSocket(IClientSocket):
    def __init__(self, host: str, port: int, on_receive_callback: Callable[[Dict], None]):
        self.__host               : str                     = host
        self.__port               : int                     = port
        self.__sock               : Optional[socket.socket] = None
        self.__running            : bool                    = False
        self.__on_receive_callback: Callable[[Dict], None]  = on_receive_callback

    def connect(self) -> None:
        ...

    def disconnect(self) -> None:
        ...

    def _reconnect(self) -> None:
        ...

    def _run_loop(self) -> None:
        while self.__running:
            ...            

    def send_packet(self, payload: Dict[str, Any]) -> None:
        ...