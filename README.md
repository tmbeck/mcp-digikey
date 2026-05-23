# DigiKey MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes DigiKey's [Product Search v4 API](https://developer.digikey.com/products/product-search/productsearch/keywordsearch) as MCP tools. Built on [FastMCP](https://github.com/jlowin/fastmcp).

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- DigiKey API credentials (`CLIENT_ID` and `CLIENT_SECRET`) — register an app at https://developer.digikey.com/

## Install

Install as a standalone CLI in an isolated environment (this is the recommended path):

```bash
# From a local checkout:
uv tool install .

# Or straight from GitHub:
uv tool install git+https://github.com/tmbeck/mcp-digikey
```

After install, `digikey-mcp` is on your `PATH`.

For development work from a source checkout, `uv run digikey-mcp` runs it without installing globally.

> Note: this package isn't on PyPI, so `uvx digikey-mcp` won't work — `uvx` resolves package names against PyPI. Use `uv tool install` as above.

## Configure

The server reads credentials from environment variables (a `.env` file in the working directory is loaded automatically). Copy `.env.example` to `.env` and fill in:

```dotenv
CLIENT_ID=your_digikey_client_id
CLIENT_SECRET=your_digikey_client_secret
USE_SANDBOX=false   # set to true to hit sandbox-api.digikey.com
```

Optional locale overrides (defaults shown):

```dotenv
DIGIKEY_LOCALE_SITE=US
DIGIKEY_LOCALE_LANGUAGE=en
DIGIKEY_LOCALE_CURRENCY=USD
LOG_LEVEL=INFO
```

## Run

```bash
digikey-mcp
```

Or from a source checkout: `uv run digikey-mcp`.

## Setting credentials in production

In production, the server is launched as a subprocess by an MCP client (Claude Desktop, Claude Code, etc.), so a project-local `.env` file usually **won't** be picked up — the client's working directory isn't your repo. Choose one of:

1. **Inline `env:` in the client config** (simplest). Put `CLIENT_ID` / `CLIENT_SECRET` in the `env` block of the MCP server entry (see `.mcp.json.example` and the Claude Desktop section below). The config file itself becomes the secret — keep it out of version control and restrict its permissions.
2. **System environment**. Export `CLIENT_ID` / `CLIENT_SECRET` from your shell profile, systemd unit, or launchd plist. Omit the `env` block in the client config entirely — the server inherits the parent process's environment. Best when several tools need the same credentials.
3. **Secret-manager wrapper**. Launch via your secret manager so plaintext never lives on disk. Example with the 1Password CLI:

   ```json
   {
     "mcpServers": {
       "digikey": {
         "command": "op",
         "args": ["run", "--no-masking", "--", "digikey-mcp"]
       }
     }
   }
   ```

   …with `CLIENT_ID = op://Vault/DigiKey/client_id` etc. in `~/.config/op/secrets`. Equivalent patterns work for `aws-vault`, `vault exec`, `doppler run`, etc.
4. **`.env` file** (dev only). Works when you launch the server yourself from the repo with `uv run digikey-mcp`. Don't rely on it for client-launched production use.

## Claude Code (project-level)

First, `uv tool install` the package so `digikey-mcp` is on your PATH (see Install above). Then copy `.mcp.json.example` to `.mcp.json` (gitignored), fill in your credentials, and Claude Code will pick it up when you open this project. The file format is:

```json
{
  "mcpServers": {
    "digikey": {
      "command": "digikey-mcp",
      "env": {
        "CLIENT_ID": "your_client_id",
        "CLIENT_SECRET": "your_client_secret",
        "USE_SANDBOX": "false"
      }
    }
  }
}
```

## Claude Desktop integration

After `uv tool install`, add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "digikey": {
      "command": "digikey-mcp",
      "env": {
        "CLIENT_ID": "your_digikey_client_id",
        "CLIENT_SECRET": "your_digikey_client_secret",
        "USE_SANDBOX": "false"
      }
    }
  }
}
```

If `digikey-mcp` isn't on Claude Desktop's PATH (it doesn't always inherit your shell's), use the absolute path — run `which digikey-mcp` to find it (typically `~/.local/bin/digikey-mcp`).

## CLI flags

```
digikey-mcp [--check-credentials] [--transport {stdio,http}]
            [--host HOST] [--port PORT]
