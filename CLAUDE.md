# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`digikey-mcp` is a FastMCP server that exposes DigiKey's Product Search v4 REST API as MCP tools. Distributed as an installable package: `uv tool install .` (from a checkout) or `uv tool install git+https://github.com/tmbeck/mcp-digikey` puts a `digikey-mcp` console script on the user's PATH (entry point declared in `pyproject.toml` `[project.scripts]`). Build backend is `hatchling`; the package lives under `src/digikey_mcp/`. Not published to PyPI, so `uvx digikey-mcp` doesn't work — `uvx` resolves names against PyPI.

## Commands

```bash
uv sync                              # install deps + dev tools from uv.lock
uv run digikey-mcp                   # run the server from the source tree (stdio)
uv run digikey-mcp --transport http  # run as HTTP server on 127.0.0.1:8000
uv run digikey-mcp --check-credentials  # one-shot auth health check, exits 0/1
uv run python -m digikey_mcp         # equivalent to `uv run digikey-mcp`
uv run pytest                         # smoke tests, no network, ~0.5s
uv run ruff check .                  # lint
uv build                             # build sdist + wheel into dist/
uv tool install .                    # install the CLI globally from a checkout
uv tool install git+https://github.com/tmbeck/mcp-digikey  # ...or from GitHub
uv add <package>                     # add a dependency
```

## Environment

`.env` (loaded via `python-dotenv`) must define `CLIENT_ID` and `CLIENT_SECRET`. `USE_SANDBOX` is a normal boolean (`true`/`false`/`1`/`0`, defaults to `false` → production). Optional: `DIGIKEY_LOCALE_SITE` / `_LANGUAGE` / `_CURRENCY` (defaults `US`/`en`/`USD`), `LOG_LEVEL` (default `INFO`). `.env.example` is the canonical template.

## Architecture

- **Layout.** Single package `src/digikey_mcp/`:
  - `server.py` — FastMCP instance, `DigiKeyClient`, all `@mcp.tool()` definitions, and `main()`.
  - `__main__.py` — delegates to `server.main()` so `python -m digikey_mcp` works.
  - `__init__.py` — exposes `__version__` via `importlib.metadata`.
- **`DigiKeyClient` owns the HTTP + auth concerns.** It holds a `requests.Session`, the OAuth2 client-credentials token, and a lock. Token fetch is **lazy** — nothing hits the network at import time, so `mcp.run()` starts and lists tools even if credentials are wrong (failure surfaces on the first tool call instead). On a 401, `request()` refreshes the token once and retries; further failures raise.
- **Tool functions are thin.** Each `@mcp.tool()` builds a payload (params or JSON body) and forwards to `_get_client().request(...)`. Adding a new endpoint = one decorated function. Keep docstrings descriptive — FastMCP turns them into the MCP tool schema clients see.
- **Filters live under `FilterOptionsRequest`.** For `keyword_search`, `ManufacturerFilter` / `CategoryFilter` / `SearchOptions` are nested under `FilterOptionsRequest` in the request body — not at the top level. This is easy to get wrong because DigiKey silently ignores unknown top-level fields and returns unfiltered results.
- **`SearchOptions` enum values are case-sensitive.** Valid values: `ChipOutpost, Has3DModel, HasCadModel, HasDatasheet, HasProductPhoto, InStock, NewProduct, NonRohsCompliant, NormallyStocking, RohsCompliant`. Note `RohsCompliant` (not `RoHSCompliant`); there is no `LeadFree`. Earlier versions of this code/README had both wrong.
- **`/substitutions` only accepts `productNumber` + `includes`** per the v4 swagger. Don't add `limit` / `excludeMarketPlaceProducts` query params — DigiKey ignores them, and filtering must happen client-side.
- **stdio safety.** This server uses FastMCP's default stdio transport, which reserves stdout for JSON-RPC frames. `main()` forces logging to `sys.stderr` with `force=True`; never add `print()` calls or other stdout writes inside tools or module-level code — they will corrupt the protocol stream and the client will disconnect.
- **HTTP errors are surfaced as `DigiKeyAPIError`** (with the truncated DigiKey response body included) rather than `requests.HTTPError`, so MCP clients see something useful.
- **Two pricing endpoints with different shapes.** `get_product_pricing` -> `/search/{n}/pricing` is multi-match with `limit`/`offset`/filters; `get_pricing_by_quantity` -> `/search/{n}/pricingbyquantity/{qty}` is single-match with quantity in the path. Earlier code had a single `get_product_pricing` calling a nonexistent `/productpricing` URL — don't accidentally restore that pattern.
- **Two `search_options` enums.** `keyword_search` and `get_recommended_products` accept the same parameter name with *different valid values* (see `KEYWORD_SEARCH_OPTIONS` vs `RECOMMENDED_PRODUCTS_OPTIONS` in `server.py`). `_parse_search_options()` validates per-endpoint; adding a new endpoint that takes search options needs its own enum constant.
- **HTTP transport startup is slow** (~10-15s) because FastMCP spawns a docket worker. Stdio startup is instant. If you're benchmarking, this isn't our latency.
- **Token refresh is proactive.** The client stores `_token_expires_at` (monotonic time) based on `expires_in` from DigiKey's response; `_token_value()` refreshes proactively. The 401-retry path in `request()` is a safety net for clock drift / unexpected revocation, not the primary refresh mechanism.

## Reference material

`useful_llm_context/digikey Product Search Swagger docs/` contains the DigiKey Product Search OpenAPI/Swagger JSON (multiple identical copies — `ProductSearch.json` is fine). This is the authoritative source for endpoint paths, request/response shapes, sort fields, and enum values. Trust it over the README when they disagree. `useful_llm_context/fastMCP docs.txt` has FastMCP usage notes.
