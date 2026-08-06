def mileage_bucket(mileage: int | None) -> int | None:
    if mileage is None:
        return None
    return (mileage // 10000) * 10000


def build_comparison_key(ad) -> tuple:
    return (
        ad.brand,
        ad.model,
        ad.year,
        ad.trim,
        mileage_bucket(ad.mileage),
    )
