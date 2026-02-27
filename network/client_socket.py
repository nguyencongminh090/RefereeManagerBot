from typing import Dict, Any


class ClientSocket:
    def __init__(self):
        ...

    def connect(self):
        ...

    def reconnect(self):
        ...

    def send_packet(self, payload: Dict[str, Any]):
        ...