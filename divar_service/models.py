from __future__ import annotations

from dataclasses import dataclass

from divar_service.normalizer import (
    build_vehicle_key,
)


@dataclass(frozen=True, slots=True)
class VehicleAd:
    ad_id: str
    brand: str
    model: str
    year: int
    price: int
    url: str

    title: str = ""
    mileage: int | None = None
    trim: str = ""

    def __post_init__(self) -> None:
        clean_ad_id = str(
            self.ad_id or ""
        ).strip()
        clean_brand = str(
            self.brand or ""
        ).strip()
        clean_model = str(
            self.model or ""
        ).strip()
        clean_url = str(
            self.url or ""
        ).strip()
        clean_title = str(
            self.title or ""
        ).strip()
        clean_trim = str(
            self.trim or ""
        ).strip()

        if not clean_ad_id:
            raise ValueError(
                "ad_id cannot be empty."
            )

        if not clean_brand:
            raise ValueError(
                "brand cannot be empty."
            )

        if not clean_model:
            raise ValueError(
                "model cannot be empty."
            )

        if self.year <= 0:
            raise ValueError(
                "year must be positive."
            )

        if self.price <= 0:
            raise ValueError(
                "price must be positive."
            )

        if not clean_url:
            raise ValueError(
                "url cannot be empty."
            )

        object.__setattr__(
            self,
            "ad_id",
            clean_ad_id,
        )
        object.__setattr__(
            self,
            "brand",
            clean_brand,
        )
        object.__setattr__(
            self,
            "model",
            clean_model,
        )
        object.__setattr__(
            self,
            "url",
            clean_url,
        )
        object.__setattr__(
            self,
            "title",
            clean_title,
        )
        object.__setattr__(
            self,
            "trim",
            clean_trim,
        )

    @property
    def base_key(
        self,
    ) -> tuple[str, str, int]:
        return (
            self.brand.strip().lower(),
            self.model.strip().lower(),
            self.year,
        )

    @property
    def comparison_key(
        self,
    ) -> tuple[str, str, str, int]:
        """
        Exact market group:

            brand + model + trim + year
        """
        return build_vehicle_key(
            brand=self.brand,
            model=self.model,
            trim=self.trim,
            year=self.year,
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

    @property
    def market_median(self) -> int:
        """
        Backward-compatible alias for older callers.

        The current business rule uses arithmetic average.
        """
        return self.market_average
