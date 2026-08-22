#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


LOGGER = logging.getLogger("accurate_average.collectors")
SYSTEM = "[ACCURATE-SYSTEM]"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BLOCK_STATUSES = {403, 429}

BLOCK_MARKERS = (
    "لطفاً تأیید کنید ربات نیستید",
    "لطفا تایید کنید ربات نیستید",
    "تأیید کنید ربات نیستید",
    "تایید کنید ربات نیستید",
    "درخواست های بیش از حد",
    "درخواست‌های بیش از حد",
    "تعداد درخواست های شما بیش از حد",
    "دسترسی شما محدود شده",
    "فعالیت غیرعادی",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "too many requests",
    "access denied",
    "captcha",
)

BLOCKED_PHRASES = (
    "اقساط",
    "اقساطی",
    "قسطی",
    "لیزینگ",
    "پیش پرداخت",
    "پیش‌پرداخت",
    "ثبت نام",
    "ثبتنام",
    "پیش فروش",
    "پیش‌فروش",
    "وام",
    "چکی",
    "اعتباری",
    "معاوضه",
    "تهاتر",
    "شرایطی",
    "حواله",
    "توافقی",
    "تماس بگیرید",
    "قیمت در تماس",
    "قیمت تماس",
    "اوراقی",
    "سوخته",
    "فروش قطعات",
    "قطعه فروشی",
    "قطعه‌ای",
    "تصادفی",
    "تعویضی",
)

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ENGLISH_DIGITS = "0123456789"

DIGIT_TRANS = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS,
    ENGLISH_DIGITS + ENGLISH_DIGITS,
)

MODEL_ALIASES: dict[str, tuple[str, str]] = {
    "پژو 206": ("Peugeot", "206"),
    "206 sd": ("Peugeot", "206 SD"),
    "206": ("Peugeot", "206"),

    "پژو 207i": ("Peugeot", "207"),
    "پژو 207": ("Peugeot", "207"),
    "207i": ("Peugeot", "207"),
    "207": ("Peugeot", "207"),

    "پژو 405": ("Peugeot", "405"),
    "405": ("Peugeot", "405"),

    "پژو 2008": ("Peugeot", "2008"),

    "پژو پارس": ("Peugeot", "Pars"),
    "پرشیا": ("Peugeot", "Pars"),
    "پارس": ("Peugeot", "Pars"),

    "سمند سورن": ("Iran Khodro", "Samand"),
    "سورن پلاس": ("Iran Khodro", "Samand"),
    "سورن": ("Iran Khodro", "Samand"),
    "سمند": ("Iran Khodro", "Samand"),

    "دنا پلاس": ("Iran Khodro", "Dena"),
    "دنا": ("Iran Khodro", "Dena"),

    "تارا": ("Iran Khodro", "Tara"),
    "رانا": ("Iran Khodro", "Runna"),
    "ریرا": ("Iran Khodro", "Reera"),

    "آریسان": ("Iran Khodro", "Arisun"),
    "اریسان": ("Iran Khodro", "Arisun"),

    "پراید": ("Saipa", "Pride"),
    "تیبا": ("Saipa", "Tiba"),
    "ساینا": ("Saipa", "Saina"),
    "کوییک": ("Saipa", "Quick"),
    "کوئیک": ("Saipa", "Quick"),
    "شاهین": ("Saipa", "Shahin"),
    "اطلس": ("Saipa", "Atlas"),
    "سهند": ("Saipa", "Sahand"),

    "ال 90": ("Renault", "L90"),
    "ال90": ("Renault", "L90"),
    "ال نود": ("Renault", "L90"),
    "تندر 90": ("Renault", "L90"),
    "مگان": ("Renault", "Megane"),
    "ساندرو": ("Renault", "Sandero"),

    "سراتو": ("Kia", "Cerato"),
    "اپتیما": ("Kia", "Optima"),
    "اسپورتیج": ("Kia", "Sportage"),
    "سورنتو": ("Kia", "Sorento"),

    "النترا": ("Hyundai", "Elantra"),
    "سوناتا هیبرید": ("Hyundai", "Sonata Hybrid"),
    "سوناتا": ("Hyundai", "Sonata"),
    "توسان": ("Hyundai", "Tucson"),
    "سانتافه": ("Hyundai", "Santa Fe"),
    "santafe": ("Hyundai", "Santa Fe"),
    "i30": ("Hyundai", "i30"),

    "کمری": ("Toyota", "Camry"),
    "کرولا": ("Toyota", "Corolla"),
    "پرادو": ("Toyota", "Prado"),
    "لندکروزر": ("Toyota", "Land Cruiser"),
    "راف 4": ("Toyota", "RAV4"),
    "راو4": ("Toyota", "RAV4"),
    "rav4": ("Toyota", "RAV4"),
    "یاریس": ("Toyota", "Yaris"),
    "پریوس": ("Toyota", "Prius"),

    "مزدا 3": ("Mazda", "3"),
    "مزدا3": ("Mazda", "3"),
    "مزدا 2": ("Mazda", "2"),
    "وانت مزدا": ("Mazda", "Pickup"),

    "x22 pro": ("MVM", "X22 Pro"),
    "x22pro": ("MVM", "X22 Pro"),
    "x22": ("MVM", "X22"),

    "x33 cross": ("MVM", "X33 Cross"),
    "x33cross": ("MVM", "X33 Cross"),
    "x33": ("MVM", "X33"),

    "x55 pro": ("MVM", "X55 Pro"),
    "x55": ("MVM", "X55"),

    "315هاچ بک": ("MVM", "315 Hatchback"),
    "315 hatchback": ("MVM", "315 Hatchback"),

    "تیگو 7": ("Chery", "Tiggo 7"),
    "تیگو 8": ("Chery", "Tiggo 8"),

    "آریزو 5": ("Chery", "Arrizo 5"),
    "اریزو 5": ("Chery", "Arrizo 5"),

    "آریزو 6": ("Fownix", "Arrizo 6"),
    "اریزو 6": ("Fownix", "Arrizo 6"),
    "fx": ("Fownix", "FX"),

    "جک s3": ("JAC", "S3"),
    "jac s3": ("JAC", "S3"),

    "جک s5": ("JAC", "S5"),
    "jac s5": ("JAC", "S5"),

    "جک j4": ("JAC", "J4"),
    "jac j4": ("JAC", "J4"),

    "لاماری ایما": ("Lamari", "Eama"),
    "لاماری": ("Lamari", "Eama"),

    "فیدلیتی": ("Fidelity", "Fidelity"),
    "دیگنیتی": ("Dignity", "Dignity"),

    "هایما s5": ("Haima", "S5"),
    "هایما s7": ("Haima", "S7"),

    "پورشه کاین": ("Porsche", "Cayenne"),
    "cayenne": ("Porsche", "Cayenne"),

    "bmw x3": ("BMW", "X3"),
    "x3": ("BMW", "X3"),

    "bmw 125i": ("BMW", "125i"),
    "125i": ("BMW", "125i"),

    "bmw 528i": ("BMW", "528i"),
    "528i": ("BMW", "528i"),

    "تیگوان": ("Volkswagen", "Tiguan"),
    "tiguan": ("Volkswagen", "Tiguan"),

    "آلفارومئو میتو": ("Alfa Romeo", "Mito"),
    "الفارومئو میتو": ("Alfa Romeo", "Mito"),
    "mito": ("Alfa Romeo", "Mito"),

    "سانگ یانگ رکستون": ("SsangYong", "Rexton"),
    "رکستون": ("SsangYong", "Rexton"),

    "میتسوبیشی asx": ("Mitsubishi", "ASX"),
    "asx": ("Mitsubishi", "ASX"),

    "میتسوبیشی لنسر": ("Mitsubishi", "Lancer"),
    "لنسر": ("Mitsubishi", "Lancer"),

    "فولکس تیراک": ("Volkswagen", "T-Roc"),
    "تیراک": ("Volkswagen", "T-Roc"),

    "زد ایکس اتو g9": ("ZX Auto", "G9"),
    "g9": ("ZX Auto", "G9"),

    "لیفان x70": ("Lifan", "X70"),
}

