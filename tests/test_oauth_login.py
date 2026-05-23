"""Tests for digikey_mcp.oauth_login — non-network bits of the auth_code flow."""

import urllib.parse

import pytest


@pytest.fixture
def cfg():
    from digikey_mcp.oauth_login import OAuthConfig

    return OAuthConfig(
        api_base="https://api.digikey.com",
        client_id="my-client",
        client_secret="hush",
        redirect_uri="http://localhost:8765/oauth/callback",
    )


def test_authorize_url_carries_required_params(cfg):
    from digikey_mcp.oauth_login import build_authorization_url

    url = build_authorization_url(cfg, state="state-xyz")
    parsed = urllib.parse.urlsplit(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))

    assert parsed.scheme == "https"
    assert parsed.netloc == "api.digikey.com"
    assert parsed.path == "/v1/oauth2/authorize"
    assert qs["response_type"] == "code"
    assert qs["client_id"] == "my-client"
    assert qs["redirect_uri"] == "http://localhost:8765/oauth/callback"
    assert qs["state"] == "state-xyz"


def test_authorize_url_state_is_url_safe():
    from digikey_mcp.oauth_login import OAuthConfig, build_authorization_url

    cfg = OAuthConfig(
        api_base="https://sandbox-api.digikey.com",
        client_id="cid",
        client_secret="sec",
        redirect_uri="http://localhost:8765/oauth/callback",
    )
    url = build_authorization_url(cfg, state="needs space & =")
    parsed = urllib.parse.urlsplit(url)
    qs = dict(urllib.parse.parse_qsl(parsed.query))
    # Round-tripped state should equal the input — i.e. proper URL encoding.
    assert qs["state"] == "needs space & ="


def test_oauthconfig_derives_urls_from_api_base():
    from digikey_mcp.oauth_login import OAuthConfig

    cfg = OAuthConfig(
        api_base="https://sandbox-api.digikey.com",
        client_id="x", client_secret="y",
    )
    assert cfg.authorize_url == "https://sandbox-api.digikey.com/v1/oauth2/authorize"
    assert cfg.token_url == "https://sandbox-api.digikey.com/v1/oauth2/token"


def test_exchange_code_for_tokens_posts_expected_payload(monkeypatch, cfg):
    """Sanity-check the request shape without hitting the network."""
    from digikey_mcp import oauth_login

    captured = {}

    def fake_post(url, data, headers, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers

        class R:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_in": 600,
                }

        return R()

    monkeypatch.setattr(oauth_login.requests, "post", fake_post)
    result = oauth_login.exchange_code_for_tokens(cfg, code="THE_CODE")
    assert result["refresh_token"] == "rt"
    assert captured["url"] == "https://api.digikey.com/v1/oauth2/token"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "THE_CODE"
    assert captured["data"]["client_id"] == "my-client"
    assert captured["data"]["redirect_uri"] == cfg.redirect_uri


def test_exchange_code_raises_on_non_200(monkeypatch, cfg):
    from digikey_mcp import oauth_login

    class R:
        status_code = 400
        text = '{"error": "invalid_grant"}'

    monkeypatch.setattr(oauth_login.requests, "post", lambda *a, **kw: R())

    with pytest.raises(RuntimeError, match="invalid_grant"):
        oauth_login.exchange_code_for_tokens(cfg, code="bad")
