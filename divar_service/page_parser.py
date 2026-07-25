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
    normalize_for_match,
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


@dataclass(frozen=True, slots=True)
class DivarAdDetail:
    ad_id: str
    url: str
    title: str
    description: str
    price: int
    year: int
    structured_vehicle_text: str


def extract_search_items(
    html: str,
) -> list[SearchResultItem]:
    """
    Extract unique advertisement links from
    a Divar vehicle-search page.
    """
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    results: list[SearchResultItem] = []
    seen_ids: set[str] = set()

    for link in soup.select(
        'a[href*="/v/"]'
    ):
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

        title = _extract_link_title(link)

        seen_ids.add(ad_id)

        results.append(
            SearchResultItem(
                ad_id=ad_id,
                url=canonical_url,
                title=title,
            )
        )

    return results


def parse_ad_detail(
    html: str,
    url: str,
) -> DivarAdDetail | None:
    """
    Parse one Divar advertisement detail page.
    """
    canonical_url = canonicalize_ad_url(url)
    ad_id = extract_ad_id(canonical_url)

    if not ad_id:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    title = _extract_title(soup)

    if not title:
        return None

    page_text = soup.get_text(
        "\n",
        strip=True,
    )

    price = _extract_price(page_text)

    if price is None:
        return None

    year = _extract_year(page_text)

    if year is None:
        return None

    structured_vehicle_text = (
        _extract_vehicle_text(soup)
    )

    description = _extract_description(
        soup
    )

    return DivarAdDetail(
        ad_id=ad_id,
        url=canonical_url,
        title=title,
        description=description,
        price=price,
        year=year,
        structured_vehicle_text=(
            structured_vehicle_text
        ),
    )


def canonicalize_ad_url(
    url: str,
) -> str:
    """
    Convert a relative URL to an absolute URL
    and remove tracking parameters.
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
    parsed = urlsplit(url)

    path_parts = [
        part
        for part in parsed.path.split("/")
        if part
    ]

    if len(path_parts) < 3:
        return ""

    if path_parts[0] != "v":
        return ""

    return path_parts[-1].strip()


def _extract_link_title(
    link: Tag,
) -> str:
    aria_label = link.get(
        "aria-label"
    )

    if isinstance(aria_label, str):
        cleaned = normalize_whitespace(
            aria_label
        )

        if cleaned:
            return cleaned

    heading = link.find(
        ["h2", "h3"]
    )

    if isinstance(heading, Tag):
        cleaned = normalize_whitespace(
            heading.get_text(
                " ",
                strip=True,
            )
        )

        if cleaned:
            return cleaned

    return normalize_whitespace(
        link.get_text(
            " ",
            strip=True,
        )
    )


def _extract_title(
    soup: BeautifulSoup,
) -> str:
    heading = soup.find("h1")

    if not isinstance(heading, Tag):
        return ""

    return normalize_whitespace(
        heading.get_text(
            " ",
            strip=True,
        )
    )


def _extract_price(
    page_text: str,
) -> int | None:
    normalized_text = normalize_digits(
        page_text
    )

    patterns = (
        (
            r"قیمت پایه"
            r"[\s\S]{0,100}?"
            r"([0-9][0-9,\s٬،]{4,})"
            r"\s*تومان"
        ),
        (
            r"قیمت"
            r"[\s\S]{0,100}?"
            r"([0-9][0-9,\s٬،]{4,})"
            r"\s*تومان"
        ),
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized_text,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        price = normalize_price(
            match.group(1)
        )

        if price is not None:
            return price

    return None


def _extract_year(
    page_text: str,
) -> int | None:
    normalized_text = normalize_digits(
        page_text
    )

    label_match = re.search(
        r"مدل\s*\(سال تولید\)",
        normalized_text,
    )

    if label_match:
        nearby_text = normalized_text[
            label_match.end():
            label_match.end() + 350
        ]

        candidates = re.findall(
            r"(?<!\d)(\d{2,4})(?!\d)",
            nearby_text,
        )

        for candidate in candidates:
            year = normalize_year(
                candidate
            )

            if year is not None:
                return year

    title_patterns = (
        r"(?:مدل|سال)\s*[:\-]?\s*(\d{2,4})",
        r"(?<!\d)(13\d{2}|14\d{2}|20\d{2})(?!\d)",
    )

    for pattern in title_patterns:
        matches = re.findall(
            pattern,
            normalized_text,
        )

        for candidate in matches:
            year = normalize_year(
                candidate
            )

            if year is not None:
                return year

    return None


def _extract_vehicle_text(
    soup: BeautifulSoup,
) -> str:
    label = soup.find(
        string=lambda value: (
            isinstance(value, str)
            and normalize_for_match(value)
            == normalize_for_match(
                "برند و مدل"
            )
        )
    )

    if label is None:
        return ""

    parent = label.parent

    if not isinstance(parent, Tag):
        return ""

    for link in parent.find_all_next(
        "a",
        limit=10,
    ):
        text = normalize_whitespace(
            link.get_text(
                " ",
                strip=True,
            )
        )

        if not text:
            continue

        normalized = normalize_for_match(
            text
        )

        if normalized in {
            "خودرو سواری و وانت",
            "پشتیبانی",
            "درباره دیوار",
        }:
            continue

        return text

    return ""


def _extract_description(
    soup: BeautifulSoup,
) -> str:
    heading = soup.find(
        ["h2", "h3"],
        string=lambda value: (
            isinstance(value, str)
            and normalize_for_match(value)
            == normalize_for_match(
                "توضیحات"
            )
        ),
    )

    if not isinstance(heading, Tag):
        return ""

    for element in heading.find_all_next(
        ["p", "div"],
        limit=25,
    ):
        text = normalize_whitespace(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if len(text) < 15:
            continue

        normalized = normalize_for_match(
            text
        )

        if normalized.startswith(
            "توضیحات"
        ):
            continue

        blocked_starts = (
            "خودرو سواری و وانت",
            "تصویر",
            "یادداشت تنها",
            "گزارش آگهی",
            "درباره دیوار",
        )

        if any(
            normalized.startswith(
                normalize_for_match(value)
            )
            for value in blocked_starts
        ):
            continue

        return text[:5000]

    return ""
