from __future__ import annotations


def build_comparison_key(ad) -> tuple[str, str, str, int]:
    """
    Exact vehicle identity used for price comparison.

    Mileage is intentionally NOT part of this key.
    Mileage similarity is handled separately in pricing.py
    using the ±10,000 km rule.
    """

    return (
        (ad.brand or "").strip().lower(),
        (ad.model or "").strip().lower(),
        (ad.trim or "").strip().lower(),
        ad.year,
    )