TRIM_ALIASES = {
    "تیپ 1": "تیپ 1",
    "تیپ یک": "تیپ 1",

    "تیپ 2": "تیپ 2",
    "تیپ دو": "تیپ 2",

    "تیپ 3": "تیپ 3",
    "تیپ سه": "تیپ 3",

    "تیپ 5": "تیپ 5",
    "تیپ پنج": "تیپ 5",

    "تیپ 6": "تیپ 6",
    "تیپ شش": "تیپ 6",

    "v8": "V8",
    "v9": "V9",

    "glx": "GLX",
    "slx": "SLX",
    "lx": "LX",
    "ef7": "EF7",

    "سورن پلاس": "سورن پلاس",
    "سورن": "سورن",

    "دنده ای": "دنده‌ای",
    "دنده‌ای": "دنده‌ای",

    "اتومات": "اتوماتیک",
    "اتوماتیک": "اتوماتیک",

    "mc": "MC",

    "پلاس توربو اتوماتیک": "پلاس توربو اتوماتیک",
    "پلاس توربو": "پلاس توربو",
    "پلاس": "پلاس",

    "e1": "E1",
    "e2": "E2",

    "r پلاس": "R پلاس",
    "r": "R",
    "s": "S",
    "rs": "RS",

    "g": "G",
    "gl": "GL",

    "1600": "1600",
    "2000": "2000",

    "premium": "Premium",
    "پریمیوم": "Premium",

    "ie": "IE",

    "pro": "Pro",
    "پرو": "Pro",

    "gls": "GLS",

    "فول عمان": "فول عمان",

    "تیپ c": "تیپ C",
    "تیپ 4": "تیپ 4",

    "فول yf": "فول YF",
}

TRIM_REQUIRED_MODELS = {
    ("Peugeot", "206"),
    ("Peugeot", "206 SD"),
    ("Peugeot", "207"),
    ("Peugeot", "405"),
    ("Peugeot", "Pars"),

    ("Iran Khodro", "Samand"),
    ("Iran Khodro", "Dena"),

    ("Saipa", "Pride"),
    ("Saipa", "Quick"),
    ("Saipa", "Shahin"),

    ("Renault", "L90"),

    ("Kia", "Cerato"),

    ("MVM", "X55"),
    ("MVM", "X55 Pro"),
}

TELEGRAM_CHANNELS = (
    "hmexpo",
    "formulagallery",
    "zh_classic_car",
    "maserati4",
    "karnameh_com",
    "namayeshgahddarann",
    "farbodcarhouse",
    "select_carr",
)


@dataclass(
    frozen=True,
    slots=True,
)
class NormalizedVehicleListing:
    source: str
    source_ad_id: str
    url: str
    title: str

    brand: str
    model: str
    trim: str

    model_year: int
    condition: str

    mileage: int | None
    price: int

    collected_at: str
    raw_text: str = ""

    @property
    def source_key(
        self,
    ) -> str:
        return (
            f"{self.source}|"
            f"{self.source_ad_id}"
        )

    @property
    def comparison_key(
        self,
    ) -> str:
        return "|".join(
            (
                normalize_for_match(
                    self.brand
                ),
                normalize_for_match(
                    self.model
                ),
                normalize_for_match(
                    self.trim
                ),
                str(
                    self.model_year
                ),
                self.condition,
            )
        )

    @property
    def fingerprint(
        self,
    ) -> str:

        basis = "|".join(
            (
                normalize_for_match(
                    self.brand
                ),
                normalize_for_match(
                    self.model
                ),
                normalize_for_match(
                    self.trim
                ),
                str(
                    self.model_year
                ),
                self.condition,
                str(
                    self.price
                ),
                str(
                    self.mileage
                    if self.mileage is not None
                    else ""
                ),
                normalize_for_match(
                    self.title
                )[:100],
            )
        )

        return hashlib.sha256(
            basis.encode(
                "utf-8"
            )
        ).hexdigest()


