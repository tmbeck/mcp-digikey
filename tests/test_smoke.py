"""Smoke tests: package imports, tools register, env parser sane.

These intentionally don't hit the network — they protect against the
'forgot to register a tool' / 'broke an import' class of regression.
"""

import asyncio

import pytest

EXPECTED_TOOLS = {
    "keyword_search",
    "product_details",
    "search_manufacturers",
    "search_categories",
    "get_category_by_id",
    "search_product_substitutions",
    "get_alternate_packaging",
    "get_product_associations",
    "get_recommended_products",
    "get_product_media",
    "get_product_pricing",
    "get_pricing_by_quantity",
    "get_digi_reel_pricing",
}


def test_package_imports():
    import digikey_mcp
    import digikey_mcp.server  # noqa: F401

    assert digikey_mcp.__version__


def test_all_tools_register():
    from digikey_mcp.server import mcp

    tools = asyncio.run(mcp.get_tools())
    assert set(tools) == EXPECTED_TOOLS


def test_keyword_search_schema_carries_constraints():
    from digikey_mcp.server import mcp

    tools = asyncio.run(mcp.get_tools())
    schema = tools["keyword_search"].parameters
    limit = schema["properties"]["limit"]
    assert limit["minimum"] == 1
    assert limit["maximum"] == 50
    assert limit["default"] == 5


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
    ],
)
def test_env_bool_parses_common_forms(monkeypatch, value, expected):
    from digikey_mcp.server import _env_bool

    monkeypatch.setenv("X_TEST_FLAG", value)
    assert _env_bool("X_TEST_FLAG", default=not expected) is expected


def test_env_bool_default_when_unset(monkeypatch):
    from digikey_mcp.server import _env_bool

    monkeypatch.delenv("X_TEST_FLAG", raising=False)
    assert _env_bool("X_TEST_FLAG", default=True) is True
    assert _env_bool("X_TEST_FLAG", default=False) is False


def test_env_bool_rejects_garbage(monkeypatch):
    from digikey_mcp.server import _env_bool

    monkeypatch.setenv("X_TEST_FLAG", "maybe")
    with pytest.raises(ValueError, match="boolean-like"):
        _env_bool("X_TEST_FLAG", default=False)


def test_qp_quotes_path_special_chars():
    from digikey_mcp.server import _qp

    # Slash is the path separator — manufacturer PNs containing it must be encoded.
    assert _qp("AB/CD") == "AB%2FCD"
    # Query and fragment markers must not escape the segment.
    assert _qp("foo?bar=baz") == "foo%3Fbar%3Dbaz"
    assert _qp("foo#frag") == "foo%23frag"
    # Path-traversal attempts get encoded too.
    assert _qp("../admin") == "..%2Fadmin"
    # Numbers stringify and pass through cleanly.
    assert _qp(123) == "123"


def test_retry_adapter_configured():
    from digikey_mcp.server import _build_retry_adapter

    adapter = _build_retry_adapter()
    retry = adapter.max_retries
    assert retry.total == 3
    assert 429 in retry.status_forcelist
    assert 503 in retry.status_forcelist


def test_search_options_validator_accepts_known():
    from digikey_mcp.server import KEYWORD_SEARCH_OPTIONS, _parse_search_options

    assert _parse_search_options(
        "InStock,RohsCompliant", KEYWORD_SEARCH_OPTIONS, "search_options"
    ) == ["InStock", "RohsCompliant"]


def test_search_options_validator_rejects_unknown():
    from digikey_mcp.server import KEYWORD_SEARCH_OPTIONS, _parse_search_options

    with pytest.raises(ValueError, match="RoHSCompliant"):
        # Classic capitalization-trap: RoHSCompliant is invalid here;
        # the correct value for keyword_search is RohsCompliant.
        _parse_search_options(
            "InStock,RoHSCompliant", KEYWORD_SEARCH_OPTIONS, "search_options"
        )


def test_search_options_validator_handles_none_and_empty():
    from digikey_mcp.server import KEYWORD_SEARCH_OPTIONS, _parse_search_options

    assert _parse_search_options(None, KEYWORD_SEARCH_OPTIONS, "search_options") == []
    assert _parse_search_options("", KEYWORD_SEARCH_OPTIONS, "search_options") == []
    assert _parse_search_options("  ,  ", KEYWORD_SEARCH_OPTIONS, "search_options") == []


def test_arg_parser_version_flag(capsys):
    from digikey_mcp import __version__
    from digikey_mcp.server import _build_arg_parser

    # argparse's action="version" prints then SystemExit(0)s.
    with pytest.raises(SystemExit) as excinfo:
        _build_arg_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in (captured.out + captured.err)


def test_arg_parser_recognizes_check_credentials():
    from digikey_mcp.server import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--check-credentials"])
    assert args.check_credentials is True

    args = parser.parse_args([])
    assert args.check_credentials is False


