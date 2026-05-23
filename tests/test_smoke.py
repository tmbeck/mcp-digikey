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


def test_arg_parser_recognizes_check_credentials():
    from digikey_mcp.server import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--check-credentials"])
    assert args.check_credentials is True

    args = parser.parse_args([])
    assert args.check_credentials is False


def test_recommended_products_has_different_enum():
    from digikey_mcp.server import KEYWORD_SEARCH_OPTIONS, RECOMMENDED_PRODUCTS_OPTIONS

    # The endpoints share a couple of values but the enums genuinely differ.
    assert "LeadFree" in RECOMMENDED_PRODUCTS_OPTIONS
    assert "LeadFree" not in KEYWORD_SEARCH_OPTIONS
    assert "RohsCompliant" in KEYWORD_SEARCH_OPTIONS
    assert "RoHSCompliant" in RECOMMENDED_PRODUCTS_OPTIONS