@dataclass(
    frozen=True,
    slots=True,
)
class CollectorResult:
    source: str
    fetched: int
    accepted: int
    rejected: int
    duplicates: int
    blocked: bool
    error: str | None
    listings: tuple[
        NormalizedVehicleListing,
        ...,
    ]


@dataclass(
    frozen=True,
    slots=True,
)
class CollectorContext:
    runtime_dir: Path
    diagnostics_dir: Path
    timeout_seconds: int = 30
    initial_delay_min: int = 20
    initial_delay_max: int = 120
    dry_network_delay: bool = False


class SourceBlockedError(
    RuntimeError
):
    pass


def normalize_digits(
    value: object,
) -> str:

    return str(
        value or ""
    ).translate(
        DIGIT_TRANS
    )


def normalize_for_match(
    value: object,
) -> str:

    text = (
        normalize_digits(
            value
        )
        .lower()
        .replace(
            "\u200c",
            " ",
        )
        .replace(
            "\u200f",
            " ",
        )
        .replace(
            "\u200e",
            " ",
        )
        .replace(
            "ي",
            "ی",
        )
        .replace(
            "ك",
            "ک",
        )
        .replace(
            "ۀ",
            "ه",
        )
        .replace(
            "ة",
            "ه",
        )
    )

    text = re.sub(
        (
            r"(?<=[A-Za-zآ-ی])"
            r"(?=\d)|"
            r"(?<=\d)"
            r"(?=[A-Za-zآ-ی])"
        ),
        " ",
        text,
    )

    text = re.sub(
        r"[_/\\|,:;؛،!?؟()\[\]{}]+",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def _integer(
    raw: str,
) -> int | None:

    cleaned = normalize_digits(
        raw
    )

    cleaned = re.sub(
        r"[,،٬.\s]",
        "",
        cleaned,
    )

    if not cleaned.isdigit():
        return None

    return int(
        cleaned
    )


def parse_price(
    text: str,
) -> int | None:

    normalized = normalize_for_match(
        text
    )

    if any(
        phrase in normalized
        for phrase in (
            "توافقی",
            "تماس بگیرید",
            "قیمت در تماس",
            "قیمت تماس",
        )
    ):
        return None

    match = re.search(
        (
            r"([0-9]"
            r"[0-9,،٬.\s]{5,22})"
            r"\s*"
            r"(?:تومان|تومن)"
        ),
        normalized,
    )

    if match:
        value = _integer(
            match.group(
                1
            )
        )

        if (
            value
            and 1_000_000
            <= value
            <= 500_000_000_000
        ):
            return value

    match = re.search(
        (
            r"([0-9]"
            r"[0-9,،٬.\s]{0,18})"
            r"\s*"
            r"(?:میلیارد|ملیارد)"
        ),
        normalized,
    )

    if match:
        number = _integer(
            match.group(
                1
            )
        )

        if number:
            value = (
                number
                * 1_000_000_000
            )

            if (
                1_000_000
                <= value
                <= 500_000_000_000
            ):
                return value

    match = re.search(
        (
            r"([0-9]"
            r"[0-9,،٬.\s]{0,18})"
            r"\s*"
            r"(?:میلیون|ملیون)"
        ),
        normalized,
    )

    if match:
        number = _integer(
            match.group(
                1
            )
        )

        if number:
            value = (
                number
                * 1_000_000
            )

            if (
                1_000_000
                <= value
                <= 500_000_000_000
            ):
                return value

    for raw in re.findall(
        r"(?<!\d)(\d{7,12})(?!\d)",
        normalized,
    ):
        value = int(
            raw
        )

        if (
            1_000_000
            <= value
            <= 500_000_000_000
        ):
            return value

    return None


def parse_year(
    text: str,
) -> int | None:

    normalized = normalize_for_match(
        text
    )

    candidates: list[
        str
    ] = []

    candidates.extend(
        re.findall(
            (
                r"(?:مدل|سال)"
                r"\s*"
                r"([0-9]{2,4})"
            ),
            normalized,
        )
    )

    candidates.extend(
        re.findall(
            (
                r"(?<!\d)"
                r"(13\d{2}|14\d{2}|20\d{2})"
                r"(?!\d)"
            ),
            normalized,
        )
    )

    for raw in candidates:
        year = int(
            raw
        )

        if len(
            raw
        ) == 2:

            if (
                70
                <= year
                <= 99
            ):
                return (
                    1300
                    + year
                )

            if (
                0
                <= year
                <= 30
            ):
                return (
                    1400
                    + year
                )

            continue

        if (
            1300
            <= year
            <= 1499
        ):
            return year

        if (
            2000
            <= year
            <= 2100
        ):
            return year

    return None


def parse_year_from_url(
    url: str,
) -> int | None:

    path = normalize_digits(
        urlsplit(
            url
        ).path
    )

    matches = re.findall(
        (
            r"(?:^|[-_/])"
            r"(13\d{2}|14\d{2}|20\d{2})"
            r"(?:$|[-_/])"
        ),
        path,
    )

    for raw in reversed(
        matches
    ):
        year = int(
            raw
        )

        if (
            1300
            <= year
            <= 1499
        ):
            return year

        if (
            2000
            <= year
            <= 2100
        ):
            return year

    return None


def parse_mileage(
    text: str,
) -> int | None:

    normalized = normalize_for_match(
        text
    )

    if re.search(
        (
            r"کارکرد\s*صفر"
            r"(?:\s*"
            r"(?:کیلومتر|km))?"
        ),
        normalized,
    ):
        return 0

    if re.search(
        (
            r"(?<!\w)"
            r"صفر\s*"
            r"(?:کیلومتر|km)"
            r"(?!\w)"
        ),
        normalized,
    ):
        return 0

    if re.search(
        (
            r"(?<!\d)"
            r"0\s*"
            r"(?:کیلومتر|km)"
            r"(?!\w)"
        ),
        normalized,
    ):
        return 0

    patterns = (
        (
            r"کارکرد\s*"
            r"([0-9]"
            r"[0-9,،٬.\s]{0,15})"
            r"(?:\s*"
            r"(?:کیلومتر|کیلو|km))?"
        ),
        (
            r"([0-9]"
            r"[0-9,،٬.\s]{1,15})"
            r"\s*"
            r"(?:کیلومتر|کیلو|km)"
        ),
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized,
            re.IGNORECASE,
        )

        if not match:
            continue

        mileage = _integer(
            match.group(
                1
            )
        )

        if (
            mileage is not None
            and 0
            <= mileage
            <= 2_000_000
        ):
            return mileage

    return None


def parse_condition(
    text: str,
    mileage: int | None,
) -> str:

    normalized = normalize_for_match(
        text
    )

    if (
        mileage == 0
        or any(
            phrase
            in normalized
            for phrase in (
                "صفر کیلومتر",
                "صفرکیلومتر",
                "کارکرد صفر",
            )
        )
    ):
        return "zero"

    if (
        mileage is not None
        and mileage > 0
    ):
        return "used"

    if any(
        phrase
        in normalized
        for phrase in (
            "کارکرده",
            "دست دوم",
        )
    ):
        return "used"

    return "unknown"


def find_blocked_phrase(
    text: str,
) -> str | None:

    normalized = normalize_for_match(
        text
    )

    for phrase in BLOCKED_PHRASES:
        if (
            normalize_for_match(
                phrase
            )
            in normalized
        ):
            return phrase

    return None


def _contains_alias(
    normalized_text: str,
    normalized_alias: str,
) -> bool:

    if not normalized_alias:
        return False

    if re.fullmatch(
        r"[a-z0-9 ]+",
        normalized_alias,
    ):
        return bool(
            re.search(
                (
                    r"(?<![a-z0-9])"
                    + re.escape(
                        normalized_alias
                    )
                    + r"(?![a-z0-9])"
                ),
                normalized_text,
            )
        )

    return (
        normalized_alias
        in normalized_text
    )


def extract_vehicle_identity(
    text: str,
) -> tuple[
    str | None,
    str | None,
    str,
]:

    normalized = normalize_for_match(
        text
    )

    best_pair: tuple[
        str,
        str,
    ] | None = None

    best_length = -1

    for (
        alias,
        pair,
    ) in MODEL_ALIASES.items():

        normalized_alias = (
            normalize_for_match(
                alias
            )
        )

        if (
            _contains_alias(
                normalized,
                normalized_alias,
            )
            and len(
                normalized_alias
            ) > best_length
        ):
            best_pair = pair

            best_length = len(
                normalized_alias
            )

    if best_pair is None:
        return (
            None,
            None,
            "",
        )

    brand, model = best_pair

    trim = ""
    trim_length = -1

    for (
        alias,
        canonical,
    ) in TRIM_ALIASES.items():

        normalized_alias = (
            normalize_for_match(
                alias
            )
        )

        if (
            _contains_alias(
                normalized,
                normalized_alias,
            )
            and len(
                normalized_alias
            ) > trim_length
        ):
            trim = canonical
            trim_length = len(
                normalized_alias
            )

    if (
        brand,
        model,
    ) == (
        "Peugeot",
        "Pars",
    ):
        if (
            "پارس سال"
            in normalized
        ):
            trim = "سال"

    if (
        brand,
        model,
    ) == (
        "Saipa",
        "Pride",
    ):
        match = re.search(
            (
                r"(?:پراید\s*)?"
                r"(111|131|132|151)"
            ),
            normalized,
        )

        if match:
            trim = match.group(
                1
            )

    if (
        brand,
        model,
    ) == (
        "Kia",
        "Cerato",
    ):
        match = re.search(
            (
                r"(?:سراتو\s*)?"
                r"(1600|2000)"
            ),
            normalized,
        )

        if match:
            trim = match.group(
                1
            )

    if (
        brand,
        model,
    ) in {
        (
            "MVM",
            "X55",
        ),
        (
            "MVM",
            "X55 Pro",
        ),
    }:

        if (
            "premium"
            in normalized
            or "پریمیوم"
            in normalized
        ):
            trim = "Premium"

        elif re.search(
            r"(?<![a-z])ie(?![a-z])",
            normalized,
        ):
            trim = "IE"

        elif (
            "pro"
            in normalized
            or "پرو"
            in normalized
        ):
            trim = "Pro"

    return (
        brand,
        model,
        trim,
    )


def canonical_url(
    url: str,
) -> str:

    parsed = urlsplit(
        str(
            url or ""
        ).strip()
    )

    if not parsed.scheme:
        return str(
            url or ""
        ).strip()

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            (
                parsed.path.rstrip(
                    "/"
                )
                or "/"
            ),
            "",
            "",
        )
    )


