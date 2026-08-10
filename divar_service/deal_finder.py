from __future__ import annotations

from collections import defaultdict
from statistics import median

from divar_service.comparison import build_comparison_key
from divar_service.models import VehicleAd, DealCandidate


# ============================================================
# تنظیمات تشخیص Deal
# ============================================================

# حداقل درصد ارزان‌تر بودن نسبت به بازار
MIN_DISCOUNT_PERCENT = 15.0

# حداقل تعداد آگهی مشابه برای محاسبه بازار
MIN_GROUP_SIZE = 3

# حداکثر اختلاف کارکرد قابل قبول
MAX_MILEAGE_DIFF = 10_000


# ============================================================
# گروه‌بندی خودروها
# ============================================================

def group_ads(
    ads: list[VehicleAd],
) -> dict[tuple, list[VehicleAd]]:
    groups: dict[tuple, list[VehicleAd]] = defaultdict(list)

    for ad in ads:
        key = build_comparison_key(ad)
        groups[key].append(ad)

    return groups


# ============================================================
# پیدا کردن خودروهای مشابه بر اساس کارکرد
# ============================================================

def filter_by_mileage(
    base_ad: VehicleAd,
    ads: list[VehicleAd],
) -> list[VehicleAd]:

    if base_ad.mileage is None:
        return []

    result: list[VehicleAd] = []

    for ad in ads:
        if ad.mileage is None:
            continue

        if abs(ad.mileage - base_ad.mileage) <= MAX_MILEAGE_DIFF:
            result.append(ad)

    return result


# ============================================================
# حذف قیمت‌های پرت
# ============================================================

def remove_outliers(
    prices: list[int],
) -> list[int]:

    if len(prices) < 5:
        return prices

    ordered = sorted(prices)

    q1_index = len(ordered) // 4
    q3_index = (len(ordered) * 3) // 4

    q1 = ordered[q1_index]
    q3 = ordered[q3_index]

    iqr = q3 - q1

    if iqr == 0:
        return prices

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    return [
        price
        for price in prices
        if lower <= price <= upper
    ]


# ============================================================
# محاسبه درصد اختلاف قیمت
# ============================================================

def calc_diff_percent(
    price: int,
    market_price: int,
) -> float:

    if market_price <= 0:
        return 0.0

    return (
        (market_price - price)
        / market_price
        * 100
    )


# ============================================================
# پیدا کردن Deal
# ============================================================

def find_deals(
    ads: list[VehicleAd],
) -> list[DealCandidate]:

    deals: list[DealCandidate] = []

    groups = group_ads(ads)

    for group in groups.values():

        # برای تشکیل بازار حداقل 3 نمونه لازم است.
        if len(group) < MIN_GROUP_SIZE:
            continue

        for ad in group:

            # فقط خودروهای با کارکرد مشخص
            # قابل مقایسه هستند.
            if ad.mileage is None:
                continue

            similar_ads = filter_by_mileage(
                ad,
                group,
            )

            if len(similar_ads) < MIN_GROUP_SIZE:
                continue

            prices = [
                item.price
                for item in similar_ads
                if item.price > 0
            ]

            if len(prices) < MIN_GROUP_SIZE:
                continue

            # حذف قیمت‌های غیرعادی
            clean_prices = remove_outliers(prices)

            if len(clean_prices) < MIN_GROUP_SIZE:
                continue

            # Median نسبت به Average برای بازار خودرو
            # مقاوم‌تر است.
            market_price = int(
                median(clean_prices)
            )

            if market_price <= 0:
                continue

            # خود آگهی نباید مساوی یا گران‌تر از بازار باشد.
            if ad.price >= market_price:
                continue

            diff_percent = calc_diff_percent(
                ad.price,
                market_price,
            )

            # فقط Deal واقعی‌تر وارد خروجی شود.
            if diff_percent < MIN_DISCOUNT_PERCENT:
                continue

            deals.append(
                DealCandidate(
                    ad=ad,
                    market_average=market_price,
                    diff_percent=round(
                        diff_percent,
                        1,
                    ),
                    sample_count=len(clean_prices),
                )
            )

    # قوی‌ترین Dealها اول
    deals.sort(
        key=lambda deal: deal.diff_percent,
        reverse=True,
    )

    return deals
