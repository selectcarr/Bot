from __future__ import annotations

from collections import defaultdict
from statistics import median

from divar_service.models import VehicleAd


# -----------------------------
# تنظیمات (قابل تنظیم)
# -----------------------------
MAX_PRICE_RATIO = 0.85   # زیر 85٪ بازار = دیل
MIN_GROUP_SIZE = 3       # حداقل تعداد برای تحلیل
MAX_MILEAGE_DIFF = 10000


# -----------------------------
# ساخت کلید مقایسه
# -----------------------------
def build_comparison_key(ad: VehicleAd) -> tuple:
    return (
        (ad.brand or "").strip().lower(),
        (ad.model or "").strip().lower(),
        ad.year,
    )


# -----------------------------
# گروه‌بندی آگهی‌ها
# -----------------------------
def group_ads(ads: list[VehicleAd]):
    groups = defaultdict(list)

    for ad in ads:
        key = build_comparison_key(ad)
        groups[key].append(ad)

    return groups


# -----------------------------
# فیلتر بر اساس کارکرد مشابه
# -----------------------------
def filter_by_mileage(base_ad: VehicleAd, ads: list[VehicleAd]):
    result = []

    for ad in ads:
        if ad.mileage is None or base_ad.mileage is None:
            continue

        if abs(ad.mileage - base_ad.mileage) <= MAX_MILEAGE_DIFF:
            result.append(ad)

    return result


# -----------------------------
# پیدا کردن دیل‌ها
# -----------------------------
def find_deals(ads: list[VehicleAd]) -> list[VehicleAd]:
    deals = []

    groups = group_ads(ads)

    for key, group in groups.items():
        if len(group) < MIN_GROUP_SIZE:
            continue

        for ad in group:
            similar_ads = filter_by_mileage(ad, group)

            if len(similar_ads) < MIN_GROUP_SIZE:
                continue

            prices = [x.price for x in similar_ads if x.price]

            if len(prices) < MIN_GROUP_SIZE:
                continue

            market_price = median(prices)

            if ad.price < market_price * MAX_PRICE_RATIO:
                deals.append(ad)

    return deals
