from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import (
    urljoin,
    urlsplit,
    urlunsplit,
)

from bs4 import BeautifulSoup
from bs4.element import Tag

from divar_service.normalizer import (
    normalize_digits,
    normalize_price,
    normalize_whitespace,
    normalize_year,
)


DIVAR_BASE_URL = "https://divar.ir"


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    ad_id: str
    url: str
    title: str
    raw_text: str
    price: int | None
    year: int | None


def extract_search_items(
    html: str,
) -> list[SearchResultItem]:
    """
    Extract vehicle advertisement information directly
    from a Divar search-results page.

    No advertisement-detail page request is required.
    """
    if not html.strip():
        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    results: list[SearchResultItem] = []
    seen_ids: set[str] = set()

    for link in soup.select('a[href*="/v/"]'):
        if not isinstance(link, Tag):
            continue

        href = link.get("href")

        if not isinstance(href, str):
            continue

        canonical_url = canonicalize_ad_url(
            href
        )

        ad_id = extract_ad_id(
            canonical_url
        )

        if not ad_id:
            continue

        if ad_id in seen_ids:
            continue

        raw_text = normalize_whitespace(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if not raw_text:
            continue

        title = extract_card_title(
            link=link,
            raw_text=raw_text,
        )

        price = extract_card_price(
            raw_text
        )

        year = extract_card_year(
            raw_text
        )

        seen_ids.add(ad_id)

        results.append(
            SearchResultItem(
                ad_id=ad_id,
                url=canonical_url,
                title=title,
                raw_text=raw_text,
                price=price,
                year=year,
            )
        )

    return results


def extract_card_title(
    link: Tag,
    raw_text: str,
) -> str:
    """
    Prefer the heading element inside the advertisement card.
    """
    for heading_name in (
        "h1",
        "h2",
        "h3",
    ):
        heading = link.find(
            heading_name
        )

        if isinstance(heading, Tag):
            title = normalize_whitespace(
                heading.get_text(
                    " ",
                    strip=True,
                )
            )

            if title:
                return title

    aria_label = link.get(
        "aria-label"
    )

    if isinstance(aria_label, str):
        title = normalize_whitespace(
            aria_label
        )

        if title:
            return title

    return derive_title_from_card_text(
        raw_text
    )


def derive_title_from_card_text(
    raw_text: str,
) -> str:
    """
    Remove common fields such as mileage, price,
    publication time and location from card text.
    """
    text = normalize_digits(
        raw_text
    )

    split_patterns = (
        r"\s+[0-9][0-9,\s٬،]*\s*کیلومتر",
        r"\s+[0-9][0-9,\s٬،]*\s*تومان",
        r"\s+لحظاتی پیش",
        r"\s+دقایقی پیش",
        r"\s+ساعاتی پیش",
        r"\s+امروز",
        r"\s+دیروز",
        r"\s+نردبان شده",
    )

    earliest_position: int | None = None

    for pattern in split_patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        if (
            earliest_position is None
            or match.start() < earliest_position
        ):
            earliest_position = match.start()

    if earliest_position is not None:
        text = text[:earliest_position]

    return normalize_whitespace(
        text
    )


def extract_card_price(
    raw_text: str,
) -> int | None:
    """
    Extract the value immediately preceding 'تومان'.
    """
    normalized_text = normalize_digits(
        raw_text
    )

    matches = re.findall(
        r"([0-9][0-9,\s٬،]{3,})\s*تومان",
        normalized_text,
        flags=re.IGNORECASE,
    )

    if not matches:
        return None

    valid_prices: list[int] = []

    for value in matches:
        price = normalize_price(
            value
        )

        if price is not None:
            valid_prices.append(
                price
            )

    if not valid_prices:
        return None

    return max(valid_prices)


def extract_card_year(
    raw_text: str,
) -> int | None:
    """
    Extract year conservatively.

    Two- and three-digit values are accepted only when
    clearly attached to words such as مدل or سال.
    """
    normalized_text = normalize_digits(
        raw_text
    )

    labeled_patterns = (
        r"(?:مدل|سال)\s*[:\-]?\s*(?:اسفند|فروردین|"
        r"اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|"
        r"آذر|دی|بهمن)?\s*(\d{2,4})",

        r"(\d{2,4})\s*(?:مدل|سال)",
    )

    for pattern in labeled_patterns:
        matches = re.findall(
            pattern,
            normalized_text,
            flags=re.IGNORECASE,
        )

        for value in matches:
            year = normalize_year(
                value
            )

            if year is not None:
                return year

    parenthesized_years = re.findall(
        r"\(\s*(13\d{2}|14\d{2}|20\d{2})\s*\)",
        normalized_text,
    )

    for value in parenthesized_years:
        year = normalize_year(
            value
        )

        if year is not None:
            return year

    four_digit_years = re.findall(
        r"(?<!\d)(13\d{2}|14\d{2}|20\d{2})(?!\d)",
        normalized_text,
    )

    for value in four_digit_years:
        year = normalize_year(
            value
        )

        if year is not None:
            return year

    # اعداد مستقلی مثل 206، 405 یا 550
    # عمداً به‌عنوان سال پذیرفته نمی‌شوند.
    return None


def canonicalize_ad_url(
    url: str,
) -> str:
    """
    Convert relative links to absolute links
    and remove query strings and tracking parameters.
    """
    absolute_url = urljoin(
        DIVAR_BASE_URL,
        url.strip(),
    )

    parsed = urlsplit(
        absolute_url
    )

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
        )
    )


def extract_ad_id(
    url: str,
) -> str:
    """
    Extract the stable advertisement identifier
    from a /v/... URL.
    """
    parsed = urlsplit(
        url
    )

    path_parts = [
        part.strip()
        for part in parsed.path.split("/")
        if part.strip()
    ]

    if len(path_parts) < 2:
        return ""

    if path_parts[0] != "v":
        return ""

    return path_parts[-1]
