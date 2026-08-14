from __future__ import annotations

from divar_service.normalizer import (
    build_vehicle_key,
)


def build_comparison_key(
    ad,
) -> tuple[str, str, str, int]:
    """
    Build the exact vehicle identity used for market comparison:

        brand + model + trim + year

    Mileage is intentionally not part of the comparison key.
    """
    return build_vehicle_key(
        brand=ad.brand,
        model=ad.model,
        trim=ad.trim,
        year=ad.year,
    )
