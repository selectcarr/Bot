from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from divar_service.config.settings import Settings
from divar_service.models import (
    DealCandidate,
    VehicleAd,
)
from divar_service.storage.sent_repository import (
    SentRepository,
)


VehicleKey = tuple[str, str, str, int]


class DealAnalyzer:
    def __init__(
        self,
        settings: Settings,
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
        Analyze recent advertisements.

        Only one lowest-price advertisement is selected
        from each exact vehicle key.
        """
        grouped_ads: dict[
            VehicleKey,
            dict[str, VehicleAd],
        ] = defaultdict(dict)

        for ad in ads:
            grouped_ads[ad.vehicle_key][ad.ad_id] = ad

        candidates: list[DealCandidate] = []

        for vehicle_ads_by_id in grouped_ads.values():
            vehicle_ads = list(
                vehicle_ads_by_id.values()
            )

            if (
                len(vehicle_ads)
                < self.settings.min_sample_count
            ):
                continue

            market_average_raw = (
                sum(ad.price for ad in vehicle_ads)
                / len(vehicle_ads)
            )

            if market_average_raw <= 0:
                continue

            lowest_ad = min(
                vehicle_ads,
                key=lambda ad: (
                    ad.price,
                    ad.ad_id,
                ),
            )

            if (
                current_ad_ids is not None
                and lowest_ad.ad_id not in current_ad_ids
            ):
                continue

            diff_percent = (
                (
                    market_average_raw
                    - lowest_ad.price
                )
                / market_average_raw
                * 100
            )

            if (
                diff_percent
                < self.settings.min_deal_percent
            ):
                continue

            if (
                diff_percent
                > self.settings.max_deal_percent
            ):
                continue

            if self.sent_repository.was_sent(
                lowest_ad.ad_id
            ):
                continue

            candidates.append(
                DealCandidate(
                    ad=lowest_ad,
                    market_average=round(
                        market_average_raw
                    ),
                    diff_percent=round(
                        diff_percent,
                        2,
                    ),
                    sample_count=len(vehicle_ads),
                )
            )

        candidates.sort(
            key=lambda candidate: (
                -candidate.diff_percent,
                candidate.ad.price,
                candidate.ad.ad_id,
            )
        )

        return candidates

    @staticmethod
    def select_best(
        candidates: Iterable[DealCandidate],
    ) -> DealCandidate | None:
        candidate_list = list(candidates)

        if not candidate_list:
            return None

        return max(
            candidate_list,
            key=lambda candidate: (
                candidate.diff_percent,
                -candidate.ad.price,
            ),
        )
