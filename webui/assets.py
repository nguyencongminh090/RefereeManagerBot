"""The dashboard's page, stylesheet and script, read once from `webui/static/`."""
from dataclasses import dataclass
from pathlib     import Path
from typing      import Dict, Optional, Tuple

STATIC_DIR = Path(__file__).parent / "static"
INDEX_NAME = "index.html"
ASSET_NAMES = ("dashboard.css", "common.js", "dashboard.js")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css" : "text/css; charset=utf-8",
    ".js"  : "text/javascript; charset=utf-8",
}


@dataclass(frozen=True)
class Asset:
    """One static file ready to send.

    Attributes:
        body: File content.
        content_type: Value for the Content-Type header.
    """
    body        : bytes
    content_type: str


def _read_all() -> Dict[str, Asset]:
    names: Tuple[str, ...] = (INDEX_NAME,) + ASSET_NAMES
    return {name: Asset((STATIC_DIR / name).read_bytes(), _CONTENT_TYPES[Path(name).suffix])
            for name in names}


_ASSETS = _read_all()


def load_asset(name: str) -> Optional[Asset]:
    """Returns the named file, or None when it is not one of the dashboard's files.

    Only the fixed names are served, so a request path can never reach another file.
    """
    return _ASSETS.get(name)
