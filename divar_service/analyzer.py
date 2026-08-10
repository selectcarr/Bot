from __future__ import annotations

from collections.abc import Iterable

from divar_service.deal_finder import find_deals
from divar_service.models import (
    DealCandidate,
    VehicleAd,
)
from divar_service.storage.sent_repository import (
    SentRepository,
)


class DealAnalyzer:
    def __init__(
        self,
        settings,
        sent_repository: SentRepository,
    ) -> None:
        self.settings = settings
        self.sent_repository = sent_repository

    def analyze(
        self,
        ads: Iterable[VehicleAd],
        current_ad_ids: set[str] | None = None,
    ) -> list[DealCandidate]:
        """
        Analyze recent advertisements using the
        centralized deal_finder logic.

        Comparison is based on:

        brand + model + trim + year

        and mileage similarity is handled by
        deal_finder.py using ±10,000 km.
        """

        ads_list = list(ads)

        candidates = find_deals(
            ads_list
        )

        filtered_candidates: list[
            DealCandidate
        ] = []

        for candidate in candidates:

            ad = candidate.ad

            # فقط آگهی‌های همین اجرای فعلی
            # برای ارسال Deal بررسی شوند.
            if (
                current_ad_ids is not None
                and ad.ad_id not in current_ad_ids
            ):
                continue

            # آگهی‌ای که قبلاً ارسال شده
            # دوباره ارسال نشود.
            if self.sent_repository.was_sent(
                ad.ad_id
            ):
                continue

            filtered_candidates.append(
                candidate
            )

        filtered_candidates.sort(
            key=lambda candidate: (
                -candidate.diff_percent,
                candidate.ad.price,
                candidate.ad.ad_id,
            )
        )

        return filtered_candidates

    @staticmethod
    def select_best(
        candidates: Iterable[DealCandidate],
    ) -> DealCandidate | None:

        candidate_list = list(
            candidates
        )

        if not candidate_list:
            return None

        return max(
            candidate_list,
            key=lambda candidate: (
                candidate.diff_percent,
                -candidate.ad.price,
            ),
        )
