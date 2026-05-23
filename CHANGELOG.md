# Changelog

All notable changes to this project. The format is loosely
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
follows [Semantic Versioning](https://semver.org/) once it hits 1.0.

## [0.3.0] - 2026-05-23

A substantial reshape of the original single-file server: bug fixes
against the Product Search v4 swagger, MCP best-practice hardening, new
endpoints, an HTTP transport mode, and dev tooling.

### Breaking

- **`get_product_pricing` signature changed** to match the actual
  swagger endpoint. Previously called a nonexistent `/productpricing`
  URL with `requested_quantity` as a query param. Now calls `/pricing`
  with `limit` / `offset` / `in_stock` / `exclude_marketplace` /
  `exclude_tariff`. If you wanted "price at a specific quantity," use
  the new `get_pricing_by_quantity` tool.
- **`USE_SANDBOX` parsing fixed.** The original code interpreted
  `USE_SANDBOX=false` as enabling the sandbox. It now does what the
  README always claimed: `true` → sandbox, `false`/unset → production.
- **`search_options` values now validated** against the swagger enum;
  unknown values raise `ValueError` instead of being silently dropped.
  In particular, `LeadFree` and `RoHSCompliant` are not valid for
  `keyword_search` (the correct value there is `RohsCompliant`) — they
  *are* valid for the new `get_recommended_products` tool, which uses
  a different enum.
- **Console-script entry point** changed from `python
  digikey_mcp_server.py` to `digikey-mcp`. Package layout moved to
  `src/digikey_mcp/`.

### Added

- New tool: `get_pricing_by_quantity` —
  `/search/{productNumber}/pricingbyquantity/{requestedQuantity}`.
- New tool: `get_alternate_packaging` —
  `/search/{productNumber}/alternatepackaging`.
- New tool: `get_product_associations` —
  `/search/{productNumber}/associations`.
- New tool: `get_recommended_products` —
  `/search/{productNumber}/recommendedproducts`.
- `digikey-mcp --check-credentials` — one-shot OAuth + `/manufacturers`
  call, prints PASS/FAIL and exits without starting the MCP server.
- `digikey-mcp --transport http` (with `--host` / `--port`) — run as a
  streamable HTTP server at `/mcp/`. Stdio remains the default and what
  MCP clients launch.
- Configurable locale via `DIGIKEY_LOCALE_SITE` / `_LANGUAGE` /
  `_CURRENCY` env vars (was hardcoded `US`/`en`/`USD`).
- `Annotated` + `pydantic.Field` on `keyword_search` params, so the
  MCP tool schema carries `minimum`/`maximum`/`pattern` constraints
  and pydantic enforces them before the function runs.

### Fixed

- `keyword_search` filter wiring: `ManufacturerId` / `CategoryId` /
  `SearchOptionList` were being sent at the top level of the request
  body, which DigiKey silently ignored. They now live under
  `FilterOptionsRequest.{ManufacturerFilter, CategoryFilter,
  SearchOptions}` as the swagger requires, so filters actually filter.
- `search_product_substitutions` no longer sends `limit` /
  `excludeMarketPlaceProducts` / `searchOptionList` query params —
  the v4 endpoint doesn't accept them.
- OAuth token fetch is now **lazy** (was at import time, so a bad
  credential prevented the server from starting and listing tools).
- OAuth token is now **proactively refreshed** based on `expires_in`,
  not just on 401. The 401-retry path remains as a safety net.
- HTTP errors return `DigiKeyAPIError` carrying the truncated DigiKey
  response body instead of an opaque `requests.HTTPError`.
- Transient HTTP failures (429, 5xx) retry up to 3 times with
  exponential backoff via a `urllib3.Retry` adapter; honors
  `Retry-After`.
- Logging forced to `sys.stderr` — stdio transport reserves stdout
  for JSON-RPC frames, and any stdout write corrupts the protocol
  stream.

### Changed

- Package layout: `src/digikey_mcp/` (was a single top-level
  `digikey_mcp_server.py`).
- Pinned `fastmcp` to `>=2.14,<3`. v3 has a much heavier transitive
  tree (OpenTelemetry, cyclopts, beartype, watchfiles, websockets) for
  features this server doesn't use; the v3 surface API is reportedly
  compatible so the upper bound can be lifted later.
- Build backend: `hatchling`. Source distribution now excludes
  `useful_llm_context/`, `.claude/`, and `CLAUDE.md` (dev-only
  material).

### Dev

- Smoke test suite at `tests/test_smoke.py` (23 tests, no network).
- Ruff config: `E/F/W/I/UP/B/SIM`, line-length 100, py310 target.
- `py.typed` marker (PEP 561).

## [0.2.0] - earlier

Initial single-file FastMCP server (`digikey_mcp_server.py`) covering
keyword search, product details, manufacturers/categories, basic
pricing, and substitutions. Superseded entirely by 0.3.0; see Breaking.