```

- `--check-credentials` — fetch an OAuth token + call `/search/manufacturers`, print PASS/FAIL, and exit. Use to debug `.mcp.json` / `claude_desktop_config.json` wiring without launching the server.
- `--transport stdio` (default) — JSON-RPC over stdin/stdout; what MCP clients launch.
- `--transport http` — streamable HTTP at `http://<host>:<port>/mcp/`. Heavier startup (~10-15s due to FastMCP's background worker init); useful for running as a persistent service.
- `--host` / `--port` — bind address for HTTP mode (defaults `127.0.0.1:8000`). Also `DIGIKEY_MCP_TRANSPORT` / `DIGIKEY_MCP_HOST` / `DIGIKEY_MCP_PORT` env vars.

## Development

```bash
uv sync                          # create .venv and install deps + dev tools
uv run digikey-mcp                # run from the source tree
uv run python -m digikey_mcp     # equivalent
uv run pytest                    # smoke tests (no network)
uv run ruff check .              # lint
```

## Tools

### Search

- `keyword_search(keywords, limit=5, offset=0, manufacturer_id=None, category_id=None, search_options=None, sort_field=None, sort_order="Ascending")` — keyword/part-number search with sort + filter.
- `search_manufacturers()` — list all manufacturers (returns IDs usable with `keyword_search`).
- `search_categories()` / `get_category_by_id(category_id)` — list/inspect categories.
- `search_product_substitutions(product_number, includes=None)` — find substitute parts.
- `get_product_associations(product_number)` — eval boards, mating connectors, accessories.
- `get_recommended_products(product_number, limit=1, search_options=None, exclude_marketplace=False)` — DigiKey's "you might also like."

### Product detail

- `product_details(product_number, manufacturer_id=None, customer_id="0")`
- `get_product_media(product_number)` — images, datasheets, videos.
- `get_alternate_packaging(product_number)` — tape-and-reel vs cut tape etc.

### Pricing

- `get_product_pricing(product_number, customer_id="0", limit=5, offset=0, in_stock=False, exclude_marketplace=False, exclude_tariff=False)` — multi-match product pricing with filters.
- `get_pricing_by_quantity(product_number, requested_quantity, manufacturer_id=None, customer_id="0")` — pricing options (Exact / MinimumOrderQuantity / MaxOrderQuantity / BetterValue) at a specific quantity.
- `get_digi_reel_pricing(product_number, requested_quantity, customer_id="0")` — DigiReel pricing.

### `keyword_search` filters

`search_options` is a comma-delimited string, validated against the v4 swagger enum:

```
ChipOutpost, Has3DModel, HasCadModel, HasDatasheet, HasProductPhoto,
InStock, NewProduct, NonRohsCompliant, NormallyStocking, RohsCompliant
```

Unknown values are rejected with a clear `ValueError` listing the actual allowed set.

> ⚠️ `get_recommended_products` uses a **different** enum (`LeadFree, CollapsePackingTypes, ExcludeNonStock, Has3DModel, InStock, ManufacturerPartSearch, NewProductsOnly, RoHSCompliant`). Same `search_options` parameter name, different valid values — the validator catches mistakes per tool.

### `keyword_search` sort fields

```
None, Packaging, ProductStatus, DigiKeyProductNumber, ManufacturerProductNumber,
Manufacturer, MinimumQuantity, QuantityAvailable, Price, Supplier,
PriceManufacturerStandardPackage
```

`sort_order` is `Ascending` or `Descending`.

## Examples

```python
keyword_search("resistor", limit=10)
keyword_search("capacitor", limit=5, sort_field="Price", sort_order="Ascending")
keyword_search("LED", limit=10, search_options="InStock,RohsCompliant")
product_details("296-8875-1-ND")
get_pricing_by_quantity("296-8875-1-ND", requested_quantity=100)
get_product_associations("296-8875-1-ND")
```
