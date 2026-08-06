from collections import defaultdict
from statistics import median

from divar_service.comparison import build_comparison_key


def group_ads(ads):
    groups = defaultdict(list)

    for ad in ads:
        key = build_comparison_key(ad)
        groups[key].append(ad)

    return groups


def compute_market_price(groups):
    market = {}

    for key, ads in groups.items():
        prices = [a.price for a in ads if a.price]

        if len(prices) < 3:
            continue

        market[key] = median(prices)

    return market
