"""Validated contracts for read-only public marketplace research."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


LIVE_SOURCE_TYPE = "LIVE"
FALLBACK_SOURCE_TYPE = "FALLBACK"


class MarketplaceSourceError(RuntimeError):
    """Base error for marketplace evidence retrieval failures."""


class MarketplaceConfigurationError(MarketplaceSourceError):
    """Raised before networking when provider configuration is unavailable."""


class MarketplaceAuthenticationError(MarketplaceSourceError):
    """Raised when an official marketplace rejects provider credentials."""


class MarketplaceRateLimitError(MarketplaceSourceError):
    """Raised after bounded retries when an official API remains rate limited."""


class MarketplaceNetworkError(MarketplaceSourceError):
    """Raised for timeouts and transport failures."""


class MarketplaceResponseError(MarketplaceSourceError):
    """Raised for server errors or malformed official API responses."""


class UnsupportedMarketplaceError(MarketplaceSourceError):
    """Raised when no approved official provider exists for a marketplace."""


class PriceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: str
    currency: str = Field(min_length=3, max_length=3)
    display: str


class ImageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    alt_text: str | None = None

    @field_validator("url")
    @classmethod
    def require_https(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("image references must use HTTPS")
        return value


class PopularitySignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    value: int | float | str
    scope: Literal["LISTING", "SHOP", "SEARCH"]
    description: str = Field(min_length=1)


class MarketplaceEvidence(BaseModel):
    """Normalized public evidence; popularity values are signals, not sales facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    marketplace: str
    listing_id: str
    title: str
    url: str
    price: PriceEvidence | None = None
    shop_id: str | None = None
    shop_name: str | None = None
    review_count: int | None = Field(default=None, ge=0)
    review_scope: Literal["LISTING", "SHOP"] | None = None
    popularity_signals: list[PopularitySignal] = Field(default_factory=list)
    image_references: list[ImageReference] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source: str
    source_url: str
    source_type: Literal["LIVE", "FALLBACK"]
    collected_at: datetime
    search_result_count: int | None = Field(default=None, ge=0)

    @field_validator("url", "source_url")
    @classmethod
    def require_https_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("marketplace URLs must use HTTPS")
        return value

    @field_validator("collected_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        price_display = self.price.display if self.price else None
        signal_text = "; ".join(
            f"{signal.name}={signal.value} ({signal.scope.lower()} signal; not verified sales)"
            for signal in self.popularity_signals
        )
        details = [self.title]
        if price_display:
            details.append(f"Public price: {price_display}")
        if self.review_count is not None and self.review_scope:
            details.append(
                f"Public review count: {self.review_count} ({self.review_scope.lower()} scope)"
            )
        if signal_text:
            details.append(signal_text)
        data.update(
            {
                "content": " | ".join(details),
                "metadata": {
                    "price_range": price_display or "Unavailable from public response",
                    "competitor_count_estimate": (
                        f"{self.search_result_count} matching active listings reported by Etsy"
                        if self.search_result_count is not None
                        else "Unavailable from public response"
                    ),
                    "popularity_disclaimer": (
                        "Public popularity indicators are estimates/signals and are not "
                        "verified listing sales."
                    ),
                },
            }
        )
        return data


class MarketplaceSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marketplace: str
    query: str
    evidence: list[MarketplaceEvidence] = Field(default_factory=list)
    total_available: int | None = Field(default=None, ge=0)
    retrieved_at: datetime
    source_type: Literal["LIVE", "FALLBACK"]

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value.astimezone(timezone.utc)


class EvidenceBatch(list[dict[str, Any]]):
    """List-compatible evidence with explicit successful-retrieval metadata."""

    def __init__(self, result: MarketplaceSearchResult) -> None:
        super().__init__(item.to_dict() for item in result.evidence)
        self.retrieval_succeeded = True
        self.marketplace = result.marketplace
        self.query = result.query
        self.total_available = result.total_available
        self.retrieved_at = result.retrieved_at
        self.source_type = result.source_type


class MarketplaceProvider(ABC):
    marketplace_id = ""
    source_type = LIVE_SOURCE_TYPE
    requires_network = True

    @abstractmethod
    def search(self, query: str, limit: int) -> MarketplaceSearchResult:
        """Retrieve public marketplace evidence using an official read-only API."""
