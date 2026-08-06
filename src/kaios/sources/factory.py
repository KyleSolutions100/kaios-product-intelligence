"""Factory for approved official marketplace providers."""

from __future__ import annotations

import os
from collections.abc import Callable

from .base import MarketplaceProvider, UnsupportedMarketplaceError
from .etsy import EtsyProvider


ProviderBuilder = Callable[[], MarketplaceProvider]


def create_marketplace_provider(
    marketplace: str,
    *,
    etsy_api_key: str | None = None,
) -> MarketplaceProvider:
    normalized = marketplace.strip().lower()
    if normalized == "etsy":
        key = etsy_api_key if etsy_api_key is not None else os.getenv("ETSY_API_KEY")
        return EtsyProvider(key)
    raise UnsupportedMarketplaceError(
        f"no approved official read-only provider is configured for: {marketplace}"
    )


def validate_marketplace_configuration(marketplace: str) -> None:
    """Fail before runtime/database initialization when configuration is missing."""

    create_marketplace_provider(marketplace)

