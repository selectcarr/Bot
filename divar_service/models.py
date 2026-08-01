from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VehicleAd:
    ad_id: str
    brand: str
    model: str
    year: int
    price: int
    url: str
    title: str = ""

    def __post_init__(self) -> None:
        if not self.ad_id.strip():
            raise ValueError("ad_id cannot be empty.")

        if not self.brand.strip():
            raise ValueError("brand cannot be empty.")

        if not self.model.strip():
            raise ValueError("model cannot be empty.")

        if self.year <= 0:
            raise ValueError("year must be positive.")

        if self.price <= 0:
            raise ValueError("price must be positive.")

        if not self.url.strip():
            raise ValueError("url cannot be empty.")

    @property
    def vehicle_key(
        self,
    ) -> tuple[str, str, str, int]:
        return (
            self.brand.strip(),
            self.model.strip(),
            self.trim.strip(),
            self.year,
        )


@dataclass(frozen=True, slots=True)
class DealCandidate:
    ad: VehicleAd
    market_average: int
    diff_percent: float
    sample_count: int

    def __post_init__(self) -> None:
        if self.market_average <= 0:
            raise ValueError(
                "market_average must be positive."
            )

        if self.diff_percent < 0:
            raise ValueError(
                "diff_percent cannot be negative."
            )

        if self.sample_count < 1:
            raise ValueError(
                "sample_count must be at least 1."
            )
