"""Handlers of the table claims: one bot per table."""
from typing import Any, Dict, Optional

from network.messages  import ResponseType
from serverapp.claims  import ClaimRegistry
from serverapp.link    import Addr, ClientLink
from serverapp.sessions import SessionRegistry


class TableHandlers:
    """Answers TABLE_CLAIM and TABLE_RELEASE."""

    def __init__(self, claims: ClaimRegistry, sessions: SessionRegistry, link: ClientLink) -> None:
        self._claims   = claims
        self._sessions = sessions
        self._link     = link

    def on_claim(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Grants the table to the first bot that asks, and denies the others."""
        table_no = self._table_no(addr, packet)
        if table_no is None:
            return
        owner = self._claims.claim(table_no, addr)
        if owner != addr and self._is_same_bot(owner, addr):
            # the bot reconnected before the server dropped its old link: the table stays with the bot
            owner = addr if self._claims.take_over(table_no, owner, addr) else owner
        if owner == addr:
            self._link.reply(addr, ResponseType.CLAIM_OK, table_no=table_no)
        else:
            self._link.reply(addr, ResponseType.CLAIM_DENIED, table_no=table_no,
                             held_by=self._sessions.name_of(owner))

    def on_release(self, addr: Addr, packet: Dict[str, Any]) -> None:
        """Frees a table; only the bot that holds it may do so."""
        table_no = self._table_no(addr, packet)
        if table_no is None:
            return
        if not self._claims.release(table_no, addr):
            self._link.error(addr, "NOT_OWNER", f"table {table_no} is not claimed by this bot")

    def _table_no(self, addr: Addr, packet: Dict[str, Any]) -> Optional[int]:
        table_no = (packet.get('data') or {}).get('table_no')
        if isinstance(table_no, int) and not isinstance(table_no, bool):
            return table_no
        self._link.error(addr, "BAD_PACKET", "data.table_no must be a number")
        return None

    def _is_same_bot(self, first: Addr, second: Addr) -> bool:
        name = self._sessions.name_of(first)
        return name is not None and name == self._sessions.name_of(second)
