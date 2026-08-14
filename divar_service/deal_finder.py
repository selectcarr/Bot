from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from divar_service.comparison import (
    build_comparison_key,
)
from divar_service.config.vehicle_catalog import (
    get_trims,
    is_valid_vehicle,
)
from divar_service.models import (
    DealCandidate,
    VehicleAd,
)


def group_ads(
    ads: Iterable[VehicleAd],
) -> dict[
    tuple[str, str, str, int],
    list[VehicleAd],
]:
    groups: dict[
        tuple[str, str, str, int],
        list[VehicleAd],
    ] = defaultdict(list)

    for ad in ads:
        if not _is_eligible_ad(ad):
            continue

        groups[
            build_comparison_key(ad)
        ].append(ad)

    return dict(groups)


def calc_diff_percent(
    price: int,
    market_average: float,
) -> float:
    if market_average <= 0:
        return 0.0

    return (
        (market_average - price)
        / market_average
        * 100
    )


def find_deals(
    ads: Iterable[VehicleAd],
    *,
    min_sample_count: int = 3,
    min_deal_percent: float = 2.0,
    max_deal_percent: float = 10.0,
) -> list[DealCandidate]:
    """
    Find one candidate at most from each exact vehicle group.

    Business rules:

    - Group by brand + model + trim + year.
    - Count each ad_id once.
    - Require at least min_sample_count unique advertisements.
    - Use arithmetic average of all valid prices.
    - Evaluate only the cheapest advertisement in the group.
    - Accept discounts inclusively between the configured
      minimum and maximum percentages.
    """
    if min_sample_count < 3:
        raise ValueError(
            "min_sample_count cannot be less than 3."
        )

    if not (
        0
        <= min_deal_percent
        < max_deal_percent
        <= 100
    ):
        raise ValueError(
            "Deal percentage limits are invalid."
        )

    deals: list[DealCandidate] = []

    for group in group_ads(ads).values():
        unique_ads = _deduplicate_by_ad_id(
            group
        )

        if len(unique_ads) < min_sample_count:
            continue

        prices = [
            ad.price
            for ad in unique_ads
            if ad.price > 0
        ]

        if len(prices) < min_sample_count:
            continue

        market_average = (
            sum(prices)
            / len(prices)
        )

        if market_average <= 0:
            continue

        cheapest_ad = min(
            unique_ads,
            key=lambda ad: (
                ad.price,
                ad.ad_id,
            ),
        )

        diff_percent = calc_diff_percent(
            cheapest_ad.price,
            market_average,
        )

        if diff_percent < min_deal_percent:
            continue

        if diff_percent > max_deal_percent:
            continue

        deals.append(
            DealCandidate(
                ad=cheapest_ad,
                market_average=int(
                    round(market_average)
                ),
                diff_percent=round(
                    diff_percent,
                    2,
                ),
                sample_count=len(
                    unique_ads
                ),
            )
        )

    deals.sort(
        key=lambda deal: (
            -deal.diff_percent,
            deal.ad.price,
            deal.ad.ad_id,
        )
    )

    return deals


def _deduplicate_by_ad_id(
    ads: Iterable[VehicleAd],
) -> list[VehicleAd]:
    unique_ads: dict[str, VehicleAd] = {}

    for ad in ads:
        # Repository data is normally unique already. Assignment
        # keeps the last occurrence if a caller passes duplicates.
        unique_ads[ad.ad_id] = ad

    return list(
        unique_ads.values()
    )


def _is_eligible_ad(
    ad: VehicleAd,
) -> bool:
    if not ad.ad_id.strip():
        return False

    if ad.price <= 0:
        return False

    available_trims = get_trims(
        ad.brand,
        ad.model,
    )

    if available_trims and not ad.trim:
        return False

    return is_valid_vehicle(
        brand=ad.brand,
        model=ad.model,
        trim=ad.trim,
    )
