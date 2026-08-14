from __future__ import annotations

from collections.abc import Iterable

from divar_service.deal_finder import (
    find_deals,
)
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
        Analyze recent advertisements using the project rules.

        Comparison is based on:

            brand + model + trim + year
        """
        candidates = find_deals(
            ads,
            min_sample_count=(
                self.settings.min_sample_count
            ),
            min_deal_percent=(
                self.settings.min_deal_percent
            ),
            max_deal_percent=(
                self.settings.max_deal_percent
            ),
        )

        filtered_candidates: list[
            DealCandidate
        ] = []

        sent_ids = (
            self.sent_repository.get_sent_ids(
                candidate.ad.ad_id
                for candidate in candidates
            )
        )

        for candidate in candidates:
            ad = candidate.ad

            if (
                current_ad_ids is not None
                and ad.ad_id not in current_ad_ids
            ):
                continue

            if ad.ad_id in sent_ids:
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
                candidate.ad.ad_id,
            ),
        )
