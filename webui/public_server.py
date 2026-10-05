"""The audience page: the same HTTP front, with no token, no actions and its own page and scripts."""
from config.process_config import PublicConfig
from webui.assets          import PUBLIC_ASSET_NAMES, PUBLIC_INDEX_NAME
from webui.http_server     import PageSettings, StateSource, WebServer


class PublicServer(WebServer):
    """Serves the read-only audience page; every change request is refused with 405."""

    def __init__(self, config: PublicConfig, state: StateSource) -> None:
        """Binds the listening socket.

        Args:
            config: The `[public]` settings.
            state: Source of the public snapshot (it must hold nothing organizer-side).

        Raises:
            OSError: If the address cannot be bound.
        """
        settings = PageSettings(None, state, config.refresh_seconds, None,
                                PUBLIC_INDEX_NAME, PUBLIC_ASSET_NAMES)
        super().__init__((config.host, config.port), settings, "public page")