def _source_sanitize_text(
    source: str,
    text: str,
) -> str:

    if (
        source
        == "hamrah_mechanic"
    ):
        cleaned = text

        for phrase in (
            "قابل معاوضه",
            "امکان خرید اقساطی",
            "اقساط 1 تا 60 ماه",
            "اقساط ۱ تا ۶۰ ماه",
            "کارشناسی شده",
            "گارانتی 7 روزه",
            "گارانتی ۷ روزه",
            "درحال کارشناسی",
        ):
            cleaned = cleaned.replace(
                phrase,
                " ",
            )

        return re.sub(
            r"\s+",
            " ",
            cleaned,
        ).strip()

    return text


def normalize_listing(
    *,
    source: str,
    source_ad_id: str,
    url: str,
    title: str,
    raw_text: str,
) -> tuple[
    NormalizedVehicleListing | None,
    str,
]:

    clean_url = canonical_url(
        url
    )

    text = (
        _source_sanitize_text(
            source,
            (
                f"{title}\n"
                f"{raw_text}"
            ).strip(),
        )
    )

    blocked = find_blocked_phrase(
        text
    )

    if blocked:
        return (
            None,
            (
                "blocked_phrase:"
                f"{blocked}"
            ),
        )

    price = parse_price(
        text
    )

    if price is None:
        return (
            None,
            "invalid_price",
        )

    year = (
        parse_year(
            text
        )
        or parse_year_from_url(
            clean_url
        )
    )

    if year is None:
        return (
            None,
            "invalid_year",
        )

    (
        brand,
        model,
        trim,
    ) = extract_vehicle_identity(
        (
            text
            + " "
            + clean_url
        )
    )

    if (
        not brand
        or not model
    ):
        return (
            None,
            "unknown_vehicle",
        )

    if (
        (
            brand,
            model,
        )
        in TRIM_REQUIRED_MODELS
        and not trim
    ):
        return (
            None,
            "missing_trim",
        )

    mileage = parse_mileage(
        text
    )

    condition = (
        parse_condition(
            text,
            mileage,
        )
    )

    if (
        source
        == "formula"
        and condition
        == "unknown"
    ):
        now = datetime.now(
            timezone.utc
        )

        if (
            1300
            <= year
            <= 1499
        ):
            approximate_current_solar = (
                now.year
                - 621
            )

            if (
                year
                <= approximate_current_solar
                - 2
            ):
                condition = "used"

        elif (
            2000
            <= year
            <= 2100
            and year
            <= now.year
            - 2
        ):
            condition = "used"

    if (
        condition
        == "unknown"
    ):
        return (
            None,
            "unknown_condition",
        )

    clean_id = str(
        source_ad_id
        or ""
    ).strip()

    if not clean_id:
        clean_id = (
            hashlib.sha256(
                clean_url.encode(
                    "utf-8"
                )
            )
            .hexdigest()[:24]
        )

    listing = (
        NormalizedVehicleListing(
            source=source,
            source_ad_id=clean_id,
            url=clean_url,
            title=re.sub(
                r"\s+",
                " ",
                title,
            ).strip()[:300],
            brand=brand,
            model=model,
            trim=trim,
            model_year=year,
            condition=condition,
            mileage=mileage,
            price=price,
            collected_at=(
                datetime.now(
                    timezone.utc
                ).isoformat()
            ),
            raw_text=text[:4000],
        )
    )

    return (
        listing,
        "accepted",
    )


