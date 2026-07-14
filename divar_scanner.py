#!/usr/bin/env python3
"""Select Car - Divar bargain detector.

Scans recent Divar car advertisements, groups genuinely comparable vehicles,
filters financing/leasing/down-payment ads, detects listings below the robust
market median, and sends qualified opportunities to a Telegram channel.

This project uses Divar's public web endpoints as exposed to the website.
Those endpoints are not a stable public contract and can change.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import math
import os
import re
import signal
import sqlite3
import statistics
import sys
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")


# =========================================================
# Configuration
# =========================================================

def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "بله",
    }


def env_int(
    name: str,
    default: int,
    minimum: int | None = None,
) -> int:
    raw = os.getenv(name)

    try:
        value = int(raw) if raw is not None else default
    except ValueError as exc:
        raise ValueError(
            f"{name} must be an integer, got {raw!r}"
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"{name} must be >= {minimum}"
        )

    return value


def env_float(
    name: str,
    default: float,
    minimum: float | None = None,
) -> float:
    raw = os.getenv(name)

    try:
        value = float(raw) if raw is not None else default
    except ValueError as exc:
        raise ValueError(
            f"{name} must be numeric, got {raw!r}"
        ) from exc

    if minimum is not None and value < minimum:
        raise ValueError(
            f"{name} must be >= {minimum}"
        )

    return value


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str

    divar_search_url: str
    divar_detail_url: str

    database_path: Path
    log_path: Path
    model_aliases_path: Path

    check_interval_hours: float

    scan_pages: int
    initial_bootstrap_pages: int

    auto_bootstrap: bool
    run_on_start: bool
    send_photo: bool

    search_request_delay_seconds: float
    detail_request_delay_seconds: float
    detail_workers: int
    request_timeout_seconds: int

    min_comparables: int
    min_discount_percent: float
    min_discount_toman: int
    max_discount_percent: float

    history_days: int
    year_tolerance: int
    mileage_tolerance_km: int

    prefer_same_city: bool
    require_year: bool
    require_mileage: bool
    require_body_condition: bool

    min_price_toman: int
    max_price_toman: int

    max_messages_per_run: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            telegram_bot_token=os.getenv(
                "TELEGRAM_BOT_TOKEN",
                "",
            ).strip(),

            telegram_chat_id=os.getenv(
                "TELEGRAM_CHAT_ID",
                "@chanelllvip",
            ).strip(),

            divar_search_url=os.getenv(
                "DIVAR_SEARCH_URL",
                "https://api.divar.ir/v8/web-search/iran/car",
            ).strip(),

            divar_detail_url=os.getenv(
                "DIVAR_DETAIL_URL",
                "https://api.divar.ir/v8/posts-v2/web/{token}",
            ).strip(),

            database_path=APP_DIR / os.getenv(
                "DATABASE_PATH",
                "data/selectcar.sqlite3",
            ),

            log_path=APP_DIR / os.getenv(
                "LOG_PATH",
                "logs/selectcar.log",
            ),

            model_aliases_path=APP_DIR / os.getenv(
                "MODEL_ALIASES_PATH",
                "model_aliases.json",
            ),

            check_interval_hours=env_float(
                "CHECK_INTERVAL_HOURS",
                3.0,
                1.0,
            ),

            scan_pages=env_int(
                "SCAN_PAGES",
                12,
                1,
            ),

            initial_bootstrap_pages=env_int(
                "INITIAL_BOOTSTRAP_PAGES",
                60,
                1,
            ),

            auto_bootstrap=env_bool(
                "AUTO_BOOTSTRAP",
                True,
            ),

            run_on_start=env_bool(
                "RUN_ON_START",
                True,
            ),

            send_photo=env_bool(
                "SEND_PHOTO",
                True,
            ),

            search_request_delay_seconds=env_float(
                "SEARCH_REQUEST_DELAY_SECONDS",
                2.2,
                0.2,
            ),

            detail_request_delay_seconds=env_float(
                "DETAIL_REQUEST_DELAY_SECONDS",
                0.35,
                0.0,
            ),

            detail_workers=env_int(
                "DETAIL_WORKERS",
                3,
                1,
            ),

            request_timeout_seconds=env_int(
                "REQUEST_TIMEOUT_SECONDS",
                30,
                5,
            ),

            min_comparables=env_int(
                "MIN_COMPARABLES",
                6,
                3,
            ),

            min_discount_percent=env_float(
                "MIN_DISCOUNT_PERCENT",
                8.0,
                0.1,
            ),

            min_discount_toman=env_int(
                "MIN_DISCOUNT_TOMAN",
                50_000_000,
                0,
            ),

            max_discount_percent=env_float(
                "MAX_DISCOUNT_PERCENT",
                35.0,
                1.0,
            ),

            history_days=env_int(
                "HISTORY_DAYS",
                45,
                7,
            ),

            year_tolerance=env_int(
                "YEAR_TOLERANCE",
                0,
                0,
            ),

            mileage_tolerance_km=env_int(
                "MILEAGE_TOLERANCE_KM",
                25_000,
                0,
            ),

            prefer_same_city=env_bool(
                "PREFER_SAME_CITY",
                True,
            ),

            require_year=env_bool(
                "REQUIRE_YEAR",
                True,
            ),

            require_mileage=env_bool(
                "REQUIRE_MILEAGE",
                True,
            ),

            require_body_condition=env_bool(
                "REQUIRE_BODY_CONDITION",
                False,
            ),

            min_price_toman=env_int(
                "MIN_PRICE_TOMAN",
                30_000_000,
                1,
            ),

            max_price_toman=env_int(
                "MAX_PRICE_TOMAN",
                100_000_000_000,
                1,
            ),

            max_messages_per_run=env_int(
                "MAX_MESSAGES_PER_RUN",
                20,
                1,
            ),
        )

    def validate(
        self,
        telegram_required: bool = True,
    ) -> None:
        if telegram_required and not self.telegram_bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is empty. Put it in .env"
            )

        if telegram_required and not self.telegram_chat_id:
            raise ValueError(
                "TELEGRAM_CHAT_ID is empty. Put it in .env"
            )

        if "{token}" not in self.divar_detail_url:
            raise ValueError(
                "DIVAR_DETAIL_URL must contain {token}"
            )

        if self.max_discount_percent <= self.min_discount_percent:
            raise ValueError(
                "MAX_DISCOUNT_PERCENT must be greater than "
                "MIN_DISCOUNT_PERCENT"
            )


# =========================================================
# Logging
# =========================================================

def setup_logging(config: Config) -> None:
    config.log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            config.log_path,
            encoding="utf-8",
        ),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=handlers,
        force=True,
    )


log = logging.getLogger("selectcar")


# =========================================================
# Persian text normalization and extraction
# =========================================================

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS + "كيىةۀؤإأٱ",
    "0123456789" + "0123456789" + "کییههوااا",
)


NUMBER_WORDS = {
    "یک": "1",
    "دو": "2",
    "سه": "3",
    "چهار": "4",
    "پنج": "5",
    "شش": "6",
    "هفت": "7",
    "هشت": "8",
    "نه": "9",
}


# هر آگهی که یکی از این عبارت‌ها را داشته باشد حذف می‌شود.
FINANCE_KEYWORDS = (
    "اقساط",
    "اقساطی",
    "قسطی",
    "فروش اقساطی",
    "لیزینگ",
    "پیش پرداخت",
    "پیشپرداخت",
    "پرداخت اولیه",
    "پیش فروش",
    "پیشفروش",
    "چکی",
    "خرید با چک",
    "بدون پیش پرداخت",
    "بدون پیشپرداخت",
    "قسط ماهانه",
    "پرداخت ماهانه",
    "تحویل اقساطی",
    "تحویل فوری اقساطی",
    "وام",
    "وام بانکی",
    "شرایطی",
    "فروش شرایطی",
    "بالن",
    "اجاره به شرط تملیک",
)


NO_PRICE_KEYWORDS = (
    "توافقی",
    "قیمت توافقی",
    "تماس بگیرید",
    "قیمت در تماس",
    "قیمت بعد از بازدید",
    "قیمت تماس",
    "قیمت واقعی تماس",
)


SEVERE_DAMAGE_KEYWORDS = (
    "چپی",
    "اتاق تعویض",
    "شاسی ضربه",
    "شاسی تعویض",
    "تصادف سنگین",
    "موتور تعویض",
)


NOISE_WORDS = {
    "فروش",
    "فروشی",
    "فوری",
    "زیرقیمت",
    "زیر قیمت",
    "قیمت مناسب",
    "تمیز",
    "بسیار تمیز",
    "خانگی",
    "شخصی",
    "کم کار",
    "کمکار",
    "کارکرد واقعی",
    "بدون واسطه",
    "ویژه",
    "عالی",
    "سالم",
    "خشک",
    "صفر",
    "صفرکیلومتر",
    "صفر کیلومتر",
}


COLOR_WORDS = {
    "سفید",
    "مشکی",
    "سیاه",
    "نقره ای",
    "نقره‌ای",
    "خاکستری",
    "نوک مدادی",
    "نوک‌مدادی",
    "آبی",
    "قرمز",
    "زرشکی",
    "بژ",
    "کرم",
    "طلایی",
    "یشمی",
    "قهوه ای",
    "قهوه‌ای",
}


BODY_CLASSES = {
    "clean": (
        "بدون رنگ",
        "بیرنگ",
        "بی رنگ",
        "بی‌رنگ",
        "بدنه سالم",
        "بدون خط و خش",
    ),

    "paint_spot": (
        "یک لکه",
        "دو لکه",
        "لکه رنگ",
        "گلگیر رنگ",
        "درب رنگ",
        "رنگ شدگی",
    ),

    "paint_round": (
        "دور رنگ",
        "چند لکه",
        "نیمه رنگ",
    ),

    "full_paint": (
        "تمام رنگ",
        "کامل رنگ",
        "یک دست رنگ",
        "یکدست رنگ",
    ),

    "replaced": (
        "تعویض",
        "قطعه تعویض",
        "درب تعویض",
        "گلگیر تعویض",
    ),

    "accident": (
        "تصادفی",
        "چپی",
        "شاسی",
        "اتاق تعویض",
    ),
}


BODY_LABELS = {
    "clean": "بدون رنگ",
    "paint_spot": "لکه‌رنگ",
    "paint_round": "دوررنگ/چندلکه",
    "full_paint": "تمام‌رنگ",
    "replaced": "قطعه تعویض",
    "accident": "تصادفی/شاسی",
    "unknown": "نامشخص",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).translate(DIGIT_TRANSLATION)

    text = (
        text
        .replace("\u200c", " ")
        .replace("\u200f", " ")
        .replace("\ufeff", " ")
    )

    text = re.sub(
        r"[\u064b-\u065f]",
        "",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


def compact_text(value: Any) -> str:
    text = normalize_text(value).lower()

    text = re.sub(
        r"[^0-9a-zآ-ی]+",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def contains_any(
    text: str,
    keywords: Sequence[str],
) -> str | None:
    normalized = compact_text(text)

    for keyword in keywords:
        if compact_text(keyword) in normalized:
            return keyword

    return None


def detect_exclusion(
    text: str,
) -> tuple[bool, str | None]:
    keyword = contains_any(
        text,
        FINANCE_KEYWORDS,
    )

    if keyword:
        return True, f"شرایط مالی/اقساط: {keyword}"

    normalized = compact_text(text)

    if re.search(
        r"(?:^|\s)\d+\s*قسط(?:\s|$)",
        normalized,
    ):
        return True, "ذکر تعداد قسط"

    if re.search(
        r"(?:^|\s)قسط\s*\d+",
        normalized,
    ):
        return True, "ذکر مبلغ یا تعداد قسط"

    keyword = contains_any(
        text,
        NO_PRICE_KEYWORDS,
    )

    if keyword:
        return True, f"قیمت نامشخص: {keyword}"

    return False, None


def to_ascii_number(value: str) -> str:
    return (
        normalize_text(value)
        .replace("٬", "")
        .replace(",", "")
        .replace(" ", "")
    )


def parse_human_number(
    raw: str,
) -> float | None:
    cleaned = (
        to_ascii_number(raw)
        .replace("٫", ".")
    )

    try:
        return float(cleaned)
    except ValueError:
        return None


def plausible_price(
    value: int,
    config: Config,
) -> int | None:
    if (
        config.min_price_toman
        <= value
        <= config.max_price_toman
    ):
        return value

    # گاهی API قیمت را به ریال برمی‌گرداند.
    converted = value // 10

    if (
        config.min_price_toman
        <= converted
        <= config.max_price_toman
    ):
        return converted

    return None


def parse_price_from_text(
    text: str,
    config: Config,
) -> int | None:
    normalized = normalize_text(text)

    if contains_any(
        normalized,
        NO_PRICE_KEYWORDS,
    ):
        return None

    patterns = (
        (
            r"([0-9]+(?:[.,٫][0-9]+)?)"
            r"\s*میلیارد(?:\s*تومان)?",
            1_000_000_000,
        ),

        (
            r"([0-9]+(?:[.,٫][0-9]+)?)"
            r"\s*میلیون(?:\s*تومان)?",
            1_000_000,
        ),

        (
            r"([0-9][0-9٬, ]{6,})"
            r"\s*تومان",
            1,
        ),
    )

    for pattern, multiplier in patterns:
        match = re.search(
            pattern,
            normalized,
        )

        if not match:
            continue

        number = parse_human_number(
            match.group(1)
        )

        if number is None:
            continue

        return plausible_price(
            int(number * multiplier),
            config,
        )

    return None


def recursive_items(
    obj: Any,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            new_path = path + (str(key),)

            yield new_path, value
            yield from recursive_items(
                value,
                new_path,
            )

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            new_path = path + (str(index),)

            yield new_path, value
            yield from recursive_items(
                value,
                new_path,
            )


def all_text(
    obj: Any,
    max_chars: int = 30_000,
) -> str:
    parts: list[str] = []
    size = 0

    for _, value in recursive_items(obj):
        if not isinstance(value, str):
            continue

        value = normalize_text(value)

        if not value:
            continue

        if value in parts:
            continue

        parts.append(value)
        size += len(value)

        if size >= max_chars:
            break

    return "\n".join(parts)


def extract_price(
    obj: Any,
    config: Config,
) -> int | None:
    texts: list[str] = []

    # ابتدا قیمت‌های نمایشی و متنی بررسی می‌شوند.
    for path, value in recursive_items(obj):
        if not isinstance(value, str):
            continue

        path_text = " ".join(path).lower()
        normalized_value = normalize_text(value)

        if (
            "price" in path_text
            or "قیمت" in normalized_value
            or "تومان" in normalized_value
        ):
            texts.append(value)

    for text in texts:
        price = parse_price_from_text(
            text,
            config,
        )

        if price:
            return price

    # سپس ساختارهای دارای برچسب قیمت بررسی می‌شوند.
    for _, value in recursive_items(obj):
        if not isinstance(value, Mapping):
            continue

        label = normalize_text(
            value.get("title")
            or value.get("label")
            or value.get("name")
            or value.get("key")
            or ""
        )

        if (
            "قیمت" not in label
            and "price" not in label.lower()
        ):
            continue

        for candidate_key in (
            "value",
            "number",
            "value_raw",
            "raw_value",
            "price",
        ):
            candidate = value.get(candidate_key)

            if isinstance(candidate, str):
                parsed = parse_price_from_text(
                    candidate + " تومان",
                    config,
                )

                if parsed:
                    return parsed

            elif (
                isinstance(candidate, (int, float))
                and not isinstance(candidate, bool)
            ):
                parsed = plausible_price(
                    int(candidate),
                    config,
                )

                if parsed:
                    return parsed

    # در مرحله آخر کلیدهای عددی که نام price دارند بررسی می‌شوند.
    for path, value in recursive_items(obj):
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            continue

        key = path[-1].lower() if path else ""

        if key not in {
            "price",
            "price_value",
            "pricevalue",
        }:
            continue

        parsed = plausible_price(
            int(value),
            config,
        )

        if parsed:
            return parsed

    return None


def parse_year(
    text: str,
) -> int | None:
    normalized = normalize_text(text)

    labelled = re.search(
        r"(?:مدل|سال ساخت|سال تولید)"
        r"\s*[:：-]?\s*"
        r"((?:13|14)\d{2}|20\d{2})",
        normalized,
    )

    if labelled:
        return int(labelled.group(1))

    years = [
        int(value)
        for value in re.findall(
            r"(?<!\d)"
            r"((?:13|14)\d{2}|20(?:0\d|1\d|2[0-6]))"
            r"(?!\d)",
            normalized,
        )
    ]

    if not years:
        return None

    shamsi_years = [
        year
        for year in years
        if 1300 <= year <= 1499
    ]

    if shamsi_years:
        return shamsi_years[0]

    return years[0]


def parse_mileage(
    text: str,
) -> int | None:
    normalized = normalize_text(text)

    if re.search(
        r"(?:کارکرد|کیلومتر)"
        r"\s*[:：-]?\s*"
        r"(?:صفر|0)"
        r"(?:\D|$)",
        normalized,
    ):
        return 0

    patterns = (
        r"کارکرد\s*[:：-]?\s*"
        r"([0-9][0-9٬, ]*)"
        r"\s*(?:کیلومتر|km)?",

        r"([0-9][0-9٬, ]*)"
        r"\s*کیلومتر"
        r"(?:\s*کارکرد)?",

        r"کیلومتر\s*[:：-]?\s*"
        r"([0-9][0-9٬, ]*)",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        number = parse_human_number(
            match.group(1)
        )

        if (
            number is not None
            and 0 <= number <= 2_000_000
        ):
            return int(number)

    return None


def detect_body_class(
    text: str,
) -> str:
    normalized = compact_text(text)

    # ترتیب شدت مهم است.
    order = (
        "accident",
        "replaced",
        "full_paint",
        "paint_round",
        "paint_spot",
        "clean",
    )

    for body_class in order:
        terms = BODY_CLASSES[body_class]

        if any(
            compact_text(term) in normalized
            for term in terms
        ):
            return body_class

    return "unknown"


def extract_first_value(
    obj: Any,
    keys: set[str],
) -> str | None:
    lowered = {
        key.lower()
        for key in keys
    }

    for path, value in recursive_items(obj):
        if not path:
            continue

        if not isinstance(
            value,
            (str, int, float),
        ):
            continue

        if path[-1].lower() not in lowered:
            continue

        text = normalize_text(value)

        if text:
            return text

    return None


def extract_city_and_district(
    obj: Any,
) -> tuple[str | None, str | None]:
    city = extract_first_value(
        obj,
        {
            "city_persian",
            "city_name",
            "city",
            "city_title",
            "city_slug",
        },
    )

    district = extract_first_value(
        obj,
        {
            "district_persian",
            "district_name",
            "district",
            "neighborhood",
            "neighbourhood",
        },
    )

    return city, district


def extract_image_url(
    obj: Any,
) -> str | None:
    candidates: list[str] = []

    for path, value in recursive_items(obj):
        if (
            not isinstance(value, str)
            or not value.startswith("http")
        ):
            continue

        key = path[-1].lower() if path else ""

        if not any(
            part in key
            for part in (
                "image",
                "thumbnail",
                "src",
                "url",
            )
        ):
            continue

        if (
            re.search(
                r"\.(?:jpg|jpeg|png|webp)(?:\?|$)",
                value,
                re.IGNORECASE,
            )
            or "divarcdn" in value
        ):
            candidates.append(value)

    if not candidates:
        return None

    return candidates[0]


def find_title(
    obj: Any,
) -> str:
    if isinstance(obj, Mapping):
        data = (
            obj.get("data")
            if isinstance(obj.get("data"), Mapping)
            else obj
        )

        for key in (
            "title",
            "header",
            "name",
        ):
            value = (
                data.get(key)
                if isinstance(data, Mapping)
                else None
            )

            if (
                isinstance(value, str)
                and normalize_text(value)
            ):
                return normalize_text(value)

    value = extract_first_value(
        obj,
        {"title"},
    )

    return value or "آگهی خودرو"


def find_token(
    obj: Any,
) -> str | None:
    direct = extract_first_value(
        obj,
        {
            "token",
            "post_token",
        },
    )

    if (
        direct
        and re.fullmatch(
            r"[A-Za-z0-9_-]{5,}",
            direct,
        )
    ):
        return direct

    for _, value in recursive_items(obj):
        if not isinstance(value, str):
            continue

        match = re.search(
            r"divar\.ir/v/(?:[^/]+/)?"
            r"([A-Za-z0-9_-]{5,})",
            value,
        )

        if match:
            return match.group(1)

    return None


@dataclass(frozen=True)
class AliasRule:
    pattern: re.Pattern[str]
    key: str


def load_alias_rules(
    path: Path,
) -> list[AliasRule]:
    if not path.exists():
        return []

    try:
        raw = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        log.warning(
            "Could not load model aliases from %s: %s",
            path,
            exc,
        )

        return []

    rules: list[AliasRule] = []

    for item in raw:
        try:
            rules.append(
                AliasRule(
                    re.compile(
                        item["pattern"],
                        re.IGNORECASE,
                    ),
                    compact_text(item["key"]),
                )
            )

        except (
            KeyError,
            re.error,
            TypeError,
        ) as exc:
            log.warning(
                "Skipping invalid alias rule %r: %s",
                item,
                exc,
            )

    return rules


def canonical_model_key(
    title: str,
    alias_rules: Sequence[AliasRule],
) -> str:
    text = compact_text(title)

    text = re.sub(
        r"(?<=\D)(20\d{2}|1[34]\d{2})(?=\D|$)",
        " ",
        f" {text} ",
    )

    text = re.sub(
        r"\bمدل\s*(?:20\d{2}|1[34]\d{2})\b",
        " ",
        text,
    )

    text = re.sub(
        r"\b\d[\d,٬ ]*\s*(?:کیلومتر|km)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    for word, number in NUMBER_WORDS.items():
        text = re.sub(
            rf"\bتیپ\s+{word}\b",
            f"تیپ {number}",
            text,
        )

    replacements = {
        "پژو206": "پژو 206",
        "پژو207": "پژو 207",
        "206sd": "206 sd",
        "دناپلاس": "دنا پلاس",
        "سمندال ایکس": "سمند lx",
        "ال نود": "l90",
        "تندر 90": "l90",
        "تندر90": "l90",
        "کوییکآر": "کوییک r",
        "تاراوی 4": "تارا v4",
        "تاراوی 2": "تارا v2",
    }

    for source, target in replacements.items():
        text = text.replace(
            source,
            target,
        )

    for phrase in sorted(
        NOISE_WORDS | COLOR_WORDS,
        key=len,
        reverse=True,
    ):
        text = text.replace(
            compact_text(phrase),
            " ",
        )

    for terms in BODY_CLASSES.values():
        for phrase in sorted(
            terms,
            key=len,
            reverse=True,
        ):
            text = text.replace(
                compact_text(phrase),
                " ",
            )

    text = re.sub(
        r"\b(?:مدل|رنگ|کارکرد)\b",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    for rule in alias_rules:
        if rule.pattern.search(text):
            return rule.key

    return text[:160]


# =========================================================
# Listing model
# =========================================================

@dataclass
class Listing:
    token: str
    title: str
    model_key: str

    year: int | None
    mileage: int | None

    body_class: str
    price_toman: int | None

    city: str | None
    district: str | None

    url: str
    image_url: str | None
    description: str

    excluded: bool
    excluded_reason: str | None

    severe_damage: bool

    first_seen: str
    last_seen: str

    source_json: str

    @classmethod
    def from_objects(
        cls,
        card: Mapping[str, Any],
        detail: Mapping[str, Any] | None,
        config: Config,
        alias_rules: Sequence[AliasRule],
        first_seen: str | None = None,
    ) -> "Listing | None":
        token = (
            find_token(card)
            or (
                find_token(detail)
                if detail
                else None
            )
        )

        if not token:
            return None

        merged: dict[str, Any] = {
            "card": card,
        }

        if detail:
            merged["detail"] = detail

        title = find_title(
            detail or card
        )

        if title == "آگهی خودرو":
            title = find_title(card)

        text = all_text(merged)

        price = extract_price(
            merged,
            config,
        )

        year = parse_year(text)
        mileage = parse_mileage(text)

        body_class = detect_body_class(text)

        city, district = extract_city_and_district(
            merged
        )

        image_url = extract_image_url(
            merged
        )

        excluded, reason = detect_exclusion(
            f"{title}\n{text}"
        )

        severe_keyword = contains_any(
            text,
            SEVERE_DAMAGE_KEYWORDS,
        )

        now = utcnow_iso()

        model_key = canonical_model_key(
            title,
            alias_rules,
        )

        if (
            not model_key
            or len(model_key) < 2
        ):
            excluded = True
            reason = (
                reason
                or "مدل خودرو قابل تشخیص نیست"
            )

        if price is None:
            excluded = True
            reason = (
                reason
                or "قیمت قابل استخراج نیست"
            )

        return cls(
            token=token,
            title=title,
            model_key=model_key,

            year=year,
            mileage=mileage,

            body_class=body_class,
            price_toman=price,

            city=city,
            district=district,

            url=f"https://divar.ir/v/a/{token}",
            image_url=image_url,

            description=text[:20_000],

            excluded=excluded,
            excluded_reason=reason,

            severe_damage=bool(
                severe_keyword
            ),

            first_seen=(
                first_seen
                or now
            ),

            last_seen=now,

            source_json=json.dumps(
                merged,
                ensure_ascii=False,
                separators=(",", ":"),
            )[:200_000],
        )


@dataclass(frozen=True)
class Opportunity:
    listing: Listing

    median_price: int
    comparable_count: int

    discount_toman: int
    discount_percent: float

    scope: str
    confidence: str


# =========================================================
# HTTP clients
# =========================================================

def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=4,
        connect=4,
        read=4,

        backoff_factor=1.0,

        status_forcelist=(
            429,
            500,
            502,
            503,
            504,
        ),

        allowed_methods=frozenset({
            "GET",
            "POST",
        }),

        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )

    session.mount(
        "https://",
        adapter,
    )

    session.mount(
        "http://",
        adapter,
    )

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),

        "Accept": (
            "application/json, "
            "text/plain, */*"
        ),

        "Accept-Language": (
            "fa-IR,fa;q=0.9,en;q=0.7"
        ),

        "Origin": "https://divar.ir",
        "Referer": "https://divar.ir/",
    })

    return session


def url_with_page(
    url: str,
    page: int,
) -> str:
    parts = urlsplit(url)

    query = dict(
        parse_qsl(
            parts.query,
            keep_blank_values=True,
        )
    )

    query["page"] = str(page)

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


class DivarClient:
    def __init__(
        self,
        config: Config,
    ):
        self.config = config
        self.session = build_session()

        self._detail_lock = threading.Lock()
        self._last_detail_request = 0.0

    def fetch_search_page(
        self,
        page: int,
    ) -> Mapping[str, Any]:
        url = url_with_page(
            self.config.divar_search_url,
            page,
        )

        response = self.session.get(
            url,
            timeout=self.config.request_timeout_seconds,
        )

        response.raise_for_status()

        data = response.json()

        time.sleep(
            self.config.search_request_delay_seconds
        )

        if not isinstance(data, Mapping):
            raise ValueError(
                "Divar search response is not a JSON object"
            )

        return data

    def fetch_detail(
        self,
        token: str,
    ) -> Mapping[str, Any] | None:
        with self._detail_lock:
            elapsed = (
                time.monotonic()
                - self._last_detail_request
            )

            wait = (
                self.config.detail_request_delay_seconds
                - elapsed
            )

            if wait > 0:
                time.sleep(wait)

            self._last_detail_request = (
                time.monotonic()
            )

        url = self.config.divar_detail_url.format(
            token=token
        )

        try:
            response = self.session.get(
                url,
                timeout=self.config.request_timeout_seconds,
            )

            if response.status_code in {
                404,
                410,
            }:
                return None

            response.raise_for_status()

            data = response.json()

            if isinstance(data, Mapping):
                return data

            return None

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            log.warning(
                "Detail request failed for %s: %s",
                token,
                exc,
            )

            return None

    @staticmethod
    def extract_cards(
        payload: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        cards: list[Mapping[str, Any]] = []
        seen_tokens: set[str] = set()

        def add_candidate(
            candidate: Any,
        ) -> None:
            if not isinstance(
                candidate,
                Mapping,
            ):
                return

            token = find_token(candidate)
            title = find_title(candidate)

            if not token or not title:
                return

            if token in seen_tokens:
                return

            seen_tokens.add(token)
            cards.append(candidate)

        web_widgets = payload.get(
            "web_widgets"
        )

        if isinstance(
            web_widgets,
            Mapping,
        ):
            post_list = web_widgets.get(
                "post_list"
            )

            if isinstance(
                post_list,
                list,
            ):
                for item in post_list:
                    add_candidate(item)

        if cards:
            return cards

        # حالت جایگزین برای تغییر احتمالی ساختار API.
        for _, value in recursive_items(payload):
            if not isinstance(value, list):
                continue

            for item in value:
                if not isinstance(
                    item,
                    Mapping,
                ):
                    continue

                widget_type = str(
                    item.get("widget_type")
                    or item.get("type")
                    or ""
                ).upper()

                if (
                    "POST" in widget_type
                    or find_token(item)
                ):
                    add_candidate(item)

        return cards


class TelegramClient:
    def __init__(
        self,
        config: Config,
    ):
        self.config = config
        self.session = build_session()

        self.base_url = (
            "https://api.telegram.org/bot"
            f"{config.telegram_bot_token}"
        )

    def test(self) -> None:
        response = self.session.get(
            f"{self.base_url}/getMe",
            timeout=self.config.request_timeout_seconds,
        )

        response.raise_for_status()

        payload = response.json()

        if not payload.get("ok"):
            raise RuntimeError(
                f"Telegram getMe failed: {payload}"
            )

        username = (
            payload
            .get("result", {})
            .get("username", "unknown")
        )

        log.info(
            "Telegram bot connected: @%s",
            username,
        )

    def send_opportunity(
        self,
        opportunity: Opportunity,
    ) -> None:
        caption = build_telegram_caption(
            opportunity
        )

        if (
            self.config.send_photo
            and opportunity.listing.image_url
        ):
            endpoint = (
                f"{self.base_url}/sendPhoto"
            )

            body = {
                "chat_id": (
                    self.config.telegram_chat_id
                ),

                "photo": (
                    opportunity.listing.image_url
                ),

                "caption": caption,

                "parse_mode": "HTML",
            }

            response = self.session.post(
                endpoint,
                data=body,
                timeout=self.config.request_timeout_seconds,
            )

            if response.ok:
                return

            log.warning(
                "sendPhoto failed (%s), "
                "falling back to sendMessage",
                response.status_code,
            )

        endpoint = (
            f"{self.base_url}/sendMessage"
        )

        body = {
            "chat_id": (
                self.config.telegram_chat_id
            ),

            "text": caption,

            "parse_mode": "HTML",

            "disable_web_page_preview": "false",
        }

        response = self.session.post(
            endpoint,
            data=body,
            timeout=self.config.request_timeout_seconds,
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram sendMessage failed: {result}"
            )


# =========================================================
# SQLite storage
# =========================================================

def utcnow_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


class Database:
    def __init__(
        self,
        path: Path,
    ):
        self.path = path

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.connection = sqlite3.connect(
            path,
            timeout=30,
            check_same_thread=False,
        )

        self.connection.row_factory = sqlite3.Row

        self.connection.execute(
            "PRAGMA journal_mode=WAL"
        )

        self.connection.execute(
            "PRAGMA synchronous=NORMAL"
        )

        self.init_schema()

    def close(self) -> None:
        self.connection.close()

    def init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (
                token TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model_key TEXT NOT NULL,
                year INTEGER,
                mileage INTEGER,
                body_class TEXT NOT NULL,
                price_toman INTEGER,
                city TEXT,
                district TEXT,
                url TEXT NOT NULL,
                image_url TEXT,
                description TEXT NOT NULL,
                excluded INTEGER NOT NULL DEFAULT 0,
                excluded_reason TEXT,
                severe_damage INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                source_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_listings_comparable
            ON listings(
                model_key,
                year,
                mileage,
                body_class,
                city,
                price_toman
            );

            CREATE INDEX IF NOT EXISTS idx_listings_last_seen
            ON listings(last_seen);

            CREATE TABLE IF NOT EXISTS sent (
                token TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL,
                median_price INTEGER NOT NULL,
                comparable_count INTEGER NOT NULL,
                discount_percent REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                mode TEXT NOT NULL,
                pages_requested INTEGER NOT NULL,
                cards_seen INTEGER NOT NULL DEFAULT 0,
                new_listings INTEGER NOT NULL DEFAULT 0,
                excluded_listings INTEGER NOT NULL DEFAULT 0,
                opportunities_sent INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            """
        )

        self.connection.commit()

    def count_listings(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS n FROM listings"
        ).fetchone()

        return int(row["n"])

    def exists(
        self,
        token: str,
    ) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM listings WHERE token = ?",
            (token,),
        ).fetchone()

        return row is not None

    def first_seen(
        self,
        token: str,
    ) -> str | None:
        row = self.connection.execute(
            "SELECT first_seen "
            "FROM listings "
            "WHERE token = ?",
            (token,),
        ).fetchone()

        if not row:
            return None

        return str(row["first_seen"])

    def upsert(
        self,
        listing: Listing,
    ) -> None:
        values = asdict(listing)

        values["excluded"] = int(
            listing.excluded
        )

        values["severe_damage"] = int(
            listing.severe_damage
        )

        columns = list(
            values.keys()
        )

        placeholders = ",".join(
            "?"
            for _ in columns
        )

        updates = ",".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column not in {
                "token",
                "first_seen",
            }
        )

        sql = (
            f"INSERT INTO listings "
            f"({','.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(token) "
            f"DO UPDATE SET {updates}"
        )

        self.connection.execute(
            sql,
            [
                values[column]
                for column in columns
            ],
        )

        self.connection.commit()

    def was_sent(
        self,
        token: str,
    ) -> bool:
        row = self.connection.execute(
            "SELECT 1 "
            "FROM sent "
            "WHERE token = ?",
            (token,),
        ).fetchone()

        return row is not None

    def mark_sent(
        self,
        opportunity: Opportunity,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO sent
            (
                token,
                sent_at,
                median_price,
                comparable_count,
                discount_percent
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                opportunity.listing.token,
                utcnow_iso(),
                opportunity.median_price,
                opportunity.comparable_count,
                opportunity.discount_percent,
            ),
        )

        self.connection.commit()

    def peer_prices(
        self,
        listing: Listing,
        config: Config,
        same_city: bool,
    ) -> list[int]:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(
                days=config.history_days
            )
        ).replace(
            microsecond=0
        ).isoformat()

        clauses = [
            "token <> ?",
            "model_key = ?",
            "excluded = 0",
            "price_toman IS NOT NULL",
            "last_seen >= ?",
        ]

        params: list[Any] = [
            listing.token,
            listing.model_key,
            cutoff,
        ]

        if listing.year is not None:
            clauses.append(
                "year BETWEEN ? AND ?"
            )

            params.extend([
                listing.year
                - config.year_tolerance,

                listing.year
                + config.year_tolerance,
            ])

        elif config.require_year:
            return []

        else:
            clauses.append(
                "year IS NULL"
            )

        if listing.mileage is not None:
            clauses.append(
                "mileage IS NOT NULL "
                "AND ABS(mileage - ?) <= ?"
            )

            params.extend([
                listing.mileage,
                config.mileage_tolerance_km,
            ])

        elif config.require_mileage:
            return []

        else:
            clauses.append(
                "mileage IS NULL"
            )

        if listing.body_class != "unknown":
            clauses.append(
                "body_class = ?"
            )

            params.append(
                listing.body_class
            )

        elif config.require_body_condition:
            return []

        else:
            clauses.append(
                "body_class = 'unknown'"
            )

        if same_city and listing.city:
            clauses.append(
                "city = ?"
            )

            params.append(
                listing.city
            )

        query = (
            "SELECT price_toman "
            "FROM listings "
            "WHERE "
            + " AND ".join(clauses)
        )

        rows = self.connection.execute(
            query,
            params,
        ).fetchall()

        return [
            int(row["price_toman"])
            for row in rows
            if row["price_toman"]
        ]

    def begin_run(
        self,
        mode: str,
        pages: int,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO runs(
                started_at,
                mode,
                pages_requested
            )
            VALUES (?, ?, ?)
            """,
            (
                utcnow_iso(),
                mode,
                pages,
            ),
        )

        self.connection.commit()

        return int(
            cursor.lastrowid
        )

    def finish_run(
        self,
        run_id: int,
        cards_seen: int,
        new_listings: int,
        excluded_listings: int,
        opportunities_sent: int,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET
                finished_at = ?,
                cards_seen = ?,
                new_listings = ?,
                excluded_listings = ?,
                opportunities_sent = ?,
                error = ?
            WHERE id = ?
            """,
            (
                utcnow_iso(),
                cards_seen,
                new_listings,
                excluded_listings,
                opportunities_sent,
                error,
                run_id,
            ),
        )

        self.connection.commit()


