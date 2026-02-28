import struct
import json

class PacketProtocol:
    
    @staticmethod
    def encode(payload: dict) -> bytes:
        data_bytes    = json.dumps(payload).encode('utf-8')
        length_header = struct.pack('>I', len(data_bytes))
        return length_header + data_bytes

    @staticmethod
    def recv_exact(sock, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                return None
            data.extend(packet)
        return bytes(data)

    @staticmethod
    def receive_packet(sock) -> dict:
        raw_msglen = PacketProtocol.recv_exact(sock, 4)
        if not raw_msglen:
            return None
            
        msglen   = struct.unpack('>I', raw_msglen)[0]
        raw_data = PacketProtocol.recv_exact(sock, msglen)
        if not raw_data:
            return None
            
        return json.loads(raw_data.decode('utf-8'))
