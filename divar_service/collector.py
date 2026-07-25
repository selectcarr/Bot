from __future__ import annotations

from dataclasses import dataclass

from divar_service.config.settings import Settings
from divar_service.divar_client import DivarClient
from divar_service.extractor import extract_vehicle
from divar_service.filters import validate_ad
from divar_service.models import VehicleAd
from divar_service.normalizer import normalize_price
from divar_service.page_parser import (
    SearchResultItem,
    extract_search_items,
)
from divar_service.storage.ads_repository import (
    AdsRepository,
)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    pages_requested: int
    ads_seen: int
    ads_saved: int
    ads_rejected: int
    duplicate_found: bool
    stop_reason: str
    current_ad_ids: frozenset[str]


class IncrementalCollector:
    def __init__(
        self,
        settings: Settings,
        client: DivarClient,
        ads_repository: AdsRepository,
    ) -> None:
        self.settings = settings
        self.client = client
        self.ads_repository = ads_repository

    def collect(self) -> CollectionResult:
        """
        Collect new Divar advertisements incrementally.

        Collection stops when one of these conditions occurs:

        - The first previously stored ad is reached.
        - Maximum ads per run is reached.
        - Maximum page count is reached.
        - An empty page is received.
        """

        pages_requested = 0
        ads_seen = 0
        ads_saved = 0
        ads_rejected = 0

        duplicate_found = False
        stop_reason = "completed"

        current_ad_ids: set[str] = set()

        for page_number in range(
            1,
            self.settings.max_pages + 1,
        ):
            if (
                ads_seen
                >= self.settings.max_ads_per_run
            ):
                stop_reason = "max_ads_reached"
                break

            html = self.client.fetch_search_page(
                page_number
            )

            pages_requested += 1

            page_items = extract_search_items(
                html
            )

            if not page_items:
                stop_reason = "empty_page"
                break

            should_stop = False

            for item in page_items:
                if (
                    ads_seen
                    >= self.settings.max_ads_per_run
                ):
                    stop_reason = "max_ads_reached"
                    should_stop = True
                    break

                ads_seen += 1

                if self.ads_repository.exists(
                    item.ad_id
                ):
                    self.ads_repository.touch(
                        item.ad_id
                    )

                    duplicate_found = True
                    stop_reason = "duplicate_reached"
                    should_stop = True
                    break

                vehicle_ad = self._build_vehicle_ad(
                    item
                )

                if vehicle_ad is None:
                    ads_rejected += 1
                    continue

                is_new = self.ads_repository.upsert(
                    vehicle_ad
                )

                if not is_new:
                    duplicate_found = True
                    stop_reason = "duplicate_reached"
                    should_stop = True
                    break

                ads_saved += 1

                current_ad_ids.add(
                    vehicle_ad.ad_id
                )

            if should_stop:
                break

            if (
                ads_seen
                >= self.settings.max_ads_per_run
            ):
                stop_reason = "max_ads_reached"
                break

            if (
                page_number
                >= self.settings.max_pages
            ):
                stop_reason = "max_pages_reached"
                break

            # توقف تصادفی فقط قبل از درخواست
            # صفحه بعدی انجام می‌شود.
            self.client.sleep_after_page(
                page_number
            )

        return CollectionResult(
            pages_requested=pages_requested,
            ads_seen=ads_seen,
            ads_saved=ads_saved,
            ads_rejected=ads_rejected,
            duplicate_found=duplicate_found,
            stop_reason=stop_reason,
            current_ad_ids=frozenset(
                current_ad_ids
            ),
        )

    def _build_vehicle_ad(
        self,
        item: SearchResultItem,
    ) -> VehicleAd | None:
        """
        Convert one search-result item into
        a validated VehicleAd.
        """

        filter_result = validate_ad(
            title=item.title,
            description=item.raw_text,
            price=item.price,
        )

        if not filter_result.accepted:
            return None

        if item.year is None:
            return None

        price = normalize_price(
            item.price
        )

        if price is None:
            return None

        extracted_vehicle = extract_vehicle(
            title=item.title,
            description=item.raw_text,
            structured_year=item.year,
        )

        if extracted_vehicle is None:
            return None

        return VehicleAd(
            ad_id=item.ad_id,
            brand=extracted_vehicle.brand,
            model=extracted_vehicle.model,
            trim=extracted_vehicle.trim,
            year=extracted_vehicle.year,
            price=price,
            url=item.url,
            title=item.title,
        )