# =========================================================
# Statistical comparison
# =========================================================

def percentile(
    sorted_values: Sequence[int],
    p: float,
) -> float:
    if not sorted_values:
        raise ValueError(
            "Cannot calculate percentile of empty data"
        )

    if len(sorted_values) == 1:
        return float(
            sorted_values[0]
        )

    index = (
        len(sorted_values) - 1
    ) * p

    lower = math.floor(index)
    upper = math.ceil(index)

    if lower == upper:
        return float(
            sorted_values[lower]
        )

    weight = index - lower

    return (
        sorted_values[lower] * (1 - weight)
        + sorted_values[upper] * weight
    )


def remove_price_outliers(
    prices: Sequence[int],
) -> list[int]:
    values = sorted(
        int(price)
        for price in prices
        if price > 0
    )

    if len(values) < 5:
        return values

    q1 = percentile(
        values,
        0.25,
    )

    q3 = percentile(
        values,
        0.75,
    )

    iqr = q3 - q1

    if iqr <= 0:
        return values

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    filtered = [
        price
        for price in values
        if lower <= price <= upper
    ]

    if len(filtered) >= 3:
        return filtered

    return values


def confidence_label(
    discount_percent: float,
    comparable_count: int,
    same_city: bool,
) -> str:
    score = 0

    if comparable_count >= 12:
        score += 2

    elif comparable_count >= 8:
        score += 1

    if same_city:
        score += 1

    if 9 <= discount_percent <= 22:
        score += 1

    if score >= 3:
        return "بالا"

    return "متوسط"


