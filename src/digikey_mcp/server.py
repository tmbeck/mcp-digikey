"""DigiKey Product Search v4 MCP server."""

import logging
import os
import sys
import threading
from dataclasses import dataclass
from typing import Annotated, Any

import requests
from dotenv import load_dotenv
from fastmcp import FastMCP
from pydantic import Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("digikey_mcp")

PROD_HOST = "https://api.digikey.com"
SANDBOX_HOST = "https://sandbox-api.digikey.com"

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off", ""}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    raise ValueError(f"{name} must be a boolean-like value, got {raw!r}")


class DigiKeyAPIError(RuntimeError):
    """A DigiKey HTTP error surfaced as a clean message to the MCP client."""


@dataclass(frozen=True)
class Locale:
    site: str = "US"
    language: str = "en"
    currency: str = "USD"


def _build_retry_adapter() -> HTTPAdapter:
    # Retry transient failures: rate-limits (429) and server errors (5xx).
    # urllib3 honors Retry-After by default. Retry on all our methods —
    # every DigiKey call we make is an idempotent read.
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    return HTTPAdapter(max_retries=retry)


class DigiKeyClient:
    """Thin wrapper around DigiKey Product Search v4.

    Owns the OAuth2 client-credentials token and refreshes it on demand. Tokens
    are fetched lazily on first call so the server can start (and list tools)
    even when credentials are temporarily wrong.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        use_sandbox: bool = False,
        locale: Locale | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = SANDBOX_HOST if use_sandbox else PROD_HOST
        self.token_url = f"{self.api_base}/v1/oauth2/token"
        self.locale = locale or Locale()
        self._session = requests.Session()
        self._session.mount("https://", _build_retry_adapter())
        self._token: str | None = None
        self._token_lock = threading.Lock()

    def _fetch_token(self) -> str:
        env = "SANDBOX" if self.api_base == SANDBOX_HOST else "PRODUCTION"
        logger.info("Requesting OAuth token from %s (client_id=%s…)", env, self.client_id[:8])
        resp = self._session.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code != 200:
            detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
            logger.error("OAuth error %s: %s", resp.status_code, detail)
            raise DigiKeyAPIError(
                f"DigiKey OAuth failed ({resp.status_code}): {detail}. "
                "Check CLIENT_ID/CLIENT_SECRET and USE_SANDBOX."
            )
        return resp.json()["access_token"]

    def _token_value(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if self._token is None or force_refresh:
                self._token = self._fetch_token()
            return self._token

    def _headers(self, customer_id: str, *, force_refresh: bool = False) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_value(force_refresh=force_refresh)}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "Content-Type": "application/json",
            "X-DIGIKEY-Locale-Site": self.locale.site,
            "X-DIGIKEY-Locale-Language": self.locale.language,
            "X-DIGIKEY-Locale-Currency": self.locale.currency,
            "X-DIGIKEY-Customer-Id": customer_id,
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        customer_id: str = "0",
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        for attempt in (0, 1):
            resp = self._session.request(
                method,
                url,
                headers=self._headers(customer_id, force_refresh=attempt == 1),
                params=params,
                json=json,
                timeout=30,
            )
            if resp.status_code == 401 and attempt == 0:
                logger.info("Got 401 on %s %s, refreshing token and retrying once", method, path)
                continue
            if resp.status_code != 200:
                detail = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
                logger.error("DigiKey %s %s -> %s: %s", method, path, resp.status_code, detail)
                raise DigiKeyAPIError(
                    f"DigiKey API {resp.status_code} on {method} {path}: {detail}"
                )
            return resp.json()
        raise RuntimeError("unreachable")


def _build_client_from_env() -> DigiKeyClient:
    load_dotenv()
    client_id = os.getenv("CLIENT_ID")
    client_secret = os.getenv("CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "CLIENT_ID and CLIENT_SECRET must be set in the environment (or a .env file)."
        )
    locale = Locale(
        site=os.getenv("DIGIKEY_LOCALE_SITE", "US"),
        language=os.getenv("DIGIKEY_LOCALE_LANGUAGE", "en"),
        currency=os.getenv("DIGIKEY_LOCALE_CURRENCY", "USD"),
    )
    return DigiKeyClient(
        client_id,
        client_secret,
        use_sandbox=_env_bool("USE_SANDBOX", default=False),
        locale=locale,
    )


mcp = FastMCP("DigiKey MCP Server")
_client: DigiKeyClient | None = None


def _get_client() -> DigiKeyClient:
    global _client
    if _client is None:
        _client = _build_client_from_env()
    return _client


_SEARCH_OPTIONS_DOC = (
    "Comma-delimited values from: ChipOutpost, Has3DModel, HasCadModel, "
    "HasDatasheet, HasProductPhoto, InStock, NewProduct, NonRohsCompliant, "
    "NormallyStocking, RohsCompliant."
)
_SORT_FIELD_DOC = (
    "Sort field. One of: None, Packaging, ProductStatus, DigiKeyProductNumber, "
    "ManufacturerProductNumber, Manufacturer, MinimumQuantity, QuantityAvailable, "
    "Price, Supplier, PriceManufacturerStandardPackage."
)


@mcp.tool()
def keyword_search(
    keywords: Annotated[
        str,
        Field(description="Search terms or part numbers.", max_length=250),
    ],
    limit: Annotated[
        int,
        Field(description="Max results (DigiKey v4 hard cap is 50).", ge=1, le=50),
    ] = 5,
    offset: Annotated[
        int,
        Field(description="Pagination offset.", ge=0),
    ] = 0,
    manufacturer_id: Annotated[
        str | None,
        Field(description="Restrict to a manufacturer ID (from `search_manufacturers`)."),
    ] = None,
    category_id: Annotated[
        str | None,
        Field(description="Restrict to a category ID (from `search_categories`)."),
    ] = None,
    search_options: Annotated[
        str | None,
        Field(description=_SEARCH_OPTIONS_DOC),
    ] = None,
    sort_field: Annotated[
        str | None,
        Field(description=_SORT_FIELD_DOC),
    ] = None,
    sort_order: Annotated[
        str,
        Field(description="Sort direction.", pattern="^(Ascending|Descending)$"),
    ] = "Ascending",
) -> dict[str, Any]:
    """Search DigiKey products by keyword or part number."""
    body: dict[str, Any] = {"Keywords": keywords, "Limit": limit, "Offset": offset}

    filter_options: dict[str, Any] = {}
    if manufacturer_id:
        filter_options["ManufacturerFilter"] = [{"Id": str(manufacturer_id)}]
    if category_id:
        filter_options["CategoryFilter"] = [{"Id": str(category_id)}]
    if search_options:
        filter_options["SearchOptions"] = [
            s.strip() for s in search_options.split(",") if s.strip()
        ]
    if filter_options:
        body["FilterOptionsRequest"] = filter_options

    if sort_field:
        body["SortOptions"] = {"Field": sort_field, "SortOrder": sort_order}

    return _get_client().request("POST", "/products/v4/search/keyword", json=body)


@mcp.tool()
def product_details(
    product_number: str,
    manufacturer_id: str | None = None,
    customer_id: str = "0",
) -> dict[str, Any]:
    """Get detailed information for a specific product.

    Args:
        product_number: DigiKey or manufacturer part number.
        manufacturer_id: Optional manufacturer ID for disambiguation when a
            manufacturer part number matches multiple manufacturers.
        customer_id: Customer ID for pricing (default "0").
    """
    params = {"manufacturerId": manufacturer_id} if manufacturer_id else None
    return _get_client().request(
        "GET",
        f"/products/v4/search/{product_number}/productdetails",
        customer_id=customer_id,
        params=params,
    )


@mcp.tool()
def search_manufacturers() -> dict[str, Any]:
    """List all product manufacturers (IDs usable with `keyword_search`)."""
    return _get_client().request("GET", "/products/v4/search/manufacturers")


@mcp.tool()
def search_categories() -> dict[str, Any]:
    """List all product categories (IDs usable with `keyword_search`)."""
    return _get_client().request("GET", "/products/v4/search/categories")


@mcp.tool()
def get_category_by_id(category_id: int) -> dict[str, Any]:
    """Get a specific category by its ID.

    Args:
        category_id: The category ID to retrieve.
    """
    return _get_client().request("GET", f"/products/v4/search/categories/{category_id}")


@mcp.tool()
def search_product_substitutions(
    product_number: str,
    includes: str | None = None,
) -> dict[str, Any]:
    """Find substitute products for a given part.

    Per DigiKey's Product Search v4 swagger, this endpoint only accepts the
    product number (path) and an optional `includes` query param. Filtering by
    search options or excluding marketplace products must be done client-side
    on the results.

    Args:
        product_number: The product to get substitutions for. Works best with a
            DigiKey product number.
        includes: Optional projection string (passed through as-is).
    """
    params = {"includes": includes} if includes else None
    return _get_client().request(
        "GET",
        f"/products/v4/search/{product_number}/substitutions",
        params=params,
    )


@mcp.tool()
def get_product_media(product_number: str) -> dict[str, Any]:
    """Get media (images, datasheets, videos) for a product.

    Args:
        product_number: The product to get media for.
    """
    return _get_client().request("GET", f"/products/v4/search/{product_number}/media")


@mcp.tool()
def get_product_pricing(
    product_number: str,
    customer_id: str = "0",
    limit: int = 5,
    offset: int = 0,
    in_stock: bool = False,
    exclude_marketplace: bool = False,
    exclude_tariff: bool = False,
) -> dict[str, Any]:
    """Get pricing information for products matching a product number.

    Returns up to `limit` matched products (DigiKey caps this at 10) with
    pricing tiers. MyPricing is included if the customer_id resolves to
    a registered account.

    Args:
        product_number: Manufacturer or DigiKey part number; partial matches allowed.
        customer_id: Customer ID for MyPricing (default "0").
        limit: Max products to return, 1-10 (default 5).
        offset: Pagination offset (default 0).
        in_stock: Only return in-stock products.
        exclude_marketplace: Only return DigiKey-fulfilled products.
        exclude_tariff: Exclude products subject to tariffs.
    """
    return _get_client().request(
        "GET",
        f"/products/v4/search/{product_number}/pricing",
        customer_id=customer_id,
        params={
            "limit": limit,
            "offset": offset,
            "inStock": in_stock,
            "excludeMarketplace": exclude_marketplace,
            "excludeTariff": exclude_tariff,
        },
    )


@mcp.tool()
def get_pricing_by_quantity(
    product_number: str,
    requested_quantity: int,
    manufacturer_id: str | None = None,
    customer_id: str = "0",
) -> dict[str, Any]:
    """Get pricing for a specific product at a specific quantity.

    Returns up to four pricing options:
    - Exact: priced at the exact quantity requested.
    - MinimumOrderQuantity: rounded up to the part's MOQ.
    - MaxOrderQuantity: rounded down to the part's max.
    - BetterValue: rounded up to a manufacturer standard package when
      the total is cheaper than the exact quantity.

    Args:
        product_number: Manufacturer or DigiKey part number.
        requested_quantity: Quantity to price.
        manufacturer_id: Disambiguates manufacturer part numbers that
            map to multiple manufacturers.
        customer_id: Customer ID for MyPricing (default "0").
    """
    params = {"manufacturerId": manufacturer_id} if manufacturer_id else None
    return _get_client().request(
        "GET",
        f"/products/v4/search/{product_number}/pricingbyquantity/{requested_quantity}",
        customer_id=customer_id,
        params=params,
    )


@mcp.tool()
def get_digi_reel_pricing(
    product_number: str,
    requested_quantity: int,
    customer_id: str = "0",
) -> dict[str, Any]:
    """Get DigiReel pricing for a product.

    Args:
        product_number: DigiKey product number (must be DigiReel compatible).
        requested_quantity: Quantity for DigiReel pricing.
        customer_id: Customer ID for pricing (default "0").
    """
    return _get_client().request(
        "GET",
        f"/products/v4/search/{product_number}/digireelpricing",
        customer_id=customer_id,
        params={"requestedQuantity": requested_quantity},
    )


def main() -> None:
    # MCP stdio transport reserves stdout for JSON-RPC; force all logging to stderr.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logger.info("Starting DigiKey MCP server")
    mcp.run()


if __name__ == "__main__":
    main()
