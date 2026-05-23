"""Tests for digikey_mcp.auth_store — token persistence on disk."""

import json
import os
import stat
import time

import pytest


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    monkeypatch.setenv("DIGIKEY_MCP_TOKENS_PATH", str(path))
    return path


def test_load_returns_none_when_missing(token_file):
    from digikey_mcp import auth_store

    assert auth_store.load() is None


def test_save_round_trip(token_file):
    from digikey_mcp import auth_store

    tokens = auth_store.StoredTokens(
        refresh_token="rt-1",
        access_token="at-1",
        expires_at=time.time() + 600,
        obtained_at=time.time(),
    )
    written = auth_store.save(tokens)
    assert written == token_file
    assert written.exists()
    loaded = auth_store.load()
    assert loaded == tokens


@pytest.mark.skipif(os.name != "posix", reason="chmod is a no-op on Windows")
def test_save_sets_mode_0600(token_file):
    from digikey_mcp import auth_store

    auth_store.save(
        auth_store.StoredTokens(
            refresh_token="rt", access_token="at", expires_at=1.0, obtained_at=0.0
        )
    )
    mode = stat.S_IMODE(token_file.stat().st_mode)
    assert mode == 0o600


def test_save_creates_parent_dir(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "dir" / "tokens.json"
    monkeypatch.setenv("DIGIKEY_MCP_TOKENS_PATH", str(path))
    from digikey_mcp import auth_store

    auth_store.save(
        auth_store.StoredTokens(
            refresh_token="rt", access_token="at", expires_at=1.0, obtained_at=0.0
        )
    )
    assert path.exists()


def test_load_returns_none_on_corrupt(token_file):
    token_file.write_text("not json at all")
    from digikey_mcp import auth_store

    assert auth_store.load() is None


def test_load_returns_none_on_missing_fields(token_file):
    token_file.write_text(json.dumps({"refresh_token": "only"}))
    from digikey_mcp import auth_store

    assert auth_store.load() is None


def test_delete_removes_file_returns_true(token_file):
    from digikey_mcp import auth_store

    auth_store.save(
        auth_store.StoredTokens(
            refresh_token="rt", access_token="at", expires_at=1.0, obtained_at=0.0
        )
    )
    assert token_file.exists()
    assert auth_store.delete() is True
    assert not token_file.exists()


def test_delete_returns_false_when_absent(token_file):
    from digikey_mcp import auth_store

    assert auth_store.delete() is False


def test_is_access_token_expired():
    from digikey_mcp.auth_store import StoredTokens

    fresh = StoredTokens(
        refresh_token="rt", access_token="at",
        expires_at=time.time() + 3600, obtained_at=time.time(),
    )
    assert fresh.is_access_token_expired() is False

    expired = StoredTokens(
        refresh_token="rt", access_token="at",
        expires_at=time.time() - 1, obtained_at=time.time() - 3600,
    )
    assert expired.is_access_token_expired() is True

    # Within skew window — treated as expired.
    nearly = StoredTokens(
        refresh_token="rt", access_token="at",
        expires_at=time.time() + 30, obtained_at=time.time(),
    )
    assert nearly.is_access_token_expired(skew_sec=60) is True
