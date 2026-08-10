from __future__ import annotations

from dataclasses import dataclass


# =============================
# مدل آگهی خودرو
# =============================
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
    trim: str | None = None

    # -------------------------
    # Validation
    # -------------------------
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

    # -------------------------
    # کلید پایه (قدیمی)
    # -------------------------
    @property
    def base_key(self) -> tuple[str, str, int]:
        return (
            self.brand.strip().lower(),
            self.model.strip().lower(),
            self.year,
        )

    # -------------------------
    # کلید دقیق (جدید - مهم)
    # -------------------------
    @property
    def comparison_key(self) -> tuple:
        """
        کلید نهایی برای مقایسه بازار
        """
        return (
            self.brand.strip().lower(),
            self.model.strip().lower(),
            self.year,
            (self.trim or "").strip().lower(),
        )


# =============================
# خروجی دیل واقعی
# =============================
@dataclass(frozen=True, slots=True)
class DealCandidate:
    ad: VehicleAd
    market_median: int
    diff_percent: float
    sample_count: int

    def __post_init__(self) -> None:
        if self.market_median <= 0:
            raise ValueError("market_median must be positive.")

        if self.diff_percent < 0:
            raise ValueError("diff_percent cannot be negative.")

        if self.sample_count < 1:
            raise ValueError("sample_count must be at least 1.")
