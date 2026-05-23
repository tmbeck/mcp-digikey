"""Persistence for DigiKey OAuth user tokens.

Stores authorization_code-flow tokens (refresh + access) as plain JSON in the
platform user-config directory with mode 0600 on Unix. The contents are
secrets — the file is protected at the filesystem-permission level only.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_config_path

logger = logging.getLogger(__name__)

APP_NAME = "digikey-mcp"
TOKEN_FILENAME = "tokens.json"


@dataclass
class StoredTokens:
    refresh_token: str
    access_token: str
    expires_at: float  # unix epoch seconds when the access_token expires
    obtained_at: float  # unix epoch seconds when these tokens were issued

    def is_access_token_expired(self, skew_sec: int = 60) -> bool:
        return time.time() >= (self.expires_at - skew_sec)


def token_path() -> Path:
    """Return the file path where tokens are stored.

    Overridable via DIGIKEY_MCP_TOKENS_PATH for testing or non-standard layouts.
    """
    override = os.getenv("DIGIKEY_MCP_TOKENS_PATH")
    if override:
        return Path(override).expanduser()
    return user_config_path(APP_NAME, appauthor=False, ensure_exists=False) / TOKEN_FILENAME


def load() -> StoredTokens | None:
    """Read tokens from disk, or None if absent/corrupt."""
    path = token_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return StoredTokens(**data)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.warning("Corrupt tokens file at %s: %s — ignoring", path, exc)
        return None


def save(tokens: StoredTokens) -> Path:
    """Write tokens to disk, creating the parent directory if needed.

    Sets mode 0600 on the file on Unix-like systems. Windows lacks chmod
    semantics here; the file inherits NTFS ACLs from the user-config dir.
    """
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same dir, then rename — avoids a window
    # where the file exists with default (world-readable) permissions.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(tokens), indent=2))
    if os.name == "posix":
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(path)
    return path


def delete() -> bool:
    """Remove the tokens file. Returns True if a file was actually deleted."""
    path = token_path()
    if path.exists():
        path.unlink()
        return True
    return False
