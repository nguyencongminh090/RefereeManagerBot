"""Wire-protocol constants shared by the client and the server."""
from enum import Enum


class RequestType(Enum):
    """Packet types a client sends to the server."""
    AUTH         = 1
    MATCH_RESULT = 2
    SCORE_QUERY  = 3
    HEARTBEAT    = 4
    ROSTER_QUERY = 5
    TABLE_CLAIM  = 6
    TABLE_RELEASE = 7
    SET_SCORE    = 8


class ResponseType(Enum):
    """Packet types the server sends to clients."""
    AUTH_OK    = 101
    SCORE_DATA = 102
    ERROR      = 103
    BROADCAST  = 104
    ROSTER_DATA = 105
    CLAIM_OK   = 106
    CLAIM_DENIED = 107
    MATCH_ACK  = 108
    SCORE_SET  = 109
