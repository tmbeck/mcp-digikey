"""DigiKey Product Search v4 MCP server."""

import argparse
import logging
import os
import sys
import threading
import time
import urllib.parse
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


def _qp(value: Any) -> str:
    """URL-encode a value for safe inclusion in a path segment.

    Manufacturer part numbers can legitimately contain `/`, `?`, `#`, etc.
    Stringify-then-percent-encode so user input never escapes its path segment.
    """
    return urllib.parse.quote(str(value), safe="")


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
        api_timeout: float = 30.0,
        oauth_timeout: float = 15.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = SANDBOX_HOST if use_sandbox else PROD_HOST
        self.token_url = f"{self.api_base}/v1/oauth2/token"
        self.locale = locale or Locale()
        self.api_timeout = api_timeout
        self.oauth_timeout = oauth_timeout
        self._session = requests.Session()
        self._session.mount("https://", _build_retry_adapter())
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = threading.Lock()

    # Refresh `_TOKEN_REFRESH_SKEW_SEC` seconds before expiry so we never hand
    # out a token that expires mid-flight.
    _TOKEN_REFRESH_SKEW_SEC = 60

    def _redact(self, text: str) -> str:
        """Strip client_id / client_secret substrings before logging.

        DigiKey *probably* doesn't echo secrets back in error bodies, but if
        any future endpoint ever does (or our own request payload ends up in
        a traceback) this prevents the secret from landing in logs.
        """
        for secret in (self.client_id, self.client_secret):
            if secret and len(secret) >= 8:
                text = text.replace(secret, "***")
        return text

    def _fetch_token(self) -> None:
        env = "SANDBOX" if self.api_base == SANDBOX_HOST else "PRODUCTION"
        logger.info("Requesting OAuth token from %s", env)
        resp = self._session.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.oauth_timeout,
        )
        if resp.status_code != 200:
            detail = self._redact(resp.text[:200]) if resp.text else f"HTTP {resp.status_code}"
            logger.error("OAuth error %s: %s", resp.status_code, detail)
            raise DigiKeyAPIError(
                f"DigiKey OAuth failed ({resp.status_code}): {detail}. "
                "Check CLIENT_ID/CLIENT_SECRET and USE_SANDBOX."
            )
        payload = resp.json()
        # DigiKey returns expires_in in seconds (typically ~600). Fall back to a
        # conservative 5min if missing.
        expires_in = int(payload.get("expires_in", 300))
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + expires_in - self._TOKEN_REFRESH_SKEW_SEC
        logger.debug(
            "Token cached for %ds (refresh skew %ds)",
            expires_in,
            self._TOKEN_REFRESH_SKEW_SEC,
        )

    def _token_value(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if (
                force_refresh
                or self._token is None
                or time.monotonic() >= self._token_expires_at
            ):
                self._fetch_token()
            return self._token  # type: ignore[return-value]

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
                timeout=self.api_timeout,
            )
            if resp.status_code == 401 and attempt == 0:
                logger.info("Got 401 on %s %s, refreshing token and retrying once", method, path)
                continue
            if resp.status_code != 200:
                detail = self._redact(resp.text[:200]) if resp.text else f"HTTP {resp.status_code}"
                logger.error("DigiKey %s %s -> %s: %s", method, path, resp.status_code, detail)
                raise DigiKeyAPIError(
                    f"DigiKey API {resp.status_code} on {method} {path}: {detail}"
                )
            payload: dict[str, Any] = resp.json()
            return payload
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
        api_timeout=float(os.getenv("DIGIKEY_TIMEOUT_SEC", "30")),
        oauth_timeout=float(os.getenv("DIGIKEY_OAUTH_TIMEOUT_SEC", "15")),
    )


mcp = FastMCP("DigiKey MCP Server")
_client: DigiKeyClient | None = None


def _get_client() -> DigiKeyClient:
    global _client
    if _client is None:
        _client = _build_client_from_env()
    return _client


# Per swagger: keyword_search's FilterOptionsRequest.SearchOptions enum.
KEYWORD_SEARCH_OPTIONS: frozenset[str] = frozenset({
    "ChipOutpost",
    "Has3DModel",
    "HasCadModel",
    "HasDatasheet",
    "HasProductPhoto",
    "InStock",
    "NewProduct",
    "NonRohsCompliant",
    "NormallyStocking",
    "RohsCompliant",
})

# Per swagger: /recommendedproducts uses a different enum.
RECOMMENDED_PRODUCTS_OPTIONS: frozenset[str] = frozenset({
    "LeadFree",
    "CollapsePackingTypes",
    "ExcludeNonStock",
    "Has3DModel",
    "InStock",
    "ManufacturerPartSearch",
    "NewProductsOnly",
    "RoHSCompliant",
})


