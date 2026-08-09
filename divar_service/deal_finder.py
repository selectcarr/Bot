from __future__ import annotations

from collections import defaultdict
from statistics import median
import re

from divar_service.models import VehicleAd


# -----------------------------
# تنظیمات
# -----------------------------
MAX_PRICE_RATIO = 0.85
MIN_GROUP_SIZE = 3
MAX_MILEAGE_DIFF = 10000


# -----------------------------
# استخراج trim از title
# -----------------------------
def extract_trim(title: str):
    if not title:
        return ""

    title = title.lower()

    # مثال ساده (قابل توسعه)
    patterns = [
        "tipe 2", "tipe 3", "type 2", "type 3",
        "v8", "v6", "se", "le"
    ]

    for p in patterns:
        if p in title:
            return p

    return ""


# -----------------------------
# ساخت comparison key (اصلاح شده)
# -----------------------------
def build_comparison_key(ad: VehicleAd) -> tuple:
    return (
        (ad.brand or "").strip().lower(),
        (ad.model or "").strip().lower(),
        ad.year,
        extract_trim(ad.title),   # ✅ اضافه شد
    )


# -----------------------------
# گروه‌بندی
# -----------------------------
def group_ads(ads: list[VehicleAd]):
    groups = defaultdict(list)

    for ad in ads:
        if not ad.price or not ad.year:
            continue  # ✅ جلوگیری از دیتای خراب

        key = build_comparison_key(ad)
        groups[key].append(ad)

    return groups


# -----------------------------
# فیلتر mileage + sanity check
# -----------------------------
def filter_by_mileage(base_ad: VehicleAd, ads: list[VehicleAd]):
    result = []

    if base_ad.mileage is None:
        return []

    for ad in ads:
        if ad.mileage is None:
            continue

        # حذف داده‌های غیرواقعی
        if ad.mileage < 0 or ad.mileage > 500_000:
            continue

        if abs(ad.mileage - base_ad.mileage) <= MAX_MILEAGE_DIFF:
            result.append(ad)

    return result


# -----------------------------
# حذف outlier قیمتی
# -----------------------------
def remove_price_outliers(prices: list[int]):
    if len(prices) < 3:
        return prices

    med = median(prices)

    filtered = [
        p for p in prices
        if 0.5 * med <= p <= 1.5 * med
    ]

    return filtered if len(filtered) >= 3 else prices


# -----------------------------
# پیدا کردن دیل
# -----------------------------
def find_deals(ads: list[VehicleAd]) -> list[VehicleAd]:
    deals = []

    groups = group_ads(ads)

    for key, group in groups.items():
        if len(group) < MIN_GROUP_SIZE:
            continue

        for ad in group:
            if not ad.price or not ad.mileage:
                continue

            similar_ads = filter_by_mileage(ad, group)

            if len(similar_ads) < MIN_GROUP_SIZE:
                continue

            prices = [a.price for a in similar_ads if a.price]

            prices = remove_price_outliers(prices)

            if len(prices) < MIN_GROUP_SIZE:
                continue

            market_price = median(prices)

            if ad.price < market_price * MAX_PRICE_RATIO:
                deals.append(ad)

    return deals
