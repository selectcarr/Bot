from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup


DIVAR_BASE_URL = "https://divar.ir"

AD_URL_PATTERN = re.compile(
    r"/v/([a-zA-Z0-9_-]+)"
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

    The parser deliberately relies on advertisement
    URLs and visible text instead of Divar CSS class
    names, because those classes may change.

    Duplicate advertisement URLs are removed.
    """

    if not isinstance(html, str):
        return []

    if not html.strip():
        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    items: list[SearchResultItem] = []

    seen_ad_ids: set[str] = set()

    for link in soup.select(
        'a[href*="/v/"]'
    ):
        href = str(
            link.get("href", "")
        ).strip()

        if not href:
            continue

        ad_id = _extract_ad_id(
            href
        )

        if not ad_id:
            continue

        if ad_id in seen_ad_ids:
            continue

        title, raw_text = _extract_card_text(
            link
        )

        if not title:
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

        absolute_url = urljoin(
            DIVAR_BASE_URL,
            href,
        )

        items.append(
            SearchResultItem(
                ad_id=ad_id,
                title=title,
                raw_text=raw_text,
                price=price,
                url=absolute_url,
                year=year,
                mileage=mileage,
            )
        )

        seen_ad_ids.add(
            ad_id
        )

    return items


def _extract_ad_id(
    href: str,
) -> str | None:
    """
    Extract the complete Divar advertisement slug.

    Example:
        /v/206-abc123
        -> 206-abc123
    """

    match = AD_URL_PATTERN.search(
        href
    )

    if not match:
        return None

    ad_id = match.group(1).strip()

    return ad_id or None


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
