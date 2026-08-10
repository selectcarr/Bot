from __future__ import annotations

from collections import defaultdict
from statistics import median

from divar_service.models import VehicleAd, DealCandidate


# -----------------------------
# تنظیمات
# -----------------------------
MAX_PRICE_RATIO = 0.85     # زیر 85٪ بازار = دیل
MIN_GROUP_SIZE = 3         # حداقل نمونه برای تحلیل
MAX_MILEAGE_DIFF = 10000  # بازه قابل قبول کارکرد


# -----------------------------
# ساخت کلید مقایسه (نسخه حرفه‌ای)
# -----------------------------
def build_comparison_key(ad: VehicleAd) -> tuple:
    return (
        (ad.brand or "").strip().lower(),
        (ad.model or "").strip().lower(),
        (ad.trim or "").strip().lower(),   # 👈 اضافه شد
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
# فیلتر بر اساس کارکرد
# -----------------------------
def filter_by_mileage(base_ad: VehicleAd, ads: list[VehicleAd]):
    if base_ad.mileage is None:
        return []

    result = []

    for ad in ads:
        if ad.mileage is None:
            continue

        if abs(ad.mileage - base_ad.mileage) <= MAX_MILEAGE_DIFF:
            result.append(ad)

    return result


# -----------------------------
# حذف قیمت‌های پرت (ضد دیل فیک)
# -----------------------------
def remove_outliers(prices: list[int]) -> list[int]:
    if len(prices) < 5:
        return prices

    prices_sorted = sorted(prices)
    q1 = prices_sorted[len(prices)//4]
    q3 = prices_sorted[(len(prices)*3)//4]

    iqr = q3 - q1

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    return [p for p in prices if lower <= p <= upper]


# -----------------------------
# محاسبه درصد اختلاف
# -----------------------------
def calc_diff_percent(price: int, market: int) -> float:
    return ((market - price) / market) * 100


# -----------------------------
# پیدا کردن دیل‌ها (نسخه نهایی)
# -----------------------------
def find_deals(ads: list[VehicleAd]) -> list[DealCandidate]:
    deals: list[DealCandidate] = []

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

            # 👇 حذف قیمت‌های پرت
            prices = remove_outliers(prices)

            if len(prices) < MIN_GROUP_SIZE:
                continue

            market_price = int(median(prices))

            if ad.price >= market_price:
                continue

            if ad.price < market_price * MAX_PRICE_RATIO:

                diff_percent = calc_diff_percent(ad.price, market_price)

                deals.append(
                    DealCandidate(
                        ad=ad,
                        market_average=market_price,
                        diff_percent=diff_percent,
                        sample_count=len(prices),
                    )
                )

    return deals