def analyze_listing(
    listing: Listing,
    db: Database,
    config: Config,
) -> Opportunity | None:
    if listing.excluded:
        return None

    if listing.price_toman is None:
        return None

    if db.was_sent(listing.token):
        return None

    if (
        config.require_year
        and listing.year is None
    ):
        return None

    if (
        config.require_mileage
        and listing.mileage is None
    ):
        return None

    if (
        config.require_body_condition
        and listing.body_class == "unknown"
    ):
        return None

    same_city = (
        config.prefer_same_city
        and bool(listing.city)
    )

    prices = db.peer_prices(
        listing,
        config,
        same_city=same_city,
    )

    scope = (
        "همان شهر"
        if same_city
        else "سراسری"
    )

    if (
        len(prices) < config.min_comparables
        and same_city
    ):
        prices = db.peer_prices(
            listing,
            config,
            same_city=False,
        )

        same_city = False
        scope = "سراسری"

    prices = remove_price_outliers(
        prices
    )

    if len(prices) < config.min_comparables:
        return None

    median_price = int(
        statistics.median(prices)
    )

    if median_price <= 0:
        return None

    if listing.price_toman >= median_price:
        return None

    discount_toman = (
        median_price
        - listing.price_toman
    )

    discount_percent = (
        discount_toman
        / median_price
        * 100
    )

    if (
        discount_toman
        < config.min_discount_toman
    ):
        return None

    if (
        discount_percent
        < config.min_discount_percent
    ):
        return None

    if (
        discount_percent
        > config.max_discount_percent
    ):
        log.info(
            "Skipping suspiciously cheap ad %s: %.1f%% below median",
            listing.token,
            discount_percent,
        )

        return None

    return Opportunity(
        listing=listing,

        median_price=median_price,
        comparable_count=len(prices),

        discount_toman=discount_toman,
        discount_percent=discount_percent,

        scope=scope,

        confidence=confidence_label(
            discount_percent,
            len(prices),
            same_city,
        ),
    )