def _parse_search_options(raw: str | None, allowed: frozenset[str], param_name: str) -> list[str]:
    if not raw:
        return []
    values = [v.strip() for v in raw.split(",") if v.strip()]
    unknown = [v for v in values if v not in allowed]
    if unknown:
        raise ValueError(
            f"Unknown {param_name} value(s): {unknown}. "
            f"Allowed: {sorted(allowed)}"
        )
    return values


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
    parsed_options = _parse_search_options(search_options, KEYWORD_SEARCH_OPTIONS, "search_options")
    if parsed_options:
        filter_options["SearchOptions"] = parsed_options
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
        f"/products/v4/search/{_qp(product_number)}/productdetails",
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
    return _get_client().request("GET", f"/products/v4/search/categories/{_qp(category_id)}")


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
        f"/products/v4/search/{_qp(product_number)}/substitutions",
        params=params,
    )


@mcp.tool()
def get_alternate_packaging(product_number: str) -> dict[str, Any]:
    """Get alternate packaging options for a product (e.g. tape-and-reel vs cut tape).

    Works best with a DigiKey product number.

    Args:
        product_number: The product to look up.
    """
    return _get_client().request(
        "GET",
        f"/products/v4/search/{_qp(product_number)}/alternatepackaging",
    )


@mcp.tool()
def get_product_associations(product_number: str) -> dict[str, Any]:
    """Get associated products for a part (eval boards, mating connectors, accessories).

    Works best with a DigiKey product number.

    Args:
        product_number: The product to look up.
    """
    return _get_client().request(
        "GET",
        f"/products/v4/search/{_qp(product_number)}/associations",
    )


@mcp.tool()
def get_recommended_products(
    product_number: str,
    limit: int = 1,
    search_options: str | None = None,
    exclude_marketplace: bool = False,
) -> dict[str, Any]:
    """Get recommended products (DigiKey's 'you might also like') for a part.

    Args:
        product_number: The product to look up.
        limit: Max recommendations (default 1).
        search_options: Comma-delimited filters from a different enum than
            keyword_search: LeadFree, CollapsePackingTypes, ExcludeNonStock,
            Has3DModel, InStock, ManufacturerPartSearch, NewProductsOnly,
            RoHSCompliant.
        exclude_marketplace: Exclude marketplace products.
    """
    parsed_options = _parse_search_options(
        search_options, RECOMMENDED_PRODUCTS_OPTIONS, "search_options"
    )
    params: dict[str, Any] = {"limit": limit, "excludeMarketPlaceProducts": exclude_marketplace}
    if parsed_options:
        params["searchOptionList"] = ",".join(parsed_options)
    return _get_client().request(
        "GET",
        f"/products/v4/search/{_qp(product_number)}/recommendedproducts",
        params=params,
    )


@mcp.tool()
def get_product_media(product_number: str) -> dict[str, Any]:
    """Get media (images, datasheets, videos) for a product.

    Args:
        product_number: The product to get media for.
    """
    return _get_client().request("GET", f"/products/v4/search/{_qp(product_number)}/media")


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
        f"/products/v4/search/{_qp(product_number)}/pricing",
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
        f"/products/v4/search/{_qp(product_number)}/pricingbyquantity/{_qp(requested_quantity)}",
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
        f"/products/v4/search/{_qp(product_number)}/digireelpricing",
        customer_id=customer_id,
        params={"requestedQuantity": requested_quantity},
    )


def _check_credentials() -> int:
    """Verify CLIENT_ID/SECRET work by fetching a token + calling /manufacturers.

    Returns the exit code (0 = OK, 1 = failure). Prints a one-line summary.
    """
    try:
        client = _build_client_from_env()
        client._token_value()  # force token fetch
        result = client.request("GET", "/products/v4/search/manufacturers")
    except Exception as exc:  # noqa: BLE001 — surfacing any failure is the point
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    count = len(result.get("Manufacturers", [])) if isinstance(result, dict) else 0
    env = "SANDBOX" if client.api_base == SANDBOX_HOST else "PRODUCTION"
    print(f"OK: {env} credentials valid; {count} manufacturers reachable.")
    return 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digikey-mcp",
        description="MCP server for DigiKey's Product Search v4 API.",
    )
    parser.add_argument(
        "--check-credentials",
        action="store_true",
        help="Verify CLIENT_ID/SECRET (via a one-shot OAuth + /manufacturers call) and exit.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=os.getenv("DIGIKEY_MCP_TRANSPORT", "stdio"),
        help="Transport mode (default: stdio; env DIGIKEY_MCP_TRANSPORT).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("DIGIKEY_MCP_HOST", "127.0.0.1"),
        help="Bind host for --transport http (default: 127.0.0.1; env DIGIKEY_MCP_HOST).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("DIGIKEY_MCP_PORT", "8000")),
        help="Bind port for --transport http (default: 8000; env DIGIKEY_MCP_PORT).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    # stdio transport reserves stdout for JSON-RPC; force logging to stderr.
    # http transport doesn't share stdout but stderr-only is still the safe default.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
        force=True,
    )
    if args.check_credentials:
        return _check_credentials()
    if args.transport == "http":
        logger.info("Starting DigiKey MCP server on http://%s:%d", args.host, args.port)
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        logger.info("Starting DigiKey MCP server on stdio")
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
