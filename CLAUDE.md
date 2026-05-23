# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`digikey-mcp` is a FastMCP server that exposes DigiKey's Product Search v4 REST API as MCP tools. Distributed as an installable package: `uv tool install .` (or `uvx digikey-mcp`) puts a `digikey-mcp` console script on the user's PATH (entry point declared in `pyproject.toml` `[project.scripts]`). Build backend is `hatchling`; the package lives under `src/digikey_mcp/`.

## Commands

```bash
uv sync                                      # install deps + dev tools
uv run digikey-mcp                           # serve over stdio (default subcommand)
uv run digikey-mcp serve --transport http    # serve over HTTP
uv run digikey-mcp check-credentials         # one-shot auth health check
uv run digikey-mcp login                     # browser OAuth → save refresh token
uv run digikey-mcp logout                    # delete stored tokens
uv run pytest                                # tests (no network, ~0.5s)
uv run ruff check .                          # lint
uv build                                     # build sdist + wheel
uv tool install .                            # install CLI globally
```

## Environment

`.env` (loaded via `python-dotenv`) must define `CLIENT_ID` and `CLIENT_SECRET`. `USE_SANDBOX` is a normal boolean (`true`/`false`/`1`/`0`, defaults to `false` → production). Optional: `DIGIKEY_LOCALE_SITE` / `_LANGUAGE` / `_CURRENCY` (defaults `US`/`en`/`USD`), `LOG_LEVEL` (default `INFO`). `.env.example` is the canonical template.

## Architecture

- **Layout.** `src/digikey_mcp/`:
  - `server.py` — FastMCP instance, `DigiKeyClient`, all `@mcp.tool()` definitions, argparse CLI, and `main()`.
  - `auth_store.py` — JSON token persistence at the platformdirs user-config path, mode 0600 on Unix. `DIGIKEY_MCP_TOKENS_PATH` overrides for tests.
  - `oauth_login.py` — `digikey-mcp login` flow: builds authorization URLs, runs a single-shot stdlib `http.server` on the callback port, exchanges the code at `/v1/oauth2/token`.
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
- **Two OAuth grant types.** `DigiKeyClient._refresh_token()` prefers `grant_type=refresh_token` when `auth_store.load()` returns stored user tokens; otherwise it uses `grant_type=client_credentials`. On refresh failure (revoked, expired refresh_token, etc.) it logs a warning and falls back to client_credentials so the server keeps working with public-data access until the user re-runs `digikey-mcp login`. Don't introduce a hard failure path here — graceful degradation is the design.
- **OAuth redirect URI must be pre-registered.** DigiKey rejects callback URLs that aren't in the app's allowlist at developer.digikey.com. Default is `http://localhost:8765/oauth/callback`. If you change the default port/path in `oauth_login.py`, the README setup steps need to match — users *must* register the exact URL.
- **`digikey-mcp login` uses stdlib `http.server`**, not FastMCP routes or aiohttp. Single-request lifecycle: bind → open browser → `handle_request()` until code arrives → close. Keeps the dep footprint minimal and avoids tangling the login flow with the MCP server lifecycle.

## Reference material

`useful_llm_context/digikey Product Search Swagger docs/` contains the DigiKey Product Search OpenAPI/Swagger JSON (multiple identical copies — `ProductSearch.json` is fine). This is the authoritative source for endpoint paths, request/response shapes, sort fields, and enum values. Trust it over the README when they disagree. `useful_llm_context/fastMCP docs.txt` has FastMCP usage notes.
