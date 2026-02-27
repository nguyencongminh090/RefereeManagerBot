from typing import Dict, Any


class ServerSocket:
    def __init__(self):
        ...

    def start(self):
        ...

    def stop(self):
        ...

    def send_packet(self, payload: Dict[str, Any]):
        ...