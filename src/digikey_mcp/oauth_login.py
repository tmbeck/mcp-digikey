"""DigiKey authorization_code OAuth flow (`digikey-mcp login`).

DigiKey's auth_code flow is standard OAuth 2.0:

1. Direct the user's browser to `<api_base>/v1/oauth2/authorize` with
   `response_type=code`, `client_id`, `redirect_uri`, and `state`.
2. The user authorizes the app in their DigiKey account.
3. DigiKey redirects back to `redirect_uri` with `?code=...&state=...`.
4. POST the code to `/v1/oauth2/token` with `grant_type=authorization_code`
   to receive an access_token + refresh_token.

The redirect_uri must be **registered with the DigiKey app** at
developer.digikey.com — DigiKey rejects any callback URL not in that
allowlist. The default here (`http://localhost:8765/oauth/callback`) is
what users should register.
"""

from __future__ import annotations

import http.server
import logging
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass

import requests

from digikey_mcp import auth_store

logger = logging.getLogger(__name__)

DEFAULT_CALLBACK_PORT = 8765
DEFAULT_REDIRECT_URI = f"http://localhost:{DEFAULT_CALLBACK_PORT}/oauth/callback"

_SUCCESS_HTML = b"""<!doctype html>
<html><head><title>DigiKey MCP - Login complete</title></head>
<body style="font-family: system-ui; padding: 2rem;">
<h1>Logged in.</h1>
<p>You can close this tab and return to your terminal.</p>
</body></html>"""

_ERROR_HTML_TEMPLATE = """<!doctype html>
<html><head><title>DigiKey MCP - Login failed</title></head>
<body style="font-family: system-ui; padding: 2rem;">
<h1>Login failed</h1>
<pre>{detail}</pre>
<p>Close this tab and check your terminal.</p>
</body></html>"""


@dataclass
class OAuthConfig:
    api_base: str  # e.g. https://api.digikey.com
    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @property
    def authorize_url(self) -> str:
        return f"{self.api_base}/v1/oauth2/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.api_base}/v1/oauth2/token"


def build_authorization_url(cfg: OAuthConfig, state: str) -> str:
    """Construct the DigiKey authorization-code URL the user visits in a browser."""
    qs = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "state": state,
    })
    return f"{cfg.authorize_url}?{qs}"


def exchange_code_for_tokens(cfg: OAuthConfig, code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    resp = requests.post(
        cfg.token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri": cfg.redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot handler that captures ?code=... on the registered path."""

    expected_path: str = "/oauth/callback"

    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != self.expected_path:
            self.send_response(404)
            self.end_headers()
            return
        params = dict(urllib.parse.parse_qsl(parsed.query))
        if "error" in params:
            self._respond_html(
                400, _ERROR_HTML_TEMPLATE.format(detail=params["error"]).encode()
            )
            self.server.received_error = params["error"]  # type: ignore[attr-defined]
            return
        if "code" not in params:
            self._respond_html(400, b"Missing ?code= in callback")
            self.server.received_error = "missing_code"  # type: ignore[attr-defined]
            return
        self.server.received_code = params["code"]  # type: ignore[attr-defined]
        self.server.received_state = params.get("state", "")  # type: ignore[attr-defined]
        self._respond_html(200, _SUCCESS_HTML)

    def log_message(self, fmt, *args):  # noqa: D401 — silence default stderr spam
        logger.debug("callback handler: " + fmt, *args)

    def _respond_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _run_callback_server(host: str, port: int, timeout_sec: int) -> tuple[str, str]:
    """Block until /oauth/callback fires (or timeout/error). Returns (code, state)."""
    server = http.server.HTTPServer((host, port), _CallbackHandler)
    server.received_code = None  # type: ignore[attr-defined]
    server.received_error = None  # type: ignore[attr-defined]
    server.received_state = ""  # type: ignore[attr-defined]
    server.timeout = timeout_sec
    deadline = time.monotonic() + timeout_sec
    try:
        while server.received_code is None and server.received_error is None:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"OAuth callback did not arrive within {timeout_sec}s")
            server.handle_request()
        if server.received_error:
            raise RuntimeError(f"OAuth provider returned error: {server.received_error}")
        return server.received_code, server.received_state  # type: ignore[return-value]
    finally:
        server.server_close()


def run_login(
    cfg: OAuthConfig,
    *,
    host: str = "localhost",
    port: int = DEFAULT_CALLBACK_PORT,
    timeout_sec: int = 300,
    open_browser: bool = True,
) -> auth_store.StoredTokens:
    """Run the full login flow. Returns the freshly-saved StoredTokens."""
    state = secrets.token_urlsafe(16)
    url = build_authorization_url(cfg, state)
    logger.info("Open this URL in a browser to authorize:\n  %s", url)
    if open_browser:
        webbrowser.open(url)
    code, returned_state = _run_callback_server(host, port, timeout_sec)
    if returned_state != state:
        raise RuntimeError("OAuth state mismatch — possible CSRF; aborting.")
    payload = exchange_code_for_tokens(cfg, code)
    now = time.time()
    tokens = auth_store.StoredTokens(
        refresh_token=payload["refresh_token"],
        access_token=payload["access_token"],
        expires_at=now + int(payload.get("expires_in", 300)),
        obtained_at=now,
    )
    auth_store.save(tokens)
    return tokens