# =========================================================
# Telegram presentation
# =========================================================

def format_toman(
    value: int | None,
) -> str:
    if value is None:
        return "نامشخص"

    return (
        f"{value:,}"
        .replace(",", "٬")
        + " تومان"
    )


def format_mileage(
    value: int | None,
) -> str:
    if value is None:
        return "نامشخص"

    if value == 0:
        return "صفر"

    return (
        f"{value:,}"
        .replace(",", "٬")
        + " کیلومتر"
    )


def build_telegram_caption(
    opportunity: Opportunity,
) -> str:
    listing = opportunity.listing

    title = html.escape(
        listing.title
    )

    location = "، ".join(
        html.escape(value)
        for value in (
            listing.city,
            listing.district,
        )
        if value
    )

    if not location:
        location = "موقعیت نامشخص"

    warning = ""

    if listing.severe_damage:
        warning = (
            "\n⚠️ <b>"
            "در توضیحات نشانه‌ای از آسیب جدی دیده شده؛ "
            "بررسی دقیق ضروری است."
            "</b>"
        )

    caption = (
        "🔥 <b>فرصت خرید احتمالی زیر قیمت بازار</b>\n\n"

        f"🚘 <b>{title}</b>\n"

        f"📍 {location}\n"

        f"📅 مدل: "
        f"<b>{listing.year or 'نامشخص'}</b>\n"

        f"🛣 کارکرد: "
        f"<b>{format_mileage(listing.mileage)}</b>\n"

        f"🎨 بدنه: "
        f"<b>{BODY_LABELS.get(listing.body_class, 'نامشخص')}</b>\n\n"

        f"💰 قیمت آگهی: "
        f"<b>{format_toman(listing.price_toman)}</b>\n"

        f"📊 میانه {opportunity.comparable_count} "
        f"آگهی مشابه: "
        f"<b>{format_toman(opportunity.median_price)}</b>\n"

        f"✅ پایین‌تر از میانه: "
        f"<b>{format_toman(opportunity.discount_toman)}</b> "
        f"({opportunity.discount_percent:.1f}٪)\n"

        f"🧭 دامنه مقایسه: "
        f"{opportunity.scope}\n"

        f"🔎 اعتبار آماری: "
        f"<b>{opportunity.confidence}</b>"

        f"{warning}\n\n"

        "⚠️ این نتیجه فقط تحلیل قیمت آگهی‌هاست؛ "
        "اصالت مدارک، سلامت فنی و بدنه و واقعی‌بودن فروشنده "
        "باید حضوری بررسی شود.\n\n"

        f"🔗 <a href=\"{html.escape(listing.url)}\">"
        "مشاهده آگهی در دیوار"
        "</a>"
    )

    return caption[:4000]


