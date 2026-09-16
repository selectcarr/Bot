from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Optional

MIN_SAMPLES = 3
MIN_DEAL_PERCENT = 1.0
MAX_DEAL_PERCENT = 15.0
MAX_MILEAGE_DIFFERENCE = 60_000

ALLOWED_BODY_CONDITIONS = {
    "clean",
    "minor_paint",
    "multi_paint",
    "full_paint",
    "replaced",
    "accident",
    "structural",
}


@dataclass(frozen=True)
class DealVehicle:
    source: str
    source_id: str
    brand: str
    model: str
    trim: str
    model_year: int
    mileage: Optional[int]
    body_condition: str
    price: int
    url: str = ""


@dataclass(frozen=True)
class DealDecision:
    is_deal: bool
    reason: str
    candidate_price: int
    market_average: Optional[int]
    discount_percent: Optional[float]
    sample_count: int


def _clean(value: str) -> str:
    return (value or "").strip().casefold()


def _valid_vehicle(vehicle: DealVehicle) -> bool:
    return (
        bool(_clean(vehicle.brand))
        and bool(_clean(vehicle.model))
        and bool(_clean(vehicle.trim))
        and 1300 <= vehicle.model_year <= 1499
        and vehicle.price > 0
        and vehicle.body_condition in ALLOWED_BODY_CONDITIONS
    )


def same_identity(left: DealVehicle, right: DealVehicle) -> bool:
    return (
        _clean(left.brand) == _clean(right.brand)
        and _clean(left.model) == _clean(right.model)
        and _clean(left.trim) == _clean(right.trim)
        and left.model_year == right.model_year
    )


def mileage_is_comparable(left: DealVehicle, right: DealVehicle) -> bool:
    if left.mileage is None or right.mileage is None:
        return True
    return abs(left.mileage - right.mileage) <= MAX_MILEAGE_DIFFERENCE


def is_comparable(candidate: DealVehicle, sample: DealVehicle) -> bool:
    if not _valid_vehicle(candidate) or not _valid_vehicle(sample):
        return False

    if (
        candidate.source == sample.source
        and candidate.source_id == sample.source_id
    ):
        return False

    return (
        same_identity(candidate, sample)
        and candidate.body_condition == sample.body_condition
        and mileage_is_comparable(candidate, sample)
    )


def comparable_samples(
    candidate: DealVehicle,
    history: Iterable[DealVehicle],
) -> list[DealVehicle]:
    return [
        sample
        for sample in history
        if is_comparable(candidate, sample)
    ]


def robust_market_average(samples: Iterable[DealVehicle]) -> Optional[int]:
    prices = sorted(
        sample.price
        for sample in samples
        if sample.price > 0
    )

    if len(prices) < MIN_SAMPLES:
        return None

    # Median is intentionally used as the robust market center. It is less
    # sensitive to one malformed or extreme listing than a simple mean.
    return int(round(median(prices)))


def discount_percent(candidate_price: int, market_average: int) -> Optional[float]:
    if candidate_price <= 0 or market_average <= 0:
        return None

    return (
        (market_average - candidate_price)
        / market_average
        * 100.0
    )


def evaluate_deal(
    candidate: DealVehicle,
    history: Iterable[DealVehicle],
) -> DealDecision:
    if not _valid_vehicle(candidate):
        return DealDecision(
            False,
            "invalid_candidate",
            candidate.price,
            None,
            None,
            0,
        )

    samples = comparable_samples(candidate, history)

    if len(samples) < MIN_SAMPLES:
        return DealDecision(
            False,
            "insufficient_samples",
            candidate.price,
            None,
            None,
            len(samples),
        )

    market = robust_market_average(samples)

    if market is None:
        return DealDecision(
            False,
            "invalid_market_average",
            candidate.price,
            None,
            None,
            len(samples),
        )

    discount = discount_percent(candidate.price, market)

    if discount is None:
        return DealDecision(
            False,
            "invalid_discount",
            candidate.price,
            market,
            None,
            len(samples),
        )

    if discount < MIN_DEAL_PERCENT:
        return DealDecision(
            False,
            "discount_below_minimum",
            candidate.price,
            market,
            discount,
            len(samples),
        )

    if discount > MAX_DEAL_PERCENT:
        return DealDecision(
            False,
            "discount_above_maximum",
            candidate.price,
            market,
            discount,
            len(samples),
        )

    return DealDecision(
        True,
        "confirmed_deal",
        candidate.price,
        market,
        discount,
        len(samples),
    )


def run_self_test() -> None:
    base = dict(
        brand="Peugeot",
        model="206",
        trim="تیپ 5",
        model_year=1397,
        mileage=100_000,
        body_condition="clean",
    )

    history = [
        DealVehicle("telegram", "1", price=1_000_000_000, **base),
        DealVehicle("bama", "2", price=1_020_000_000, **base),
        DealVehicle("divar", "3", price=980_000_000, **base),
    ]

    candidate = DealVehicle(
        "telegram",
        "candidate",
        price=950_000_000,
        **base,
    )

    result = evaluate_deal(candidate, history)
    assert result.is_deal
    assert result.market_average == 1_000_000_000
    assert round(result.discount_percent or 0, 2) == 5.0

    # Body condition must match.
    painted = DealVehicle(
        "bama",
        "painted",
        price=900_000_000,
        **{**base, "body_condition": "minor_paint"},
    )
    assert not is_comparable(candidate, painted)

    # Unknown body condition is not eligible for a confirmed deal.
    unknown = DealVehicle(
        "telegram",
        "unknown",
        price=950_000_000,
        **{**base, "body_condition": "unknown"},
    )
    assert evaluate_deal(unknown, history).reason == "invalid_candidate"

    # Mileage outside the permitted window is not comparable.
    high_mileage = DealVehicle(
        "bama",
        "high-mileage",
        price=800_000_000,
        **{**base, "mileage": 200_000},
    )
    assert not is_comparable(candidate, high_mileage)

    # At least three comparable samples are required.
    assert evaluate_deal(candidate, history[:2]).reason == "insufficient_samples"

    # Exact policy boundaries: 1% through 15% inclusive.
    one_percent = DealVehicle(
        "telegram",
        "one-percent",
        price=990_000_000,
        **base,
    )
    assert evaluate_deal(one_percent, history).is_deal

    fifteen_percent = DealVehicle(
        "telegram",
        "fifteen-percent",
        price=850_000_000,
        **base,
    )
    assert evaluate_deal(fifteen_percent, history).is_deal

    below = DealVehicle(
        "telegram",
        "below",
        price=995_000_000,
        **base,
    )
    assert not evaluate_deal(below, history).is_deal

    above = DealVehicle(
        "telegram",
        "above",
        price=840_000_000,
        **base,
    )
    assert not evaluate_deal(above, history).is_deal

    print("universal_deal_engine self-test: OK")


if __name__ == "__main__":
    run_self_test()
