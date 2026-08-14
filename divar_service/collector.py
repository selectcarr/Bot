from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from divar_service.config.settings import Settings
from divar_service.divar_client import (
    DivarBlockedError,
    DivarClient,
    DivarClientError,
)
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
    blocked: bool = False
    error_message: str | None = None


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
        self.random = random.SystemRandom()

    def collect(self) -> CollectionResult:
        """
        Collect advertisements incrementally from search pages.

        Detail-page requests are intentionally disabled. Search
        cards that do not contain enough reliable information are
        rejected rather than creating extra requests or accepting
        ambiguous data.
        """
        pages_requested = 0
        ads_seen = 0
        ads_saved = 0
        ads_rejected = 0

        duplicate_found = False
        stop_reason = "completed"
        blocked = False
        error_message: str | None = None

        current_ad_ids: set[str] = set()

        self._sleep_before_first_request()

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

            try:
                html = self.client.fetch_search_page(
                    page_number
                )

            except DivarBlockedError as exc:
                blocked = True
                error_message = str(exc)
                stop_reason = "divar_blocked"

                LOGGER.warning(
                    (
                        "Collection stopped immediately after "
                        "a Divar blocking response | "
                        "page=%s | error=%s"
                    ),
                    page_number,
                    exc,
                )

                break

            except DivarClientError as exc:
                LOGGER.error(
                    (
                        "Search page could not be fetched | "
                        "page=%s | error=%s"
                    ),
                    page_number,
                    exc,
                )

                error_message = str(exc)
                stop_reason = "search_page_fetch_failed"
                break

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
                LOGGER.warning(
                    (
                        "Search page contained no "
                        "advertisements | page=%s"
                    ),
                    page_number,
                )

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
                        "title=%r | price=%r | year=%r"
                    ),
                    ads_seen,
                    item.ad_id,
                    item.title,
                    item.price,
                    item.year,
                )

                existed_before = self.ads_repository.exists(
                    item.ad_id
                )

                vehicle_ad = self._build_vehicle_ad(
                    item
                )

                if vehicle_ad is None:
                    ads_rejected += 1

                    if existed_before:
                        duplicate_found = True
                        stop_reason = "duplicate_reached"
                        should_stop = True

                        LOGGER.info(
                            (
                                "Collection stopped at an existing "
                                "advertisement whose current card "
                                "could not be classified reliably | "
                                "ad_id=%s"
                            ),
                            item.ad_id,
                        )

                        break

                    continue

                is_new = self.ads_repository.upsert(
                    vehicle_ad
                )

                current_ad_ids.add(
                    vehicle_ad.ad_id
                )

                if is_new:
                    ads_saved += 1

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

                    continue

                duplicate_found = True
                stop_reason = "duplicate_reached"
                should_stop = True

                LOGGER.info(
                    (
                        "Existing advertisement refreshed before "
                        "incremental stop | ad_id=%s | "
                        "price=%s"
                    ),
                    vehicle_ad.ad_id,
                    vehicle_ad.price,
                )

                break

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

            delay = self.client.sleep_after_page(
                page_number
            )

            LOGGER.info(
                (
                    "Search pagination delay | "
                    "after_page=%s | seconds=%.2f"
                ),
                page_number,
                delay,
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
            blocked=blocked,
            error_message=error_message,
        )

        LOGGER.info(
            (
                "Collection finished: "
                "pages=%s seen=%s saved=%s "
                "rejected=%s duplicate=%s "
                "blocked=%s stop_reason=%s"
            ),
            result.pages_requested,
            result.ads_seen,
            result.ads_saved,
            result.ads_rejected,
            result.duplicate_found,
            result.blocked,
            result.stop_reason,
        )

        return result

    def _sleep_before_first_request(self) -> float:
        minimum = self.settings.initial_request_delay_min
        maximum = self.settings.initial_request_delay_max

        delay = self.random.uniform(
            minimum,
            maximum,
        )

        LOGGER.info(
            (
                "Request pacing | "
                "stage=before_first_search | "
                "delay_seconds=%.2f"
            ),
            delay,
        )

        if delay > 0:
            time.sleep(
                delay
            )

        return delay

    def _build_vehicle_ad(
        self,
        item: SearchResultItem,
    ) -> VehicleAd | None:
        """
        Build one VehicleAd using search-page data only.
        """
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
                    "stage=validate_ad | ad_id=%s | "
                    "title=%r | price=%r | reason=%s"
                ),
                item.ad_id,
                item.title,
                item.price,
                filter_result.reason,
            )

            return None

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
                    "stage=incomplete_search_classification | "
                    "ad_id=%s | title=%r | "
                    "search_year=%r"
                ),
                item.ad_id,
                item.title,
                item.year,
            )

            return None

        try:
            vehicle_ad = VehicleAd(
                ad_id=item.ad_id,
                brand=extracted_vehicle.brand,
                model=extracted_vehicle.model,
                trim=extracted_vehicle.trim,
                year=extracted_vehicle.year,
                price=price,
                url=item.url,
                title=item.title,
                mileage=extracted_vehicle.mileage,
            )

        except Exception:
            LOGGER.exception(
                (
                    "Advertisement rejected | "
                    "stage=build_vehicle_ad_exception | "
                    "ad_id=%s"
                ),
                item.ad_id,
            )

            return None

        LOGGER.info(
            (
                "Vehicle extracted from search page | "
                "ad_id=%s | brand=%s | model=%s | "
                "trim=%s | year=%s | mileage=%s"
            ),
            item.ad_id,
            vehicle_ad.brand,
            vehicle_ad.model,
            vehicle_ad.trim,
            vehicle_ad.year,
            vehicle_ad.mileage,
        )

        return vehicle_ad