# =========================================================
# Scanner service
# =========================================================

@dataclass
class ScanStats:
    cards_seen: int = 0
    new_listings: int = 0
    excluded_listings: int = 0
    opportunities_sent: int = 0


class ScannerService:
    def __init__(
        self,
        config: Config,
    ):
        self.config = config

        self.db = Database(
            config.database_path
        )

        self.divar = DivarClient(
            config
        )

        self.telegram = (
            TelegramClient(config)
            if config.telegram_bot_token
            else None
        )

        self.alias_rules = load_alias_rules(
            config.model_aliases_path
        )

        self._run_lock = threading.Lock()

    def close(self) -> None:
        self.db.close()

    def collect_cards(
        self,
        pages: int,
    ) -> list[Mapping[str, Any]]:
        cards: list[Mapping[str, Any]] = []
        seen: set[str] = set()

        for page in range(
            1,
            pages + 1,
        ):
            try:
                payload = (
                    self.divar.fetch_search_page(
                        page
                    )
                )

            except (
                requests.RequestException,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                log.error(
                    "Search page %d failed: %s",
                    page,
                    exc,
                )

                if page == 1:
                    raise

                break

            page_cards = (
                self.divar.extract_cards(
                    payload
                )
            )

            log.info(
                "Divar page %d: %d cards",
                page,
                len(page_cards),
            )

            if not page_cards:
                break

            for card in page_cards:
                token = find_token(card)

                if (
                    token
                    and token not in seen
                ):
                    seen.add(token)
                    cards.append(card)

        return cards

    def hydrate_new_cards(
        self,
        cards: Sequence[Mapping[str, Any]],
        fetch_details: bool = True,
    ) -> tuple[list[Listing], int]:
        new_cards: list[Mapping[str, Any]] = []
        existing_cards: list[Mapping[str, Any]] = []

        for card in cards:
            token = find_token(card)

            if not token:
                continue

            if self.db.exists(token):
                existing_cards.append(card)

            else:
                new_cards.append(card)

        # اطلاعات آگهی‌های قدیمی را به‌روزرسانی می‌کند.
        for card in existing_cards:
            token = find_token(card)

            if not token:
                continue

            first_seen = self.db.first_seen(
                token
            )

            listing = Listing.from_objects(
                card,
                None,
                self.config,
                self.alias_rules,
                first_seen=first_seen,
            )

            if not listing:
                continue

            old = self.db.connection.execute(
                "SELECT * "
                "FROM listings "
                "WHERE token = ?",
                (token,),
            ).fetchone()

            if old:
                listing = merge_with_existing(
                    listing,
                    old,
                )

            self.db.upsert(listing)

        if not new_cards:
            return [], 0

        details: dict[
            str,
            Mapping[str, Any] | None,
        ] = {}

        if fetch_details:
            with ThreadPoolExecutor(
                max_workers=self.config.detail_workers
            ) as executor:

                futures = {
                    executor.submit(
                        self.divar.fetch_detail,
                        find_token(card) or "",
                    ): find_token(card)

                    for card in new_cards

                    if find_token(card)
                }

                for future in as_completed(futures):
                    token = futures[future]

                    try:
                        details[token or ""] = (
                            future.result()
                        )

                    except Exception as exc:
                        log.warning(
                            "Detail worker failed for %s: %s",
                            token,
                            exc,
                        )

                        details[token or ""] = None

        listings: list[Listing] = []
        excluded_count = 0

        for card in new_cards:
            token = find_token(card)

            if not token:
                continue

            listing = Listing.from_objects(
                card,
                details.get(token),
                self.config,
                self.alias_rules,
            )

            if not listing:
                continue

            self.db.upsert(listing)
            listings.append(listing)

            if listing.excluded:
                excluded_count += 1

                log.debug(
                    "Excluded %s: %s",
                    listing.token,
                    listing.excluded_reason,
                )

        return listings, excluded_count

    def run_scan(
        self,
        pages: int | None = None,
        *,
        send: bool = True,
        mode: str = "scheduled",
    ) -> ScanStats:
        if not self._run_lock.acquire(
            blocking=False
        ):
            log.warning(
                "Previous scan is still running; "
                "skipping overlapping run"
            )

            return ScanStats()

        pages = (
            pages
            or self.config.scan_pages
        )

        run_id = self.db.begin_run(
            mode,
            pages,
        )

        stats = ScanStats()
        error_text: str | None = None

        try:
            log.info(
                "Starting %s scan: %d page(s)",
                mode,
                pages,
            )

            cards = self.collect_cards(
                pages
            )

            stats.cards_seen = len(cards)

            new_listings, excluded_count = (
                self.hydrate_new_cards(
                    cards,
                    fetch_details=True,
                )
            )

            stats.new_listings = len(
                new_listings
            )

            stats.excluded_listings = (
                excluded_count
            )

            log.info(
                "Scan parsed %d cards; "
                "%d new; %d excluded",
                stats.cards_seen,
                stats.new_listings,
                stats.excluded_listings,
            )

            if send:
                if not self.telegram:
                    raise ValueError(
                        "Telegram is not configured"
                    )

                opportunities: list[
                    Opportunity
                ] = []

                for listing in new_listings:
                    opportunity = analyze_listing(
                        listing,
                        self.db,
                        self.config,
                    )

                    if opportunity:
                        opportunities.append(
                            opportunity
                        )

                opportunities.sort(
                    key=lambda item: (
                        item.discount_percent
                    ),
                    reverse=True,
                )

                selected = opportunities[
                    :self.config.max_messages_per_run
                ]

                for opportunity in selected:
                    try:
                        self.telegram.send_opportunity(
                            opportunity
                        )

                        self.db.mark_sent(
                            opportunity
                        )

                        stats.opportunities_sent += 1

                        log.info(
                            "Sent %s: %.1f%% below median",
                            opportunity.listing.token,
                            opportunity.discount_percent,
                        )

                        time.sleep(1.0)

                    except (
                        requests.RequestException,
                        RuntimeError,
                    ) as exc:
                        log.error(
                            "Telegram send failed for %s: %s",
                            opportunity.listing.token,
                            exc,
                        )

                log.info(
                    "Opportunities sent: %d",
                    stats.opportunities_sent,
                )

            return stats

        except Exception as exc:
            error_text = (
                f"{type(exc).__name__}: {exc}"
            )

            log.exception(
                "Scan failed"
            )

            raise

        finally:
            self.db.finish_run(
                run_id,
                stats.cards_seen,
                stats.new_listings,
                stats.excluded_listings,
                stats.opportunities_sent,
                error_text,
            )

            self._run_lock.release()

    def bootstrap_if_needed(self) -> None:
        if not self.config.auto_bootstrap:
            return

        if self.db.count_listings() > 0:
            return

        log.info(
            "Empty database detected. "
            "Bootstrapping %d pages "
            "without Telegram messages.",
            self.config.initial_bootstrap_pages,
        )

        self.run_scan(
            pages=(
                self.config.initial_bootstrap_pages
            ),
            send=False,
            mode="bootstrap",
        )

        log.info(
            "Bootstrap complete. "
            "Database now has %d listings.",
            self.db.count_listings(),
        )


def merge_with_existing(
    new: Listing,
    old: sqlite3.Row,
) -> Listing:
    """اطلاعات کامل قبلی را در صورت ناقص بودن کارت جدید حفظ می‌کند."""

    return replace(
        new,

        title=(
            new.title
            if new.title != "آگهی خودرو"
            else old["title"]
        ),

        model_key=(
            new.model_key
            or old["model_key"]
        ),

        year=(
            new.year
            if new.year is not None
            else old["year"]
        ),

        mileage=(
            new.mileage
            if new.mileage is not None
            else old["mileage"]
        ),

        body_class=(
            new.body_class
            if new.body_class != "unknown"
            else old["body_class"]
        ),

        price_toman=(
            new.price_toman
            if new.price_toman is not None
            else old["price_toman"]
        ),

        city=(
            new.city
            or old["city"]
        ),

        district=(
            new.district
            or old["district"]
        ),

        image_url=(
            new.image_url
            or old["image_url"]
        ),

        description=(
            new.description
            if len(new.description)
            > len(old["description"])
            else old["description"]
        ),

        excluded=bool(
            new.excluded
            or old["excluded"]
        ),

        excluded_reason=(
            new.excluded_reason
            or old["excluded_reason"]
        ),

        severe_damage=bool(
            new.severe_damage
            or old["severe_damage"]
        ),

        first_seen=old["first_seen"],

        source_json=(
            new.source_json
            if len(new.source_json)
            > len(old["source_json"])
            else old["source_json"]
        ),
    )


# =========================================================
# Command line
# =========================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select Car Divar bargain detector"
        ),

        formatter_class=(
            argparse.ArgumentDefaultsHelpFormatter
        ),
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan and exit",
    )

    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help=(
            "Build the comparison database "
            "without sending Telegram messages"
        ),
    )

    parser.add_argument(
        "--pages",
        type=int,
        help="Override number of search pages",
    )

    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help=(
            "Validate Telegram bot token and exit"
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Scan and store ads but do not send "
            "Telegram messages"
        ),
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    config = Config.from_env()

    setup_logging(config)

    telegram_required = (
        not args.bootstrap
        and not args.dry_run
    )

    config.validate(
        telegram_required=telegram_required
    )

    service = ScannerService(config)

    try:
        if args.test_telegram:
            if not service.telegram:
                raise ValueError(
                    "Telegram is not configured"
                )

            service.telegram.test()

            print(
                "Telegram connection is valid."
            )

            return 0

        if args.bootstrap:
            pages = (
                args.pages
                or config.initial_bootstrap_pages
            )

            service.run_scan(
                pages=pages,
                send=False,
                mode="bootstrap-manual",
            )

            return 0

        service.bootstrap_if_needed()

        if args.once or args.dry_run:
            service.run_scan(
                pages=(
                    args.pages
                    or config.scan_pages
                ),

                send=not args.dry_run,

                mode=(
                    "once"
                    if args.once
                    else "dry-run"
                ),
            )

            return 0

        stop_event = threading.Event()

        def stop_service(
            signum: int,
            frame: Any,
        ) -> None:
            del frame

            log.info(
                "Signal %s received; shutting down",
                signum,
            )

            stop_event.set()

        signal.signal(
            signal.SIGTERM,
            stop_service,
        )

        signal.signal(
            signal.SIGINT,
            stop_service,
        )

        if config.run_on_start:
            service.run_scan(
                send=True,
                mode="startup",
            )

        interval_seconds = (
            config.check_interval_hours
            * 3600
        )

        log.info(
            "Scheduler active: every %.1f hours",
            config.check_interval_hours,
        )

        while not stop_event.wait(
            interval_seconds
        ):
            try:
                service.run_scan(
                    send=True,
                    mode="scheduled",
                )

            except Exception:
                log.exception(
                    "Scheduled scan failed; "
                    "the service will retry "
                    "next interval"
                )

        return 0

    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
