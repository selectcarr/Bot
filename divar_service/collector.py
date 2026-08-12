from __future__ import annotations

import logging
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


LOGGER = logging.getLogger(
    "divar_service.collector"
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

        Every rejected advertisement is logged with the
        exact processing stage that rejected it.
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

            LOGGER.info(
                (
                    "Search page parsed | "
                    "page=%s | items=%s"
                ),
                page_number,
                len(page_items),
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

                LOGGER.info(
                    (
                        "Processing advertisement | "
                        "number=%s | ad_id=%s | "
                        "title=%r | price=%r | "
                        "year=%r"
                    ),
                    ads_seen,
                    item.ad_id,
                    item.title,
                    item.price,
                    item.year,
                )

                if self.ads_repository.exists(
                    item.ad_id
                ):
                    self.ads_repository.touch(
                        item.ad_id
                    )

                    duplicate_found = True
                    stop_reason = "duplicate_reached"
                    should_stop = True

                    LOGGER.info(
                        (
                            "Collection stopped at "
                            "previously stored advertisement | "
                            "ad_id=%s"
                        ),
                        item.ad_id,
                    )

                    break

                vehicle_ad = (
                    self._build_vehicle_ad(item)
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

                    LOGGER.info(
                        (
                            "Collection stopped because "
                            "advertisement already exists "
                            "during upsert | ad_id=%s"
                        ),
                        item.ad_id,
                    )

                    break

                ads_saved += 1

                current_ad_ids.add(
                    vehicle_ad.ad_id
                )

                LOGGER.info(
                    (
                        "Advertisement saved | "
                        "ad_id=%s | brand=%s | "
                        "model=%s | trim=%s | "
                        "year=%s | price=%s | "
                        "mileage=%s"
                    ),
                    vehicle_ad.ad_id,
                    vehicle_ad.brand,
                    vehicle_ad.model,
                    vehicle_ad.trim,
                    vehicle_ad.year,
                    vehicle_ad.price,
                    vehicle_ad.mileage,
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
            # صفحه بعدی.
            self.client.sleep_after_page(
                page_number
            )

        result = CollectionResult(
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

        LOGGER.info(
            (
                "Collection finished: "
                "pages=%s seen=%s saved=%s "
                "rejected=%s duplicate=%s "
                "stop_reason=%s"
            ),
            result.pages_requested,
            result.ads_seen,
            result.ads_saved,
            result.ads_rejected,
            result.duplicate_found,
            result.stop_reason,
        )

        return result

    def _build_vehicle_ad(
        self,
        item: SearchResultItem,
    ) -> VehicleAd | None:
        """
        Convert one search-result item into
        a validated VehicleAd.

        The method logs the exact stage at which
        an advertisement is rejected.
        """

        # -------------------------------------------------
        # مرحله 1: فیلتر اولیه
        # -------------------------------------------------

        try:
            filter_result = validate_ad(
                title=item.title,
                description=item.raw_text,
                price=item.price,
            )

        except Exception:
            LOGGER.exception(
                (
                    "Advertisement rejected | "
                    "stage=validate_ad_exception | "
                    "ad_id=%s | title=%r"
                ),
                item.ad_id,
                item.title,
            )

            return None

        if not filter_result.accepted:
            LOGGER.warning(
                (
                    "Advertisement rejected | "
                    "stage=validate_ad | "
                    "ad_id=%s | "
                    "title=%r | price=%r"
                ),
                item.ad_id,
                item.title,
                item.price,
            )

            return None

        LOGGER.info(
            (
                "Advertisement passed validation | "
                "ad_id=%s"
            ),
            item.ad_id,
        )

        # -------------------------------------------------
        # مرحله 2: سال
        # -------------------------------------------------

        if item.year is None:
            LOGGER.warning(
                (
                    "Advertisement rejected | "
                    "stage=missing_year | "
                    "ad_id=%s | "
                    "title=%r"
                ),
                item.ad_id,
                item.title,
            )

            return None

        LOGGER.info(
            (
                "Advertisement year accepted | "
                "ad_id=%s | year=%s"
            ),
            item.ad_id,
            item.year,
        )

        # -------------------------------------------------
        # مرحله 3: نرمال‌سازی قیمت
        # -------------------------------------------------

        try:
            price = normalize_price(
                item.price
            )

        except Exception:
            LOGGER.exception(
                (
                    "Advertisement rejected | "
                    "stage=normalize_price_exception | "
                    "ad_id=%s | raw_price=%r"
                ),
                item.ad_id,
                item.price,
            )

            return None

        if price is None:
            LOGGER.warning(
                (
                    "Advertisement rejected | "
                    "stage=invalid_price | "
                    "ad_id=%s | raw_price=%r"
                ),
                item.ad_id,
                item.price,
            )

            return None

        LOGGER.info(
            (
                "Advertisement price accepted | "
                "ad_id=%s | price=%s"
            ),
            item.ad_id,
            price,
        )

        # -------------------------------------------------
        # مرحله 4: استخراج خودرو
        # -------------------------------------------------

        try:
            extracted_vehicle = extract_vehicle(
                title=item.title,
                description=item.raw_text,
                structured_year=item.year,
            )

        except Exception:
            LOGGER.exception(
                (
                    "Advertisement rejected | "
                    "stage=extract_vehicle_exception | "
                    "ad_id=%s | title=%r"
                ),
                item.ad_id,
                item.title,
            )

            return None

        if extracted_vehicle is None:
            LOGGER.warning(
                (
                    "Advertisement rejected | "
                    "stage=extract_vehicle | "
                    "ad_id=%s | "
                    "title=%r | year=%r"
                ),
                item.ad_id,
                item.title,
                item.year,
            )

            return None

        LOGGER.info(
            (
                "Vehicle extracted | "
                "ad_id=%s | brand=%s | "
                "model=%s | trim=%s | "
                "year=%s | mileage=%s"
            ),
            item.ad_id,
            extracted_vehicle.brand,
            extracted_vehicle.model,
            extracted_vehicle.trim,
            extracted_vehicle.year,
            extracted_vehicle.mileage,
        )

        # -------------------------------------------------
        # مرحله 5: ساخت VehicleAd
        # -------------------------------------------------

        vehicle_ad = VehicleAd(
            ad_id=item.ad_id,
            brand=extracted_vehicle.brand,
            model=extracted_vehicle.model,
            year=extracted_vehicle.year,
            price=price,
            url=item.url,
            title=item.title,
            mileage=extracted_vehicle.mileage,
            trim=extracted_vehicle.trim,
        )

        LOGGER.info(
            (
                "VehicleAd built successfully | "
                "ad_id=%s"
            ),
            item.ad_id,
        )

        return vehicle_ad