class BaseCollector:
    source = "base"
    is_web = True

    def __init__(
        self,
        context: CollectorContext,
    ) -> None:

        self.context = context

        self.session = (
            requests.Session()
        )

        self.session.headers.update(
            HEADERS
        )

        self.cookie_path = (
            context.runtime_dir
            / "cookies"
            / f"{self.source}.json"
        )

        self._load_cookies()

    def close(
        self,
    ) -> None:

        self._save_cookies()

        self.session.close()

    def _initial_delay(
        self,
    ) -> float:

        delay = (
            random.uniform(
                self.context
                .initial_delay_min,
                self.context
                .initial_delay_max,
            )
        )

        LOGGER.info(
            (
                "%s Source=%s "
                "RequestPacing "
                "delay_seconds=%.2f"
            ),
            SYSTEM,
            self.source,
            delay,
        )

        if not (
            self.context
            .dry_network_delay
        ):
            time.sleep(
                delay
            )

        return delay

    def _visible_marker(
        self,
        html: str,
    ) -> str | None:

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        for element in soup.find_all(
            [
                "script",
                "style",
                "noscript",
                "svg",
            ]
        ):
            element.decompose()

        visible = normalize_for_match(
            " ".join(
                soup.stripped_strings
            )
        )

        for marker in BLOCK_MARKERS:
            if (
                normalize_for_match(
                    marker
                )
                in visible
            ):
                return marker

        return None

    def _get(
        self,
        url: str,
    ) -> str:

        self._initial_delay()

        try:
            response = (
                self.session.get(
                    url,
                    timeout=(
                        self.context
                        .timeout_seconds
                    ),
                    allow_redirects=True,
                )
            )

        except (
            requests.RequestException
        ) as exc:
            raise RuntimeError(
                (
                    f"{self.source} "
                    f"request failed: "
                    f"{exc}"
                )
            ) from exc

        self._save_cookies()

        marker = (
            self._visible_marker(
                response.text
            )
        )

        LOGGER.info(
            (
                "%s Source=%s "
                "status=%s "
                "final_url=%s "
                "bytes=%s "
                "marker=%r "
                "redirects=%s"
            ),
            SYSTEM,
            self.source,
            response.status_code,
            response.url,
            len(
                response.content
            ),
            marker,
            len(
                response.history
            ),
        )

        if (
            response.status_code
            in BLOCK_STATUSES
            or marker
        ):
            self._save_diagnostic(
                response.text,
                response.status_code,
                (
                    marker
                    or "blocking_status"
                ),
            )

            raise SourceBlockedError(
                (
                    f"{self.source} "
                    "blocked: "
                    f"status="
                    f"{response.status_code} "
                    f"marker={marker!r}"
                )
            )

        try:
            response.raise_for_status()

        except (
            requests.HTTPError
        ) as exc:
            self._save_diagnostic(
                response.text,
                response.status_code,
                "http_error",
            )

            raise RuntimeError(
                (
                    f"{self.source} "
                    "returned HTTP "
                    f"{response.status_code}"
                )
            ) from exc

        return response.text

    def _save_diagnostic(
        self,
        html: str,
        status: int,
        reason: str,
    ) -> None:

        directory = (
            self.context
            .diagnostics_dir
            / self.source
        )

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        timestamp = (
            datetime.now(
                timezone.utc
            )
            .strftime(
                "%Y%m%dT%H%M%SZ"
            )
        )

        safe_reason = re.sub(
            r"[^A-Za-z0-9_-]+",
            "_",
            str(
                reason
            ),
        )[:60]

        path = (
            directory
            / (
                f"{timestamp}-"
                f"{status}-"
                f"{safe_reason}.html"
            )
        )

        path.write_text(
            html[:500_000],
            encoding="utf-8",
        )

    def _load_cookies(
        self,
    ) -> None:

        if not (
            self.cookie_path
            .exists()
        ):
            return

        try:
            items = json.loads(
                self.cookie_path
                .read_text(
                    encoding="utf-8"
                )
            )

        except (
            OSError,
            json.JSONDecodeError,
        ):
            return

        if not isinstance(
            items,
            list,
        ):
            return

        for item in items:
            if (
                not isinstance(
                    item,
                    dict,
                )
                or not item.get(
                    "name"
                )
            ):
                continue

            self.session.cookies.set(
                item[
                    "name"
                ],
                item.get(
                    "value",
                    "",
                ),
                domain=(
                    item.get(
                        "domain"
                    )
                    or None
                ),
                path=(
                    item.get(
                        "path"
                    )
                    or "/"
                ),
            )

    def _save_cookies(
        self,
    ) -> None:

        self.cookie_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        items = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
            for cookie
            in self.session.cookies
        ]

        try:
            self.cookie_path.write_text(
                json.dumps(
                    items,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        except OSError:
            pass

    def collect(
        self,
    ) -> CollectorResult:
        raise NotImplementedError


class TelegramCollector(
    BaseCollector
):
    source = "telegram"
    is_web = False

    POST_RE = re.compile(
        (
            r'data-post="'
            r'([^"/]+)/'
            r'([0-9]+)"'
        )
    )

    TEXT_RE = re.compile(
        (
            r'<div class="'
            r'tgme_widget_message_text'
            r'[^"]*"[^>]*>'
            r'(.*?)'
            r'</div>'
        ),
        re.DOTALL,
    )

    def __init__(
        self,
        context: CollectorContext,
        channels: Iterable[
            str
        ] = TELEGRAM_CHANNELS,
    ) -> None:

        super().__init__(
            context
        )

        self.channels = tuple(
            channels
        )

    def collect(
        self,
    ) -> CollectorResult:

        listings: list[
            NormalizedVehicleListing
        ] = []

        fetched = 0
        rejected = 0
        duplicates = 0

        seen: set[
            str
        ] = set()

        errors: list[
            str
        ] = []

        blocked = False

        for channel in (
            self.channels
        ):
            try:
                html = self._get(
                    (
                        "https://t.me/s/"
                        f"{channel}"
                    )
                )

                markers = list(
                    self.POST_RE
                    .finditer(
                        html
                    )
                )

                for (
                    index,
                    marker,
                ) in enumerate(
                    markers
                ):

                    end = (
                        markers[
                            index + 1
                        ].start()
                        if (
                            index + 1
                            < len(
                                markers
                            )
                        )
                        else len(
                            html
                        )
                    )

                    block = html[
                        marker.start():
                        end
                    ]

                    text_match = (
                        self.TEXT_RE.search(
                            block
                        )
                    )

                    if not text_match:
                        continue

                    raw = re.sub(
                        r"<br\s*/?>",
                        "\n",
                        text_match.group(
                            1
                        ),
                        flags=re.I,
                    )

                    text = (
                        BeautifulSoup(
                            html_lib.unescape(
                                raw
                            ),
                            "html.parser",
                        )
                        .get_text(
                            "\n",
                            strip=True,
                        )
                    )

                    if not text:
                        continue

                    post_id = (
                        marker.group(
                            2
                        )
                    )

                    key = (
                        f"{channel}:"
                        f"{post_id}"
                    )

                    if key in seen:
                        duplicates += 1
                        continue

                    seen.add(
                        key
                    )

                    fetched += 1

                    (
                        listing,
                        reason,
                    ) = normalize_listing(
                        source=(
                            self.source
                        ),
                        source_ad_id=key,
                        url=(
                            "https://t.me/"
                            f"{channel}/"
                            f"{post_id}"
                        ),
                        title=(
                            text
                            .splitlines()[0]
                        ),
                        raw_text=text,
                    )

                    if listing:
                        listings.append(
                            listing
                        )

                    else:
                        rejected += 1

                        LOGGER.info(
                            (
                                "%s "
                                "Source=telegram "
                                "Reject=%s "
                                "post=%s"
                            ),
                            SYSTEM,
                            reason,
                            key,
                        )

            except (
                SourceBlockedError
            ) as exc:
                errors.append(
                    str(
                        exc
                    )
                )

                blocked = True
                break

            except Exception as exc:
                errors.append(
                    (
                        f"{channel}:"
                        f"{exc}"
                    )
                )

        return CollectorResult(
            source=self.source,
            fetched=fetched,
            accepted=len(
                listings
            ),
            rejected=rejected,
            duplicates=duplicates,
            blocked=blocked,
            error=(
                "; ".join(
                    errors
                )
                or None
            ),
            listings=tuple(
                listings
            ),
        )


class GenericListingCollector(
    BaseCollector
):
    listing_url = ""

    allowed_hosts: tuple[
        str,
        ...,
    ] = ()

    href_patterns: tuple[
        re.Pattern[str],
        ...,
    ] = ()

    card_mode = "anchor"

    def _is_candidate_href(
        self,
        href: str,
    ) -> bool:

        if not href:
            return False

        absolute = canonical_url(
            urljoin(
                self.listing_url,
                href,
            )
        )

        parsed = urlsplit(
            absolute
        )

        host = (
            parsed
            .netloc
            .lower()
        )

        if (
            self.allowed_hosts
            and not any(
                (
                    host
                    == allowed
                    or host.endswith(
                        "."
                        + allowed
                    )
                )
                for allowed
                in self.allowed_hosts
            )
        ):
            return False

        return any(
            pattern.search(
                parsed.path
            )
            for pattern
            in self.href_patterns
        )

    @staticmethod
    def _node_text(
        node,
    ) -> str:

        return re.sub(
            r"\s+",
            " ",
            " ".join(
                node.stripped_strings
            ),
        ).strip()

    def _extract_card_text(
        self,
        anchor,
    ) -> tuple[
        str,
        str,
    ]:

        anchor_text = (
            self._node_text(
                anchor
            )
        )

        title = (
            anchor_text[:300]
        )

        if (
            self.card_mode
            == "anchor"
        ):
            return (
                title,
                anchor_text,
            )

        node = anchor
        best = anchor_text

        for _ in range(
            7
        ):
            if (
                getattr(
                    node,
                    "parent",
                    None,
                )
                is None
            ):
                break

            node = node.parent

            text = self._node_text(
                node
            )

            if not text:
                continue

            if len(
                text
            ) > 1800:
                break

            if (
                15
                <= len(
                    text
                )
                <= 1800
            ):
                best = text

            if (
                parse_price(
                    text
                )
                is not None
                or any(
                    phrase
                    in normalize_for_match(
                        text
                    )
                    for phrase in (
                        "توافقی",
                        "تماس بگیرید",
                        "قیمت در تماس",
                    )
                )
            ):
                best = text
                break

        return (
            (
                title
                or best[:300]
            ),
            best,
        )

    def _extract_cards(
        self,
        html: str,
    ) -> list[
        tuple[
            str,
            str,
            str,
        ]
    ]:

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        found: list[
            tuple[
                str,
                str,
                str,
            ]
        ] = []

        seen: set[
            str
        ] = set()

        for anchor in soup.find_all(
            "a",
            href=True,
        ):
            href = str(
                anchor.get(
                    "href",
                    "",
                )
            ).strip()

            if not (
                self._is_candidate_href(
                    href
                )
            ):
                continue

            url = canonical_url(
                urljoin(
                    self.listing_url,
                    href,
                )
            )

            if url in seen:
                continue

            seen.add(
                url
            )

            (
                title,
                text,
            ) = self._extract_card_text(
                anchor
            )

            if not text:
                continue

            found.append(
                (
                    url,
                    title,
                    text,
                )
            )

        return found

    def _id_from_url(
        self,
        url: str,
    ) -> str:

        path = (
            urlsplit(
                url
            )
            .path
            .rstrip(
                "/"
            )
        )

        token = (
            path.split(
                "/"
            )[-1]
            if path
            else ""
        )

        return (
            token
            or hashlib.sha256(
                url.encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
        )

    def collect(
        self,
    ) -> CollectorResult:

        try:
            html = self._get(
                self.listing_url
            )

            cards = (
                self._extract_cards(
                    html
                )
            )

            if not cards:
                self._save_diagnostic(
                    html,
                    200,
                    "no_listing_cards",
                )

                LOGGER.info(
                    (
                        "%s Source=%s "
                        "NoPublicListingsOrSelectorMatch=true"
                    ),
                    SYSTEM,
                    self.source,
                )

            listings: list[
                NormalizedVehicleListing
            ] = []

            rejected = 0

            for (
                url,
                title,
                text,
            ) in cards:

                (
                    listing,
                    reason,
                ) = normalize_listing(
                    source=(
                        self.source
                    ),
                    source_ad_id=(
                        self._id_from_url(
                            url
                        )
                    ),
                    url=url,
                    title=title,
                    raw_text=text,
                )

                if listing:
                    listings.append(
                        listing
                    )

                else:
                    rejected += 1

                    LOGGER.info(
                        (
                            "%s Source=%s "
                            "Reject=%s "
                            "url=%s"
                        ),
                        SYSTEM,
                        self.source,
                        reason,
                        url,
                    )

            return CollectorResult(
                source=self.source,
                fetched=len(
                    cards
                ),
                accepted=len(
                    listings
                ),
                rejected=rejected,
                duplicates=0,
                blocked=False,
                error=None,
                listings=tuple(
                    listings
                ),
            )

        except (
            SourceBlockedError
        ) as exc:

            return CollectorResult(
                source=self.source,
                fetched=0,
                accepted=0,
                rejected=0,
                duplicates=0,
                blocked=True,
                error=str(
                    exc
                ),
                listings=(),
            )

        except Exception as exc:

            return CollectorResult(
                source=self.source,
                fetched=0,
                accepted=0,
                rejected=0,
                duplicates=0,
                blocked=False,
                error=str(
                    exc
                ),
                listings=(),
            )


class DivarCollector(
    GenericListingCollector
):
    source = "divar"

    listing_url = (
        "https://divar.ir/"
        "s/tehran/car"
    )

    allowed_hosts = (
        "divar.ir",
    )

    href_patterns = (
        re.compile(
            (
                r"^/v/"
                r"[^/]+/"
                r"[^/]+/?$"
            ),
            re.I,
        ),
    )

    card_mode = "anchor"


class BamaCollector(
    GenericListingCollector
):
    source = "bama"

    listing_url = (
        "https://bama.ir/car"
    )

    allowed_hosts = (
        "bama.ir",
    )

    href_patterns = (
        re.compile(
            (
                r"^/car/"
                r"detail-"
                r"[^/?#]+/?$"
            ),
            re.I,
        ),
    )

    card_mode = "anchor"


class Khodro45Collector(
    GenericListingCollector
):
    source = "khodro45"

    listing_url = (
        "https://khodro45.com/"
        "used-car/"
    )

    allowed_hosts = (
        "khodro45.com",
    )

    href_patterns = (
        re.compile(
            (
                r"^/used-car/"
                r"[^/?#]+/"
                r"[0-9A-Za-z_-]+/?$"
            ),
            re.I,
        ),
        re.compile(
            (
                r"^/cars?/"
                r"[^/?#]+/"
                r"[0-9A-Za-z_-]+/?$"
            ),
            re.I,
        ),
    )

    card_mode = "anchor"


class FormulaCollector(
    GenericListingCollector
):
    source = "formula"

    listing_url = (
        "https://formula.ir/car/"
    )

    allowed_hosts = (
        "formula.ir",
    )

    href_patterns = (
        re.compile(
            (
                r"^/car/"
                r"detail-"
                r"[^/?#]+/?$"
            ),
            re.I,
        ),
    )

    card_mode = (
        "price_ancestor"
    )


class SheypoorCollector(
    GenericListingCollector
):
    source = "sheypoor"

    listing_url = (
        "https://www.sheypoor.com/"
        "s/iran/car"
    )

    allowed_hosts = (
        "sheypoor.com",
        "www.sheypoor.com",
    )

    href_patterns = (
        re.compile(
            (
                r"^/v/"
                r".+\.html/?$"
            ),
            re.I,
        ),
    )

    card_mode = (
        "price_ancestor"
    )


class KarnamehCollector(
    GenericListingCollector
):
    source = "karnameh"

    listing_url = (
        "https://karnameh.com/"
        "buy-used-cars"
    )

    allowed_hosts = (
        "karnameh.com",
    )

    href_patterns = (
        re.compile(
            (
                r"^/used-cars/"
                r"[0-9a-f]{8}-"
                r"[0-9a-f]{4}-"
                r"[0-9a-f]{4}-"
                r"[0-9a-f]{4}-"
                r"[0-9a-f]{12}/?$"
            ),
            re.I,
        ),
    )

    card_mode = "anchor"


class HamrahMechanicCollector(
    GenericListingCollector
):
    source = (
        "hamrah_mechanic"
    )

    listing_url = (
        "https://www.hamrah-mechanic.com/"
        "cars-for-sale/"
    )

    allowed_hosts = (
        "hamrah-mechanic.com",
        "www.hamrah-mechanic.com",
    )

    href_patterns = (
        re.compile(
            (
                r"^/cars-for-sale/"
                r"[^/]+/"
                r"[^/]+/"
                r"[0-9]+/?$"
            ),
            re.I,
        ),
    )

    card_mode = "anchor"


def build_collectors(
    context: CollectorContext,
    enabled_sources: (
        Iterable[str]
        | None
    ) = None,
) -> dict[
    str,
    BaseCollector,
]:

    all_collectors: dict[
        str,
        BaseCollector,
    ] = {
        "telegram": (
            TelegramCollector(
                context
            )
        ),

        "divar": (
            DivarCollector(
                context
            )
        ),

        "bama": (
            BamaCollector(
                context
            )
        ),

        "khodro45": (
            Khodro45Collector(
                context
            )
        ),

        "formula": (
            FormulaCollector(
                context
            )
        ),

        "sheypoor": (
            SheypoorCollector(
                context
            )
        ),

        "karnameh": (
            KarnamehCollector(
                context
            )
        ),

        "hamrah_mechanic": (
            HamrahMechanicCollector(
                context
            )
        ),
    }

    if (
        enabled_sources
        is None
    ):
        return all_collectors

    enabled = {
        source
        .strip()
        .lower()
        for source
        in enabled_sources
        if source.strip()
    }

    for name in tuple(
        all_collectors
    ):
        if name not in enabled:

            all_collectors[
                name
            ].close()

            del all_collectors[
                name
            ]

    return all_collectors


def run_self_test() -> None:

    sample = (
        "پژو 206 تیپ 2 "
        "مدل 1383 "
        "کارکرد 169000 "
        "قیمت 595,500,000 تومان"
    )

    (
        listing,
        reason,
    ) = normalize_listing(
        source="test",
        source_ad_id="1",
        url=(
            "https://example.com/"
            "a/1"
        ),
        title=sample,
        raw_text=sample,
    )

    assert (
        reason
        == "accepted"
    )

    assert (
        listing
        is not None
    )

    assert (
        listing.brand
        == "Peugeot"
    )

    assert (
        listing.model
        == "206"
    )

    assert (
        listing.trim
        == "تیپ 2"
    )

    assert (
        listing.model_year
        == 1383
    )

    assert (
        listing.condition
        == "used"
    )

    assert (
        listing.mileage
        == 169000
    )

    assert (
        listing.price
        == 595_500_000
    )

    (
        blocked,
        why,
    ) = normalize_listing(
        source="test",
        source_ad_id="2",
        url=(
            "https://example.com/"
            "a/2"
        ),
        title=(
            "پژو 206 تیپ 2 "
            "مدل 1383 اقساطی"
        ),
        raw_text=(
            "قیمت 500 میلیون "
            "کارکرد 100000"
        ),
    )

    assert (
        blocked
        is None
    )

    assert (
        why.startswith(
            "blocked_phrase:"
        )
    )

    test_context = (
        CollectorContext(
            runtime_dir=Path(
                "."
            ),
            diagnostics_dir=Path(
                "."
            ),
            dry_network_delay=True,
        )
    )

    bama = BamaCollector(
        test_context
    )

    assert (
        bama
        ._is_candidate_href(
            (
                "/car/"
                "detail-pdh9jy1c-"
                "mvm-315hatchback-"
                "basic-1393"
            )
        )
    )

    bama.close()

    karnameh = (
        KarnamehCollector(
            test_context
        )
    )

    assert (
        karnameh
        ._is_candidate_href(
            (
                "/used-cars/"
                "05c1a387-538f-"
                "4c22-b09d-"
                "d5f143dcf3bf"
            )
        )
    )

    karnameh.close()

    sheypoor = (
        SheypoorCollector(
            test_context
        )
    )

    assert (
        sheypoor
        ._is_candidate_href(
            (
                "/v/"
                "example-"
                "466599945.html"
            )
        )
    )

    assert not (
        sheypoor
        ._is_candidate_href(
            (
                "/s/iran/car/"
                "peugeot"
            )
        )
    )

    sheypoor.close()


if __name__ == "__main__":

    run_self_test()

    print(
        (
            "accurate_average_collectors "
            "self-test: OK"
        )
    )
