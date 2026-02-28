import socket
import threading
from abc              import ABC, abstractmethod
from typing           import Callable, Dict, Any, Tuple, Optional
from network.protocol import PacketProtocol


class IServerSocket(ABC):
    @abstractmethod
    def start_listening(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def send_packet(self, client_addr: Tuple[str, int], payload: Dict[str, Any]) -> None: ...

    @abstractmethod
    def broadcast(self, payload: Dict[str, Any]) -> None: ...


class TcpServerSocket(IServerSocket):
    def __init__(self,
                 host:str,
                 port:int,
                 on_receive_callback: Callable[[Tuple[str, int], Dict], None]):

        self.__host: str = host
        self.__port: int = port
        self.__on_receive_callback: Callable[[Tuple[str, int], Dict], None] = on_receive_callback

        self.__server_sock : Optional[socket.socket]              = None
        self.__running     : bool                                 = False
        self.__clients     : Dict[Tuple[str, int], socket.socket] = {}
        self.__clients_lock: threading.Lock                       = threading.Lock()


    def start_listening(self):
        self.__server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.__server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.__server_sock.bind((self.__host, self.__port))
        self.__server_sock.listen()
        self.__running = True

        acceptor_thread = threading.Thread(target=self._run_acceptor_loop, daemon=True)
        acceptor_thread.start()

    def _run_acceptor_loop(self):
        while self.__running:
            try:
                client_sock, client_addr = self.__server_sock.accept()
                
                with self.__clients_lock:
                    self.__clients[client_addr] = client_sock

                worker_thread = threading.Thread(
                    target = self._handle_single_client, 
                    args   = (client_sock, client_addr),
                    daemon = True
                )
                worker_thread.start()
                
            except Exception:
                if not self.__running:
                    break

    def _handle_single_client(self, client_sock: socket.socket, client_addr: Tuple[str, int]):
        while self.__running:
            try:
                packet = PacketProtocol.receive_packet(client_sock)
                if not packet:
                    break
                
                self.__on_receive_callback(client_addr, packet)
                
            except Exception as e:
                break
                
        self._remove_client(client_addr, client_sock)


    def send_packet(self, client_addr: Tuple[str, int], payload: Dict[str, Any]):
        with self.__clients_lock:
            client_sock = self.__clients.get(client_addr)
            
        if client_sock:
             data_bytes = PacketProtocol.encode(payload)
             try:
                 client_sock.sendall(data_bytes)
             except:
                 self._remove_client(client_addr, client_sock)

    def broadcast(self, payload: Dict[str, Any]):
        data_bytes = PacketProtocol.encode(payload)
        
        with self.__clients_lock:
            for addr, sock in list(self.__clients.items()):
                try:
                    sock.sendall(data_bytes)
                except:
                    self._remove_client(addr, sock)

    def _remove_client(self, addr, sock):
        with self.__clients_lock:
             if addr in self.__clients:
                 del self.__clients[addr]
        try:
             sock.close()
        except: 
            pass
        
    def stop(self): 
        self.__running = False
        if self.__server_sock:
            self.__server_sock.close()