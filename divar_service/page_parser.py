from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


LOGGER = logging.getLogger(
    "divar_service.page_parser"
)

DIVAR_BASE_URL = "https://divar.ir"

AD_ID_PATTERN = re.compile(
    r"^[a-zA-Z0-9_-]+$"
)

PRICE_PATTERN = re.compile(
    r"([\d۰-۹][\d۰-۹,،٬\s]*)\s*(?:تومان|تومن)"
)

YEAR_PATTERN = re.compile(
    r"(?:مدل|سال)\s*[:：\-]?\s*"
    r"([0-9۰-۹]{2,4})"
)

MILEAGE_PATTERN = re.compile(
    r"کارکرد\s*[:：]?\s*"
    r"([0-9۰-۹][0-9۰-۹,،٬.\s]*)"
    r"\s*(?:کیلومتر|کیلو|km)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    """
    Raw advertisement data extracted from a
    Divar search-results page.

    This class intentionally does not classify the
    vehicle. Vehicle classification belongs to extractor.py.
    """

    ad_id: str
    title: str
    raw_text: str
    price: int | None
    url: str
    year: int | None
    mileage: int | None = None


def extract_search_items(
    html: str,
) -> list[SearchResultItem]:
    """
    Extract advertisement cards from a Divar
    search-results HTML page.

    Advertisement identity is taken from the stable
    token at the end of the /v/<slug>/<ad-id> path.

    Query parameters and fragments are removed from
    stored advertisement URLs.

    Duplicate advertisement IDs are removed.
    """

    if not isinstance(html, str):
        LOGGER.warning(
            "Search parser received non-string HTML | type=%s",
            type(html).__name__,
        )
        return []

    if not html.strip():
        LOGGER.warning(
            "Search parser received empty HTML"
        )
        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    items: list[SearchResultItem] = []
    seen_ad_ids: set[str] = set()

    links = soup.select(
        'a[href*="/v/"]'
    )

    links_found = len(links)
    invalid_href = 0
    invalid_ad_id = 0
    duplicate_id = 0
    empty_raw_text = 0
    empty_title = 0
    accepted_items = 0

    for link in links:
        href = str(
            link.get("href", "")
        ).strip()

        if not href:
            invalid_href += 1
            continue

        canonical_url = _canonicalize_ad_url(
            href
        )

        if not canonical_url:
            invalid_href += 1
            continue

        ad_id = _extract_ad_id(
            href
        )

        if not ad_id:
            invalid_ad_id += 1
            continue

        if ad_id in seen_ad_ids:
            duplicate_id += 1
            continue

        title, raw_text = _extract_card_text(
            link
        )

        if not raw_text:
            empty_raw_text += 1

        if not title:
            empty_title += 1
            continue

        price = _extract_price(
            raw_text
        )

        year = _extract_year(
            raw_text
        )

        mileage = _extract_mileage(
            raw_text
        )

        items.append(
            SearchResultItem(
                ad_id=ad_id,
                title=title,
                raw_text=raw_text,
                price=price,
                url=canonical_url,
                year=year,
                mileage=mileage,
            )
        )

        seen_ad_ids.add(
            ad_id
        )

        accepted_items += 1

    LOGGER.info(
        (
            "Search parser diagnostics | "
            "links_found=%s | "
            "invalid_href=%s | "
            "invalid_ad_id=%s | "
            "duplicate_id=%s | "
            "empty_raw_text=%s | "
            "empty_title=%s | "
            "accepted_items=%s"
        ),
        links_found,
        invalid_href,
        invalid_ad_id,
        duplicate_id,
        empty_raw_text,
        empty_title,
        accepted_items,
    )

    return items


def _extract_ad_id(
    href: str,
) -> str | None:
    """
    Extract the stable Divar advertisement token.

    Current URL example:

        /v/405تیوفایو/ga-piTfC?tracker_session_id=...

    Result:

        ga-piTfC

    The human-readable slug is not used as ad_id.
    """

    if not href:
        return None

    absolute_url = urljoin(
        DIVAR_BASE_URL,
        href,
    )

    parsed = urlsplit(
        absolute_url
    )

    path_parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    try:
        v_index = path_parts.index(
            "v"
        )
    except ValueError:
        return None

    advertisement_parts = path_parts[
        v_index + 1:
    ]

    if len(advertisement_parts) < 2:
        return None

    ad_id = advertisement_parts[-1].strip()

    if not AD_ID_PATTERN.fullmatch(
        ad_id
    ):
        return None

    return ad_id


def _canonicalize_ad_url(
    href: str,
) -> str | None:
    """
    Build a stable absolute Divar URL.

    Tracking query parameters and fragments are removed.
    """

    if not href:
        return None

    absolute_url = urljoin(
        DIVAR_BASE_URL,
        href,
    )

    parsed = urlsplit(
        absolute_url
    )

    if (
        not parsed.scheme
        or not parsed.netloc
        or not parsed.path
    ):
        return None

    canonical_path = (
        parsed.path.rstrip("/")
        or "/"
    )

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            canonical_path,
            "",
            "",
        )
    )


def _extract_card_text(
    link,
) -> tuple[str, str]:
    """
    Extract visible text from one advertisement card.

    The parser keeps the visible text relatively intact
    because downstream vehicle extraction may need
    information other than the title.
    """

    text_parts: list[str] = []

    for text in link.stripped_strings:
        cleaned = _clean_text(
            text
        )

        if cleaned:
            text_parts.append(
                cleaned
            )

    if not text_parts:
        return "", ""

    # Remove exact duplicate fragments while preserving
    # their original order.
    unique_parts = list(
        dict.fromkeys(
            text_parts
        )
    )

    raw_text = " ".join(
        unique_parts
    )

    title = _extract_title(
        unique_parts
    )

    return title, raw_text


def _extract_title(
    text_parts: list[str],
) -> str:
    """
    Select a probable advertisement title.

    Divar may change its card markup, therefore this
    intentionally avoids relying on CSS classes.

    Price and mileage-only fragments are ignored.
    """

    ignored_exact = {
        "تومان",
        "توافقی",
        "تماس بگیرید",
        "در حد نو",
        "کارکرده",
        "نو",
    }

    for text in text_parts:
        normalized = text.strip()

        if not normalized:
            continue

        if normalized in ignored_exact:
            continue

        if _looks_like_price(
            normalized
        ):
            continue

        if _looks_like_mileage(
            normalized
        ):
            continue

        return normalized

    return (
        text_parts[0]
        if text_parts
        else ""
    )


def _extract_price(
    text: str,
) -> int | None:
    """
    Extract price in تومان.

    Example:
        ۱,۲۷۰,۰۰۰,۰۰۰ تومان
        -> 1270000000
    """

    if not text:
        return None

    normalized = _normalize_digits(
        text
    )

    match = PRICE_PATTERN.search(
        normalized
    )

    if match:
        raw = match.group(1)

        raw = (
            raw
            .replace(",", "")
            .replace("،", "")
            .replace("٬", "")
            .replace(" ", "")
        )

        if raw.isdigit():
            try:
                value = int(raw)
            except ValueError:
                return None

            if value > 0:
                return value

    # بعضی کارت‌ها ممکن است قیمت را بدون
    # کلمه «تومان» نمایش دهند.
    return _extract_standalone_price(
        normalized
    )


def _extract_standalone_price(
    text: str,
) -> int | None:
    """
    Conservative fallback for price extraction.

    The range is intentionally broad enough for
    vehicle prices but avoids interpreting small
    numbers such as model years as prices.
    """

    candidates = re.findall(
        r"(?<!\d)"
        r"([0-9]{3,15})"
        r"(?!\d)",
        text,
    )

    for candidate in candidates:
        try:
            value = int(candidate)
        except ValueError:
            continue

        if (
            100_000
            <= value
            <= 100_000_000_000
        ):
            return value

    return None


def _extract_year(
    text: str,
) -> int | None:
    """
    Extract a vehicle model year.

    Supported forms include:

        مدل ۱۴۰۲
        مدل: 1402
        سال ۱۴۰۰
        ۱۴۰۰ مدل
        1402

    Two-digit years are accepted only when they are
    explicitly associated with «مدل» or «سال».

    Example:
        مدل 97 -> 97

    Conversion of two-digit years into the canonical
    year representation is intentionally left to
    normalize_year() in extractor.py.
    """

    if not text:
        return None

    normalized = _normalize_digits(
        text
    )

    match = YEAR_PATTERN.search(
        normalized
    )

    if match:
        raw = match.group(1)

        try:
            year = int(raw)
        except ValueError:
            return None

        if 0 <= year <= 99:
            return year

        if 1300 <= year <= 1499:
            return year

        return None

    # مستقل چهاررقمی شمسی
    matches = re.findall(
        r"(?<!\d)"
        r"(13\d{2}|14\d{2})"
        r"(?!\d)",
        normalized,
    )

    for raw in matches:
        try:
            year = int(raw)
        except ValueError:
            continue

        if 1300 <= year <= 1499:
            return year

    return None


def _extract_mileage(
    text: str,
) -> int | None:
    """
    Extract mileage from a search card.

    Examples:

        کارکرد 130000
        کارکرد 130,000
        کارکرد ۱۳۰٬۰۰۰ کیلومتر
    """

    if not text:
        return None

    normalized = _normalize_digits(
        text
    )

    match = MILEAGE_PATTERN.search(
        normalized
    )

    if not match:
        return None

    raw = match.group(1)

    raw = (
        raw
        .replace(",", "")
        .replace("،", "")
        .replace("٬", "")
        .replace(".", "")
        .replace(" ", "")
    )

    if not raw.isdigit():
        return None

    try:
        mileage = int(raw)
    except ValueError:
        return None

    if not 0 <= mileage <= 2_000_000:
        return None

    return mileage


def _looks_like_price(
    text: str,
) -> bool:
    normalized = _normalize_digits(
        text
    )

    return bool(
        PRICE_PATTERN.search(
            normalized
        )
    )


def _looks_like_mileage(
    text: str,
) -> bool:
    normalized = _normalize_digits(
        text
    )

    return bool(
        MILEAGE_PATTERN.search(
            normalized
        )
    )


def _normalize_digits(
    text: str,
) -> str:
    """
    Normalize Persian/Arabic digits and common
    Persian characters.
    """

    return (
        text.translate(
            str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹",
                "0123456789",
            )
        )
        .replace(
            "ي",
            "ی",
        )
        .replace(
            "ك",
            "ک",
        )
    )


def _clean_text(
    text: str,
) -> str:
    """
    Normalize whitespace and remove zero-width
    non-joiner characters.
    """

    return re.sub(
        r"\s+",
        " ",
        text.replace(
            "\u200c",
            " ",
        ),
    ).strip()
