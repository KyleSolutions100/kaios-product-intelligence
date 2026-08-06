"""Official, read-only Etsy Open API marketplace provider."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .base import (
    ImageReference,
    LIVE_SOURCE_TYPE,
    MarketplaceAuthenticationError,
    MarketplaceConfigurationError,
    MarketplaceEvidence,
    MarketplaceNetworkError,
    MarketplaceProvider,
    MarketplaceRateLimitError,
    MarketplaceResponseError,
    MarketplaceSearchResult,
    PopularitySignal,
    PriceEvidence,
)


ETSY_API_BASE_URL = "https://openapi.etsy.com"
ETSY_ACTIVE_LISTINGS_PATH = "/v3/application/listings/active"
ETSY_ACTIVE_LISTINGS_URL = f"{ETSY_API_BASE_URL}{ETSY_ACTIVE_LISTINGS_PATH}"
ETSY_SOURCE_NAME = "Etsy Open API v3"


class EtsyProvider(MarketplaceProvider):
    marketplace_id = "etsy"
    source_type = LIVE_SOURCE_TYPE
    requires_network = True

    def __init__(
        self,
        api_key: str | None,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        normalized_key = (api_key or "").strip()
        if not normalized_key:
            raise MarketplaceConfigurationError(
                "ETSY_API_KEY is required for official live Etsy research"
            )
        if max_retries < 0 or max_retries > 5:
            raise ValueError("max_retries must be between 0 and 5")
        self._api_key = normalized_key
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(timezone.utc))

    def search(self, query: str, limit: int) -> MarketplaceSearchResult:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("Etsy search query must not be empty")
        if limit < 1 or limit > 100:
            raise ValueError("Etsy result limit must be between 1 and 100")

        response = self._request(
            ETSY_ACTIVE_LISTINGS_URL,
            params={
                "keywords": normalized_query,
                "limit": limit,
                "offset": 0,
                "includes": "Shop,Images",
            },
        )
        data = self._decode_response(response)
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise MarketplaceResponseError(
                "Etsy returned a malformed response: results must be a list"
            )
        total_available = _optional_nonnegative_int(data.get("count"))
        collected_at = self._utc_now()
        evidence: list[MarketplaceEvidence] = []
        seen_listing_ids: set[str] = set()
        for raw_item in raw_results:
            if not isinstance(raw_item, dict):
                raise MarketplaceResponseError(
                    "Etsy returned a malformed listing record"
                )
            item = self._normalize_listing(
                raw_item,
                collected_at=collected_at,
                total_available=total_available,
            )
            if item.listing_id in seen_listing_ids:
                continue
            seen_listing_ids.add(item.listing_id)
            evidence.append(item)
            if len(evidence) >= limit:
                break

        return MarketplaceSearchResult(
            marketplace=self.marketplace_id,
            query=normalized_query,
            evidence=evidence,
            total_available=total_available,
            retrieved_at=collected_at,
            source_type=self.source_type,
        )

    def _request(self, url: str, *, params: dict[str, Any]) -> httpx.Response:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "openapi.etsy.com"
            or parsed.path != ETSY_ACTIVE_LISTINGS_PATH
        ):
            raise MarketplaceConfigurationError(
                "blocked non-allowlisted Etsy API request"
            )

        for attempt in range(self._max_retries + 1):
            try:
                if self._client is not None:
                    response = self._client.get(
                        url,
                        params=params,
                        headers={"x-api-key": self._api_key},
                        timeout=self._timeout_seconds,
                    )
                else:
                    with httpx.Client(
                        timeout=self._timeout_seconds,
                        follow_redirects=False,
                    ) as client:
                        response = client.get(
                            url,
                            params=params,
                            headers={"x-api-key": self._api_key},
                        )
            except httpx.TimeoutException as error:
                if attempt < self._max_retries:
                    self._sleep(_retry_delay(attempt))
                    continue
                raise MarketplaceNetworkError(
                    "Etsy request timed out after bounded retries"
                ) from error
            except httpx.RequestError as error:
                if attempt < self._max_retries:
                    self._sleep(_retry_delay(attempt))
                    continue
                raise MarketplaceNetworkError(
                    "Etsy network request failed after bounded retries"
                ) from error

            if response.status_code in (401, 403):
                raise MarketplaceAuthenticationError(
                    "Etsy rejected the application credentials or endpoint permission"
                )
            if response.status_code == 429:
                if attempt < self._max_retries:
                    self._sleep(_retry_after_seconds(response, attempt))
                    continue
                raise MarketplaceRateLimitError(
                    "Etsy rate limit remained active after bounded retries"
                )
            if response.status_code >= 500:
                if attempt < self._max_retries:
                    self._sleep(_retry_delay(attempt))
                    continue
                raise MarketplaceResponseError(
                    f"Etsy server failed after bounded retries (HTTP {response.status_code})"
                )
            if response.status_code >= 400:
                raise MarketplaceResponseError(
                    f"Etsy request failed (HTTP {response.status_code})"
                )
            return response
        raise MarketplaceResponseError("Etsy request failed unexpectedly")

    def _decode_response(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except (ValueError, TypeError) as error:
            raise MarketplaceResponseError(
                "Etsy returned malformed JSON"
            ) from error
        if not isinstance(data, dict):
            raise MarketplaceResponseError(
                "Etsy returned a malformed response: expected an object"
            )
        return data

    def _normalize_listing(
        self,
        item: dict[str, Any],
        *,
        collected_at: datetime,
        total_available: int | None,
    ) -> MarketplaceEvidence:
        listing_id = str(item.get("listing_id") or "").strip()
        title = str(item.get("title") or "").strip()
        listing_url = str(item.get("url") or "").strip()
        if not listing_id or not title or not listing_url:
            raise MarketplaceResponseError(
                "Etsy listing is missing listing_id, title, or URL"
            )
        _require_public_etsy_url(listing_url)

        shop = _first_mapping(item.get("shop"), item.get("Shop"))
        images = _mapping_list(item.get("images") or item.get("Images"))
        price = _normalize_price(item.get("price"))
        shop_id = _optional_string(
            (shop or {}).get("shop_id") or item.get("shop_id")
        )
        shop_name = _optional_string((shop or {}).get("shop_name"))
        review_count = _optional_nonnegative_int((shop or {}).get("review_count"))

        signals: list[PopularitySignal] = []
        favorites = _optional_nonnegative_int(item.get("num_favorers"))
        if favorites is not None:
            signals.append(
                PopularitySignal(
                    name="listing_favorites",
                    value=favorites,
                    scope="LISTING",
                    description=(
                        "Public Etsy favourites count; a popularity indicator, not "
                        "verified sales."
                    ),
                )
            )
        shop_sales = _optional_nonnegative_int(
            (shop or {}).get("transaction_sold_count")
        )
        if shop_sales is not None:
            signals.append(
                PopularitySignal(
                    name="shop_transaction_count",
                    value=shop_sales,
                    scope="SHOP",
                    description=(
                        "Public historical shop transaction count; shop-level context, "
                        "not verified sales for this listing."
                    ),
                )
            )

        image_references: list[ImageReference] = []
        seen_image_urls: set[str] = set()
        for image in images:
            image_url = _first_string(
                image.get("url_fullxfull"),
                image.get("url_570xN"),
                image.get("url_170x135"),
                image.get("url_75x75"),
            )
            if not image_url or image_url in seen_image_urls:
                continue
            if not image_url.startswith("https://"):
                continue
            seen_image_urls.add(image_url)
            image_references.append(
                ImageReference(
                    url=image_url,
                    width=_optional_nonnegative_int(image.get("full_width")),
                    height=_optional_nonnegative_int(image.get("full_height")),
                    alt_text=_optional_string(image.get("alt_text")),
                )
            )

        tags = _string_list(item.get("tags"))
        return MarketplaceEvidence(
            marketplace=self.marketplace_id,
            listing_id=listing_id,
            title=title,
            url=listing_url,
            price=price,
            shop_id=shop_id,
            shop_name=shop_name,
            review_count=review_count,
            review_scope="SHOP" if review_count is not None else None,
            popularity_signals=signals,
            image_references=image_references,
            tags=tags,
            keywords=tags,
            source=ETSY_SOURCE_NAME,
            source_url=listing_url,
            source_type=LIVE_SOURCE_TYPE,
            collected_at=collected_at,
            search_result_count=total_available,
        )

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketplaceConfigurationError(
                "Etsy provider clock must return a timezone-aware timestamp"
            )
        return value.astimezone(timezone.utc)


def _normalize_price(value: Any) -> PriceEvidence | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("amount")
    divisor = value.get("divisor")
    currency = str(value.get("currency_code") or "").upper().strip()
    if amount is None or not currency or len(currency) != 3:
        return None
    try:
        decimal_amount = Decimal(str(amount))
        decimal_divisor = Decimal(str(divisor or 1))
        if decimal_divisor <= 0:
            return None
        normalized = decimal_amount / decimal_divisor
    except (InvalidOperation, ValueError, TypeError):
        return None
    amount_text = format(normalized, "f")
    if "." in amount_text:
        amount_text = amount_text.rstrip("0").rstrip(".")
    return PriceEvidence(
        amount=amount_text,
        currency=currency,
        display=f"{amount_text} {currency}",
    )


def _require_public_etsy_url(value: str) -> None:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        host == "etsy.com" or host.endswith(".etsy.com")
    ):
        raise MarketplaceResponseError(
            "Etsy returned a non-public or unexpected listing URL"
        )


def _first_mapping(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return None


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    output: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_string(*values: Any) -> str | None:
    for value in values:
        text = _optional_string(value)
        if text:
            return text
    return None


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _retry_delay(attempt: int) -> float:
    return min(2.0**attempt, 8.0)


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return min(max(float(raw), 0.0), 60.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return min(max(seconds, 0.0), 60.0)
            except (TypeError, ValueError, OverflowError):
                pass
    return _retry_delay(attempt)

