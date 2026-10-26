"""Product catalog loader and fuzzy matcher for Kirana items.

Loads items from data/items.csv and provides matching against handwritten
or vernacular transaction lines.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.models import CatalogProduct


class CatalogManager:
    """Manages the canonical Kirana grocery product catalog."""

    def __init__(self, csv_path: Optional[Path] = None) -> None:
        if csv_path is None:
            csv_path = Path(__file__).resolve().parent.parent / "data" / "items.csv"
        self.csv_path = csv_path
        self._items: List[CatalogProduct] = []
        self._by_id: Dict[int, CatalogProduct] = {}
        self.load_catalog()

    def load_catalog(self) -> None:
        """Loads canonical products from the CSV catalog."""
        if not self.csv_path.exists():
            return

        self._items.clear()
        self._by_id.clear()

        with open(self.csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    product = CatalogProduct(
                        item_id=int(row["item_id"]),
                        name_en=row["name_en"].strip(),
                        name_kn=row["name_kn"].strip(),
                        name_hi=row["name_hi"].strip(),
                        category=row["category"].strip(),
                        default_unit=row["default_unit"].strip(),
                        standard_price_inr=float(row["standard_price_inr"]),
                    )
                    self._items.append(product)
                    self._by_id[product.item_id] = product
                except (ValueError, KeyError):
                    continue

    def get_all_items(self) -> List[CatalogProduct]:
        """Returns all loaded catalog items."""
        return list(self._items)

    def get_by_id(self, item_id: int) -> Optional[CatalogProduct]:
        """Retrieves an item by its numerical ID."""
        return self._by_id.get(item_id)

    def find_match(
        self, query: str, threshold: float = 75.0
    ) -> Optional[Tuple[CatalogProduct, float]]:
        """Finds closest matching catalog product using RapidFuzz or fallback."""
        if not query or not query.strip() or not self._items:
            return None

        query_clean = query.strip().lower()

        # Try RapidFuzz if installed
        try:
            from rapidfuzz import fuzz

            best_match: Optional[CatalogProduct] = None
            best_score = 0.0

            for item in self._items:
                candidates = [
                    item.name_en.lower(),
                    item.name_kn.lower(),
                    item.name_hi.lower(),
                ]
                for cand in candidates:
                    score = fuzz.token_sort_ratio(query_clean, cand)
                    if score > best_score:
                        best_score = score
                        best_match = item

            if best_score >= threshold and best_match is not None:
                return best_match, best_score
            return None

        except ImportError:
            # Fallback simple substring/exact matching
            for item in self._items:
                if (
                    query_clean in item.name_en.lower()
                    or query_clean in item.name_kn.lower()
                    or query_clean in item.name_hi.lower()
                ):
                    return item, 100.0
            return None


_GLOBAL_CATALOG: Optional[CatalogManager] = None


def get_catalog_manager() -> CatalogManager:
    """Returns singleton CatalogManager instance."""
    global _GLOBAL_CATALOG
    if _GLOBAL_CATALOG is None:
        _GLOBAL_CATALOG = CatalogManager()
    return _GLOBAL_CATALOG
