from enum import Enum


class GameResult(Enum):
    WIN  = 1
    LOSS = 2
    DRAW = 3


class SessionState(Enum):
    IN_PROGRESS = 1
    BREAK_TIME  = 2
    COMPLETED   = 3
    

class PacketType(Enum):
    AUTH_REQUEST    = 1       
    MATCH_RESULT    = 2       
    SCORE_REQUEST   = 3      
    HEARTBEAT       = 4          
    
    AUTH_RESPONSE   = 101    
    SCORE_RESPONSE  = 102   
    ERROR_RESPONSE  = 103   
    BROADCAST_MSG   = 104    