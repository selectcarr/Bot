from __future__ import annotations

from collections import defaultdict
from statistics import median

from divar_service.comparison import build_comparison_key


MIN_GROUP_SIZE = 3
MAX_MILEAGE_DIFF = 10_000


def group_ads(ads):
    """
    Group advertisements by exact vehicle identity.

    Identity is determined by:
    brand + model + trim + year
    """

    groups = defaultdict(list)

    for ad in ads:
        key = build_comparison_key(ad)
        groups[key].append(ad)

    return groups


def filter_by_mileage(
    base_ad,
    ads,
):
    """
    Keep only advertisements whose mileage is within
    ±10,000 km of the base advertisement.
    """

    if base_ad.mileage is None:
        return []

    result = []

    for ad in ads:
        if ad.mileage is None:
            continue

        if (
            abs(ad.mileage - base_ad.mileage)
            <= MAX_MILEAGE_DIFF
        ):
            result.append(ad)

    return result


def compute_market_price(
    groups,
):
    """
    Calculate the local market median for each advertisement.

    The market price is calculated separately for each ad
    using only comparable vehicles with mileage within
    ±10,000 km.

    Returns:
        dict[ad_id, market_price]
    """

    market = {}

    for key, ads in groups.items():

        if len(ads) < MIN_GROUP_SIZE:
            continue

        for base_ad in ads:

            comparable_ads = filter_by_mileage(
                base_ad,
                ads,
            )

            if len(comparable_ads) < MIN_GROUP_SIZE:
                continue

            prices = [
                ad.price
                for ad in comparable_ads
                if ad.price and ad.price > 0
            ]

            if len(prices) < MIN_GROUP_SIZE:
                continue

            market_price = median(prices)

            market[base_ad.ad_id] = int(
                round(market_price)
            )

    return market
