from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup


DIVAR_BASE_URL = "https://divar.ir"

AD_URL_PATTERN = re.compile(
    r"/v/[a-zA-Z0-9_-]+"
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

    The parser is deliberately conservative:
    malformed or incomplete advertisement cards
    are skipped instead of creating invalid records.
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

        ad_id = _extract_ad_id(href)

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

        seen_ad_ids.add(ad_id)

    return items


def _extract_ad_id(
    href: str,
) -> str | None:
    match = AD_URL_PATTERN.search(
        href
    )

    if not match:
        return None

    path = match.group(0)

    ad_id = path.rsplit(
        "/",
        1,
    )[-1].strip()

    return ad_id or None


def _extract_card_text(
    link,
) -> tuple[str, str]:
    """
    Extract visible text from one advertisement card.

    The first meaningful text line is treated as the title.
    """

    text_parts = []

    for text in link.stripped_strings:
        cleaned = _clean_text(text)

        if cleaned:
            text_parts.append(
                cleaned
            )

    if not text_parts:
        return "", ""

    raw_text = " ".join(
        dict.fromkeys(
            text_parts
        )
    )

    title = _extract_title(
        text_parts
    )

    return title, raw_text


def _extract_title(
    text_parts: list[str],
) -> str:
    """
    Select a probable advertisement title.

    Divar may change its card markup, so this
    intentionally relies on visible text rather
    than fragile CSS class names.
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

    return text_parts[0] if text_parts else ""


def _extract_price(
    text: str,
) -> int | None:
    if not text:
        return None

    normalized = _normalize_digits(
        text
    )

    match = PRICE_PATTERN.search(
        normalized
    )

    if not match:
        # بعضی کارت‌ها ممکن است قیمت را
        # بدون کلمه تومان نمایش دهند.
        return _extract_standalone_price(
            normalized
        )

    raw = match.group(1)

    raw = (
        raw
        .replace(",", "")
        .replace("،", "")
        .replace("٬", "")
        .replace(" ", "")
    )

    if not raw.isdigit():
        return None

    try:
        value = int(raw)
    except ValueError:
        return None

    if value <= 0:
        return None

    return value


def _extract_standalone_price(
    text: str,
) -> int | None:
    """
    Conservative fallback for price extraction.

    It intentionally does not treat arbitrary numbers
    as prices unless they look like a realistic vehicle
    price.
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

        # این بازه صرفاً برای جلوگیری از تبدیل
        # سال/کارکرد به قیمت است.
        if 100_000 <= value <= 100_000_000_000:
            return value

    return None


def _extract_year(
    text: str,
) -> int | None:
    if not text:
        return None

    normalized = _normalize_digits(
        text
    )

    match = YEAR_PATTERN.search(
        normalized
    )

    if not match:
        # سال چهاررقمی مستقل
        matches = re.findall(
            r"(?<!\d)"
            r"(13\d{2}|14\d{2})"
            r"(?!\d)",
            normalized,
        )

        if not matches:
            return None

        raw = matches[0]

    else:
        raw = match.group(1)

    try:
        year = int(raw)
    except ValueError:
        return None

    if 1300 <= year <= 1499:
        return year

    return None


def _extract_mileage(
    text: str,
) -> int | None:
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
    return re.sub(
        r"\s+",
        " ",
        text.replace(
            "\u200c",
            " ",
        ),
    ).strip()