def test_arg_parser_transport_defaults_and_overrides(monkeypatch):
    from digikey_mcp.server import _build_arg_parser

    monkeypatch.delenv("DIGIKEY_MCP_TRANSPORT", raising=False)
    args = _build_arg_parser().parse_args([])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 8000

    args = _build_arg_parser().parse_args(
        ["--transport", "http", "--host", "0.0.0.0", "--port", "9999"]
    )
    assert args.transport == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9999


def test_arg_parser_transport_env_override(monkeypatch):
    monkeypatch.setenv("DIGIKEY_MCP_TRANSPORT", "http")
    monkeypatch.setenv("DIGIKEY_MCP_PORT", "5555")
    from digikey_mcp.server import _build_arg_parser

    args = _build_arg_parser().parse_args([])
    assert args.transport == "http"
    assert args.port == 5555


def test_recommended_products_has_different_enum():
    from digikey_mcp.server import KEYWORD_SEARCH_OPTIONS, RECOMMENDED_PRODUCTS_OPTIONS

    # The endpoints share a couple of values but the enums genuinely differ.
    assert "LeadFree" in RECOMMENDED_PRODUCTS_OPTIONS
    assert "LeadFree" not in KEYWORD_SEARCH_OPTIONS
    assert "RohsCompliant" in KEYWORD_SEARCH_OPTIONS
    assert "RoHSCompliant" in RECOMMENDED_PRODUCTS_OPTIONS


# ---------- DigiKeyClient.request() lifecycle ----------


class _FakeHTTPResp:
    def __init__(self, status: int, body: dict | None = None, text: str = ""):
        self.status_code = status
        self._body = body or {}
        self.text = text or str(body)

    def json(self):
        return self._body


def _client_with_fixed_token(monkeypatch):
    """Build a client that already has a valid cached token, no network."""
    from digikey_mcp.server import DigiKeyClient

    client = DigiKeyClient("cid", "secret")
    client._token = "at-cached"
    client._token_expires_at = float("inf")  # never expires for this test
    return client


def test_request_returns_json_on_200(monkeypatch):
    client = _client_with_fixed_token(monkeypatch)

    def fake_request(method, url, **kwargs):
        assert method == "GET"
        assert url.endswith("/manufacturers")
        return _FakeHTTPResp(200, {"Manufacturers": [{"Id": 1}]})

    monkeypatch.setattr(client._session, "request", fake_request)
    result = client.request("GET", "/products/v4/search/manufacturers")
    assert result == {"Manufacturers": [{"Id": 1}]}


def test_request_retries_once_on_401_with_fresh_token(monkeypatch):
    client = _client_with_fixed_token(monkeypatch)
    calls: list[tuple[str, str]] = []

    def fake_post(url, data, **kwargs):
        # Token refresh on 401 — return a NEW token.
        return _FakeHTTPResp(200, {"access_token": "at-refreshed", "expires_in": 600})

    def fake_request(method, url, headers, **kwargs):
        calls.append(("request", headers["Authorization"]))
        # First call: 401 with stale token. Second call: 200 with new token.
        if len(calls) == 1:
            return _FakeHTTPResp(401, text="token expired")
        return _FakeHTTPResp(200, {"ok": True})

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr(client._session, "request", fake_request)

    result = client.request("GET", "/products/v4/search/manufacturers")
    assert result == {"ok": True}
    assert calls[0][1] == "Bearer at-cached"  # first try used stale token
    assert calls[1][1] == "Bearer at-refreshed"  # retry used fresh token


def test_request_raises_digikey_api_error_with_body(monkeypatch):
    from digikey_mcp.server import DigiKeyAPIError

    client = _client_with_fixed_token(monkeypatch)

    def fake_request(method, url, **kwargs):
        return _FakeHTTPResp(429, text='{"error": "rate limit exceeded"}')

    monkeypatch.setattr(client._session, "request", fake_request)

    with pytest.raises(DigiKeyAPIError) as excinfo:
        client.request("GET", "/products/v4/search/manufacturers")
    msg = str(excinfo.value)
    assert "429" in msg
    assert "rate limit" in msg


def test_request_builds_expected_headers(monkeypatch):
    client = _client_with_fixed_token(monkeypatch)
    captured = {}

    def fake_request(method, url, headers, **kwargs):
        captured.update(headers)
        return _FakeHTTPResp(200, {"ok": True})

    monkeypatch.setattr(client._session, "request", fake_request)
    client.request("GET", "/products/v4/search/manufacturers", customer_id="42")

    assert captured["Authorization"] == "Bearer at-cached"
    assert captured["X-DIGIKEY-Client-Id"] == "cid"
    assert captured["X-DIGIKEY-Customer-Id"] == "42"
    assert captured["X-DIGIKEY-Locale-Site"] == "US"
    assert captured["X-DIGIKEY-Locale-Language"] == "en"
    assert captured["X-DIGIKEY-Locale-Currency"] == "USD"
