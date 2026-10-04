"""Decides which short information text follows a game, from the number of games played."""
from typing import Optional

LAST_GAME  = "last_game"
FINAL      = "final"
BREAK_HINT = "break_hint"


def info_key(games: int, total: int, break_after: int) -> Optional[str]:
    """Names the text to write after a game, or None when nothing is needed.

    Args:
        games: Games the server has counted for the pair, including the one just finished.
        total: Games in one micro-match.
        break_after: Games between breaks, 0 for no break rule.

    Returns:
        FINAL once the match is over, LAST_GAME one game before, BREAK_HINT one game before a
        break, else None. The last game wins over a break hint.
    """
    if games >= total:
        return FINAL
    if games == total - 1:
        return LAST_GAME
    if break_after and (games + 1) % break_after == 0:
        return BREAK_HINT
    return None
