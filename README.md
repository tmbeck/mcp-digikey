# DigiKey MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes DigiKey's [Product Search v4 API](https://developer.digikey.com/products/product-search/productsearch/keywordsearch) as MCP tools. Built on [FastMCP](https://github.com/jlowin/fastmcp).

## Requirements

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- DigiKey API credentials (`CLIENT_ID` and `CLIENT_SECRET`) — register an app at https://developer.digikey.com/

## Install

Install as a standalone CLI in an isolated environment:

```bash
uv tool install digikey-mcp
# or, directly from this checkout:
uv tool install .
```

Or run without installing (uv resolves and caches on first use):

```bash
uvx digikey-mcp
```

Or, if you prefer `pip`:

```bash
pip install .
```

Once installed, the `digikey-mcp` console script is on your `PATH`.

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

## Claude Desktop integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) — `uvx` is the simplest path since it handles the venv for you:

```json
{
  "mcpServers": {
    "digikey": {
      "command": "uvx",
      "args": ["digikey-mcp"],
      "env": {
        "CLIENT_ID": "your_digikey_client_id",
        "CLIENT_SECRET": "your_digikey_client_secret",
        "USE_SANDBOX": "false"
      }
    }
  }
}
```

If you installed via `uv tool install`, swap `command` to `digikey-mcp` and drop `args`.

## Development

```bash
uv sync                     # create .venv and install deps
uv run digikey-mcp          # run from the source tree
uv run python -m digikey_mcp
```

## Tools

### Search

- `keyword_search(keywords, limit=5, offset=0, manufacturer_id=None, category_id=None, search_options=None, sort_field=None, sort_order="Ascending")` — keyword/part-number search with sort + filter.
- `search_manufacturers()` — list all manufacturers (returns IDs usable with `keyword_search`).
- `search_categories()` / `get_category_by_id(category_id)` — list/inspect categories.
- `search_product_substitutions(product_number, includes=None)` — find substitute parts.

### Product detail

- `product_details(product_number, manufacturer_id=None, customer_id="0")`
- `get_product_media(product_number)` — images, datasheets, videos.
- `get_product_pricing(product_number, customer_id="0", requested_quantity=1)`
- `get_digi_reel_pricing(product_number, requested_quantity, customer_id="0")`

### `keyword_search` filters

`search_options` is a comma-delimited string. Valid values (from the v4 swagger):

```
ChipOutpost, Has3DModel, HasCadModel, HasDatasheet, HasProductPhoto,
InStock, NewProduct, NonRohsCompliant, NormallyStocking, RohsCompliant
```

> ⚠️ Note: the v4 enum is `RohsCompliant` (not `RoHSCompliant`), and there is no `LeadFree` option — earlier versions of this README listed those, but they cause the API to silently ignore the filter.

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
get_product_pricing("296-8875-1-ND", requested_quantity=100)
```
