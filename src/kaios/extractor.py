"""Marketplace evidence extraction through approved official providers."""

from __future__ import annotations

from collections.abc import Callable

from .sources import EvidenceBatch, MarketplaceProvider, create_marketplace_provider


ProviderFactory = Callable[[str], MarketplaceProvider]


class Extractor:
    def __init__(
        self,
        marketplace: str,
        limit: int = 12,
        *,
        provider_factory: ProviderFactory = create_marketplace_provider,
    ) -> None:
        self.marketplace = marketplace
        self.limit = limit
        self._provider_factory = provider_factory

    def gather(self, seed: str) -> EvidenceBatch:
        provider = self._provider_factory(self.marketplace)
        return EvidenceBatch(provider.search(seed, self.limit))
