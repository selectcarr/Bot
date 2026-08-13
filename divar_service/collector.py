from __future__ import annotations

import logging
from dataclasses import dataclass

from divar_service.config.settings import Settings
from divar_service.divar_client import (
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

        Search-result extraction is attempted first.

        If the search card does not contain enough vehicle
        information, the advertisement detail page is fetched.

        Collection stops when:

        - A previously stored advertisement is reached.
        - Maximum ads per run is reached.
        - Maximum page count is reached.
        - An empty search page is received.
        - Divar returns an unusable search response.

        A temporary/unusable page response does not get
        converted into a false "no advertisements" result.
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

            # -------------------------------------------------
            # دریافت صفحه جستجو
            # -------------------------------------------------

            try:
                html = self.client.fetch_search_page(
                    page_number
                )

            except DivarClientError as exc:
                LOGGER.error(
                    (
                        "Search page could not be fetched | "
                        "page=%s | error=%s"
                    ),
                    page_number,
                    exc,
                )

                stop_reason = (
                    "search_page_fetch_failed"
                )

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

            # -------------------------------------------------
            # پردازش آگهی‌های صفحه
            # -------------------------------------------------

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

                # -------------------------------------------------
                # duplicate check
                # -------------------------------------------------

                if self.ads_repository.exists(
                    item.ad_id
                ):
                    self.ads_repository.touch(
                        item.ad_id
                    )

                    duplicate_found = True
                    stop_reason = (
                        "duplicate_reached"
                    )
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

                # -------------------------------------------------
                # ساخت آگهی
                # -------------------------------------------------

                vehicle_ad = (
                    self._build_vehicle_ad(item)
                )

                if vehicle_ad is None:
                    ads_rejected += 1
                    continue

                # -------------------------------------------------
                # ذخیره
                # -------------------------------------------------

                is_new = self.ads_repository.upsert(
                    vehicle_ad
                )

                if not is_new:
                    duplicate_found = True
                    stop_reason = (
                        "duplicate_reached"
                    )
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

            # -------------------------------------------------
            # فاصله قبل از صفحه بعد
            # -------------------------------------------------

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
        Build a VehicleAd.

        Search-page information is preferred.

        If year / vehicle classification is incomplete,
        the advertisement detail page is fetched and
        parsed before rejecting the advertisement.
        """

        # =================================================
        # مرحله 1: فیلتر اولیه
        # =================================================

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
                    "title=%r | price=%r | "
                    "reason=%s"
                ),
                item.ad_id,
                item.title,
                item.price,
                filter_result.reason,
            )

            return None

        LOGGER.info(
            (
                "Advertisement passed validation | "
                "ad_id=%s"
            ),
            item.ad_id,
        )

        # =================================================
        # مرحله 2: قیمت
        # =================================================

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

        # =================================================
        # مرحله 3: تلاش برای استخراج از Search Page
        # =================================================

        extracted_vehicle = None

        try:
            extracted_vehicle = extract_vehicle(
                title=item.title,
                description=item.raw_text,
                structured_year=item.year,
            )

        except Exception:
            LOGGER.exception(
                (
                    "Search extraction failed | "
                    "ad_id=%s | title=%r"
                ),
                item.ad_id,
                item.title,
            )

        # اگر اطلاعات Search Page کافی است،
        # نیازی به درخواست Detail Page نیست.
        if extracted_vehicle is not None:
            LOGGER.info(
                (
                    "Vehicle extracted from search page | "
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

            return self._build_vehicle_ad_from_extracted(
                item=item,
                price=price,
                extracted_vehicle=(
                    extracted_vehicle
                ),
            )

        # =================================================
        # مرحله 4: Search Page ناقص → Detail Page
        # =================================================

        LOGGER.info(
            (
                "Search result does not contain enough "
                "vehicle information | fetching "
                "advertisement detail | ad_id=%s | "
                "title=%r | year=%r"
            ),
            item.ad_id,
            item.title,
            item.year,
        )

        try:
            detail_html = self.client.fetch_ad_page(
                item.url
            )

        except DivarClientError as exc:
            LOGGER.warning(
                (
                    "Advertisement detail could not "
                    "be fetched | ad_id=%s | "
                    "error=%s"
                ),
                item.ad_id,
                exc,
            )

            return None

        LOGGER.info(
            (
                "Advertisement detail fetched "
                "successfully | ad_id=%s | "
                "text_characters=%s"
            ),
            item.ad_id,
            len(detail_html),
        )

        # =================================================
        # مرحله 5: استخراج از Detail Page
        # =================================================

        try:
            detail_vehicle = extract_vehicle(
                title=item.title,
                description=detail_html,
                structured_year=item.year,
            )

        except Exception:
            LOGGER.exception(
                (
                    "Advertisement rejected | "
                    "stage=detail_extract_vehicle_exception | "
                    "ad_id=%s | title=%r"
                ),
                item.ad_id,
                item.title,
            )

            return None

        if detail_vehicle is None:
            LOGGER.warning(
                (
                    "Advertisement rejected | "
                    "stage=detail_extract_vehicle | "
                    "ad_id=%s | "
                    "title=%r | search_year=%r"
                ),
                item.ad_id,
                item.title,
                item.year,
            )

            return None

        LOGGER.info(
            (
                "Vehicle extracted from detail page | "
                "ad_id=%s | brand=%s | "
                "model=%s | trim=%s | "
                "year=%s | mileage=%s"
            ),
            item.ad_id,
            detail_vehicle.brand,
            detail_vehicle.model,
            detail_vehicle.trim,
            detail_vehicle.year,
            detail_vehicle.mileage,
        )

        return self._build_vehicle_ad_from_extracted(
            item=item,
            price=price,
            extracted_vehicle=detail_vehicle,
        )

    @staticmethod
    def _build_vehicle_ad_from_extracted(
        item: SearchResultItem,
        price: int,
        extracted_vehicle,
    ) -> VehicleAd | None:
        """
        Build the final VehicleAd after successful
        vehicle extraction.
        """

        try:
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
                "VehicleAd built successfully | "
                "ad_id=%s"
            ),
            item.ad_id,
        )

        return vehicle_ad
