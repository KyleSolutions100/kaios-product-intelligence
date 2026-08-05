from typing import List, Dict, Any

from .sources import build_sources


class Extractor:
    def __init__(self, marketplace: str, limit: int = 12):
        self.marketplace = marketplace
        self.limit = limit

    def gather(self, seed: str) -> List[Dict[str, Any]]:
        sources = build_sources(self.marketplace, self.limit)
        snippets: List[Dict[str, Any]] = []
        for source in sources:
            for item in source.search(seed, self.limit):
                snippets.append(item.to_dict())
        return snippets
