import httpx
import os
from abc import ABC, abstractmethod
from typing import List


class EvidenceItem:
    def __init__(self, title, url, price, demand, competition, source_name, source_url, source_type):
        self.title = title
        self.url = url
        self.price = price
        self.demand = demand
        self.competition = competition
        self.source_name = source_name
        self.source_url = source_url
        self.source_type = source_type

    def to_dict(self):
        return {
            "title": self.title,
            "url": self.url,
            "content": f"{self.title} | {self.price} | demand:{self.demand} | competition:{self.competition}",
            "source": self.source_name,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "price": self.price,
            "demand_signal": self.demand,
            "competitor_count_estimate": self.competition,
        }


class BaseSource(ABC):
    source_name = ""
    source_type = ""
    source_url = ""

    @abstractmethod
    def search(self, query, limit):
        ...


class EtsyApiSource(BaseSource):
    source_name = "Etsy API v3"
    source_type = "official_api"
    source_url = "https://developers.etsy.com/"

    def __init__(self, api_key):
        self.api_key = api_key

    def search(self, query, limit):
        if not self.api_key:
            return []
        url = "https://openapi.etsy.com/v3/application/listings/active"
        params = {"q": query, "limit": min(limit, 100)}
        headers = {"x-api-key": self.api_key}
        try:
            with httpx.Client(timeout=20) as client:
                r = client.get(url, params=params, headers=headers)
                data = r.json()
        except Exception:
            return []
        items = data.get("results", []) if isinstance(data, dict) else []
        out = []
        for item in items[:limit]:
            out.append(EvidenceItem(
                title=item.get("title") or "Untitled",
                url=item.get("url") or item.get("canonical_url") or "",
                price="",
                demand=f"listings:{item.get('listing_id_count', 1)} taxons:{','.join([t.get('name','') for t in item.get('taxonomy_path', []) if isinstance(t, dict)])}",
                competition=f"active_listings:{data.get('count', '?')}",
                source_name=self.source_name,
                source_url=item.get("url") or item.get("canonical_url") or "",
                source_type=self.source_type,
            ))
        return out


class WebSearchSource(BaseSource):
    source_name = "Web Search"
    source_type = "compliant_web_search"
    source_url = "https://duckduckgo.com/"

    def __init__(self, marketplace="etsy", limit=12):
        self.marketplace = marketplace
        self.limit = limit

    def search(self, query, limit):
        search_url = f"https://duckduckgo.com/html/?q={self.marketplace}+{query}"
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(search_url, headers={"User-Agent": "Mozilla/5.0"})
                text = resp.text
        except Exception:
            return []
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(text, "html.parser")
        except Exception:
            return []
        links = [a.get("href") for a in soup.select("a[href]") if a.get("href", "").startswith("http")]
        seen = set()
        ordered = []
        for u in links:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        results = []
        for url in ordered[:limit]:
            item = self._extract(url)
            if item:
                results.append(item)
        return results

    def _extract(self, url):
        try:
            with httpx.Client(timeout=20, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                html = resp.text
        except Exception:
            return None
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return None
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        return EvidenceItem(
            title=title or "Untitled",
            url=url,
            price="",
            demand="web sourced evidence",
            competition="web sourced evidence",
            source_name=self.source_name,
            source_url=url,
            source_type=self.source_type,
        )


def build_sources(marketplace, limit):
    sources = []
    api_key = os.getenv("ETSY_API_KEY")
    if marketplace.lower() == "etsy" and api_key:
        sources.append(EtsyApiSource(api_key=api_key))
    sources.append(WebSearchSource(marketplace=marketplace, limit=limit))
    return sources
