#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import os
import random
import sqlite3
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

import source_normalizers as source_normalizers_v2


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
    # These phrases make the listing unsuitable regardless of payment options.
    # Finance/transaction words (e.g. اقساط/معاوضه) are intentionally NOT here:
    # a trustworthy full-vehicle price may coexist with optional financing.
    "توافقی",
    "تماس بگیرید",
    "قیمت در تماس",
    "قیمت تماس",
    "اوراقی",
    "سوخته",
    "فروش قطعات",
    "قطعه فروشی",
    "قطعه‌ای",
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
    body_condition: str

    mileage: int | None
    price: int

    collected_at: str
    raw_text: str = ""

    # Stage 3 reference evidence. None means the received evidence did not
    # establish the value; it is never coerced to zero. These fields are
    # populated after vehicle_evidence_normalizer runs.
    paint_count: int | None = None
    replacement_count: int | None = None
    chassis_hit: int | None = None
    defect_score: int | None = None
    evidence_state: str = "unknown"

    # Telegram image evidence. The URL is used only for the current-run deal
    # message and is not part of comparison identity or market history.
    image_url: str = ""
    image_evidence_used: bool = False

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
        # Stage 3 base group: exact vehicle identity only. Mileage and the
        # unified defect score are soft ranking signals inside this group, not
        # hard gates. Source, condition and coarse body labels are deliberately
        # excluded.
        return "|".join(
            (
                normalize_for_match(self.brand),
                normalize_for_match(self.model),
                normalize_for_match(self.trim),
                str(self.model_year),
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
                self.body_condition,
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

    # Some public web cards (notably Sheypoor) render the full asking price as
    # a grouped integer without a nearby currency word. This local parser is
    # used only to choose the smallest card ancestor containing price evidence;
    # authoritative full-price validation still happens later in
    # source_normalizers_v2.parse_vehicle_price().
    grouped_values = []
    for match in re.finditer(
        r"(?<!\d)(\d{1,3}(?:\s+\d{3}){2,3})(?!\d)",
        normalized,
    ):
        before = normalized[max(0, match.start() - 40):match.start()]
        if re.search(
            r"(?:کارکرد|پیمایش|شماره|تماس|مدل|سال|پیش\s*پرداخت|پیشپرداخت|"
            r"ودیعه|قسط|اقساط|ماهانه|مبلغ\s*اولیه|پرداخت\s*اولیه)\s*[:=\-]?\s*$",
            before,
        ):
            continue
        value = _integer(match.group(1))
        if value is not None and 20_000_000 <= value <= 100_000_000_000:
            grouped_values.append(value)

    if len(set(grouped_values)) == 1:
        return grouped_values[0]

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


def parse_body_condition(
    text: str,
    condition: str,
) -> str:
    """Normalize body/paint condition so unlike cars are never averaged together."""
    if condition == "zero":
        return "factory"

    normalized = normalize_for_match(text)

    groups = (
        ("accident", (
            "تصادفی", "تصادف", "چپی", "واژگون", "شاسی ضربه",
            "ضربه شاسی", "اتاق تعویض",
        )),
        ("replaced", (
            "تعویضی", "قطعه تعویض", "درب تعویض", "گلگیر تعویض",
            "کاپوت تعویض", "صندوق تعویض",
        )),
        ("full_paint", (
            "تمام رنگ", "تمام رنگه", "دور رنگ", "کامل رنگ",
        )),
        ("minor_paint", (
            "یک لکه رنگ", "یه لکه رنگ", "یک تکه رنگ", "تک لکه رنگ",
            "لکه رنگ",
        )),
        ("painted", (
            "رنگ شدگی", "رنگشدگی", "رنگ دارد", "دو لکه رنگ",
            "چند لکه رنگ", "چند تکه رنگ",
        )),
        ("clean", (
            "بدون رنگ", "بی رنگ", "بیرنگ", "بدنه بی رنگ",
            "بدنه بدون رنگ", "رنگ شدگی ندارد", "رنگشدگی ندارد",
        )),
    )

    for label, phrases in groups:
        if any(normalize_for_match(p) in normalized for p in phrases):
            return label

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
    """Remove only known site chrome; never erase seller finance evidence broadly."""
    cleaned = str(text or "")

    # Source-specific UI/service badges that are known not to describe the car's
    # asking price. Keep this allowlist narrow: actual seller text such as a
    # down-payment amount must survive and be rejected by the structured parser.
    source_noise = {
        "hamrah_mechanic": (
            "قابل معاوضه",
            "امکان خرید اقساطی",
            "اقساط 1 تا 60 ماه",
            "اقساط ۱ تا ۶۰ ماه",
            "کارشناسی شده",
            "گارانتی 7 روزه",
            "گارانتی ۷ روزه",
            "درحال کارشناسی",
        ),
    }
    for phrase in source_noise.get(source, ()):
        cleaned = cleaned.replace(phrase, " ")

    return re.sub(r"\s+", " ", cleaned).strip()



def _normalize_telegram_listing(
    *, source_ad_id: str, url: str, title: str, raw_text: str,
) -> tuple[NormalizedVehicleListing | None, str]:
    """Use the known normalizer contract directly; no guessed signatures.

    A missing / incompatible dependency is an error, not permission to silently
    fall back to a weaker price parser. Preflight exercises this exact path.
    Other sources continue through the unchanged legacy normalize_listing path.
    """
    if getattr(source_normalizers_v2, "API_VERSION", None) != 2:
        raise RuntimeError("Telegram normalization requires source_normalizers API_VERSION=2")
    clean_title = str(title or "").strip()
    clean_raw = str(raw_text or "").strip()
    text = clean_raw if clean_raw.startswith(clean_title) else f"{clean_title}\n{clean_raw}".strip()
    blocked = find_blocked_phrase(text)
    if blocked:
        return None, f"blocked_phrase:{blocked}"

    # Exact signature and exact result type: this is the integration fix.
    normalized = source_normalizers_v2.normalize_source_text(
        source="telegram", raw_text=text,
    )
    if not isinstance(normalized, source_normalizers_v2.SourceNormalization):
        raise TypeError("normalize_source_text must return SourceNormalization")
    if normalized.finance_blocked:
        return None, "blocked_phrase:finance"
    if normalized.price is None:
        return None, f"invalid_price:{normalized.price_reason}"
    if type(normalized.price) is not int or not (
        source_normalizers_v2.MIN_PRICE <= normalized.price <= source_normalizers_v2.MAX_PRICE
    ):
        return None, "invalid_price:out_of_range"
    if normalized.model_year is None:
        return None, f"invalid_year:{normalized.year_reason}"
    year = normalized.model_year
    if type(year) is not int or not (1300 <= year <= 1499 or 1950 <= year <= 2035):
        return None, "invalid_year:out_of_range"

    clean_url = canonical_url(url)
    brand, model, trim = extract_vehicle_identity(normalized.normalized_text)
    if not brand or not model:
        return None, "unknown_vehicle"
    if (brand, model) in TRIM_REQUIRED_MODELS and not trim:
        return None, "missing_trim"

    mileage = normalized.mileage
    if mileage is not None and (type(mileage) is not int or not 0 <= mileage <= 2_000_000):
        return None, "unknown_condition:invalid_mileage"
    condition = "unknown" if mileage is None else "zero" if mileage == 0 else "used"

    body = normalized.body_condition
    # Stage 3 no longer rejects a listing merely because mileage/body evidence
    # is absent. Detailed evidence is interpreted later and unknown remains
    # unknown. Explicit contradictory zero-car damage is still held back.
    body_map = {
        "unknown": "unknown",
        "clean": "clean", "minor_paint": "minor_paint",
        "multi_paint": "painted", "full_paint": "full_paint",
        "replaced": "replaced", "accident": "accident",
        "structural": "accident",
    }
    if body not in body_map:
        body_condition = "unknown"
    else:
        body_condition = body_map[body]
    if condition == "zero" and body_condition not in {"clean", "unknown"}:
        return None, "body_condition_needs_review:zero_with_damage"
    if condition == "zero" and body_condition == "clean":
        body_condition = "factory"
    clean_id = str(source_ad_id or "").strip() or hashlib.sha256(clean_url.encode('utf-8')).hexdigest()[:24]
    listing = NormalizedVehicleListing(
        source="telegram", source_ad_id=clean_id, url=clean_url,
        title=re.sub(r"\s+", " ", clean_title)[:300],
        brand=brand, model=model, trim=trim, model_year=year,
        condition=condition, body_condition=body_condition,
        mileage=mileage, price=normalized.price,
        collected_at=datetime.now(timezone.utc).isoformat(), raw_text=text[:4000],
    )
    LOGGER.info(
        "%s Source=telegram Normalizer=structured-v2 post=%s "
        "price=%s price_reason=%s year=%s year_reason=%s mileage=%s body_condition=%s",
        SYSTEM, clean_id, normalized.price, normalized.price_reason,
        year, normalized.year_reason, mileage, body_condition,
    )
    return listing, "accepted"

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

    if source == "telegram":
        return _normalize_telegram_listing(
            source_ad_id=source_ad_id, url=url, title=title, raw_text=raw_text,
        )

    clean_url = canonical_url(
        url
    )

    # Divar uses percent-encoded Persian slugs heavily. Decode the URL for
    # parsing only; keep clean_url unchanged for storage/deduplication.
    decoded_url = unquote(clean_url)

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

    # Use the same strict full-price parser for every source. Financing words by
    # themselves do not reject a listing; finance-component amounts are ignored
    # and can never become the vehicle's market price.
    price, price_reason = source_normalizers_v2.parse_vehicle_price(
        text, allow_shorthand=False,
    )

    if price is None:
        return (
            None,
            "invalid_price:" + str(price_reason),
        )

    year = (
        parse_year(
            text
        )
        or parse_year_from_url(
            decoded_url
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
            + decoded_url
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

    body_condition = parse_body_condition(
        text,
        condition,
    )

    # A Divar car with an explicit older model year but no mileage label is
    # still a used-car listing. This avoids rejecting ordinary cards whose
    # compact markup omits the mileage field. Recent/current model years stay
    # unknown unless the card explicitly identifies them as zero/used.
    if (
        source
        == "divar"
        and condition
        == "unknown"
    ):
        now = datetime.now(timezone.utc)
        approximate_current_solar = now.year - 621

        if 1300 <= year <= 1499 and year <= approximate_current_solar - 2:
            condition = "used"
        elif 2000 <= year <= 2100 and year <= now.year - 2:
            condition = "used"

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

    # Stage 3 permits missing mileage/usage evidence. Unknown remains explicit
    # and is handled as a lower-priority comparison signal, never guessed.
    body_condition = parse_body_condition(
        text,
        condition,
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
            body_condition=body_condition,
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

    def _capture_observation(self, source_ad_id, url, title, text, listing, reason):
        """Retain received listing text, including rejects, inside encrypted-state scope.

        No request, retry, change to CollectorResult or permission to send occurs
        here. This database is deliberately OUTSIDE the diagnostic upload paths.
        It holds received text, not independently verified vehicle condition.
        """
        connection = None
        try:
            directory = self.context.runtime_dir
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "listing_observations.sqlite3"
            connection = sqlite3.connect(path, timeout=10)
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS listing_observations (
                    source_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_ad_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    received_text TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    text_chars INTEGER NOT NULL,
                    text_truncated INTEGER NOT NULL,
                    received_scope TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    claimed_body_in_text TEXT NOT NULL,
                    parsed_fields_json TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_observations_source_time "
                               "ON listing_observations(source, last_seen)")
            received = str(text or "")
            ad_id = str(source_ad_id or "").strip()
            if not ad_id:
                ad_id = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:24]
            key = f"{self.source}|{ad_id}"
            timestamp = datetime.now(timezone.utc).isoformat()
            # Do not treat truncated input as evidence of absence of damage.
            retained = received[:8000]
            truncated = len(received) > len(retained)
            claimed_body = ("unknown" if truncated else
                            source_normalizers_v2.classify_body_condition(received))
            parsed = {} if listing is None else {
                "brand": listing.brand, "model": listing.model, "trim": listing.trim,
                "model_year": listing.model_year, "condition": listing.condition,
                "body_condition": listing.body_condition, "mileage": listing.mileage,
                "price": listing.price,
            }
            connection.execute("""
                INSERT INTO listing_observations (
                    source_key, source, source_ad_id, url, title, received_text,
                    text_sha256, text_chars, text_truncated, received_scope,
                    outcome, reason, claimed_body_in_text, parsed_fields_json,
                    first_seen, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_key) DO UPDATE SET
                    url=excluded.url, title=excluded.title,
                    received_text=excluded.received_text,
                    text_sha256=excluded.text_sha256, text_chars=excluded.text_chars,
                    text_truncated=excluded.text_truncated,
                    received_scope=excluded.received_scope,
                    outcome=excluded.outcome, reason=excluded.reason,
                    claimed_body_in_text=excluded.claimed_body_in_text,
                    parsed_fields_json=excluded.parsed_fields_json,
                    last_seen=excluded.last_seen
            """, (key, self.source, ad_id, canonical_url(str(url)), str(title or "")[:300],
                  retained, hashlib.sha256(received.encode("utf-8")).hexdigest(),
                  len(received), int(truncated),
                  "telegram_message_text" if self.source == "telegram" else "search_card_text",
                  "accepted" if listing is not None else "rejected", str(reason)[:240],
                  claimed_body, json.dumps(parsed, ensure_ascii=False, sort_keys=True),
                  timestamp, timestamp))
            # Bounded observation cache, NOT the market history or sent-message table.
            connection.execute("DELETE FROM listing_observations WHERE "
                               "julianday(last_seen) < julianday(?) - 14", (timestamp,))
            connection.execute("DELETE FROM listing_observations WHERE source=? AND "
                               "source_key IN (SELECT source_key FROM listing_observations "
                               "WHERE source=? ORDER BY last_seen DESC, source_key "
                               "LIMIT -1 OFFSET 500)", (self.source, self.source))
            connection.execute("DELETE FROM listing_observations WHERE source_key IN "
                               "(SELECT source_key FROM listing_observations ORDER BY "
                               "last_seen DESC, source_key LIMIT -1 OFFSET 2000)")
            connection.commit()
        except Exception as exc:
            # A capture failure must not modify acceptance/sending decisions.
            LOGGER.warning("%s ObservationCaptureFailed source=%s error_type=%s",
                           SYSTEM, self.source, type(exc).__name__)
        finally:
            if connection is not None:
                connection.close()

    def _log_observation_summary(self):
        """Only aggregate counts in public logs; never print post descriptions."""
        connection = None
        try:
            path = self.context.runtime_dir / "listing_observations.sqlite3"
            if not path.is_file():
                return
            connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("""
                SELECT COUNT(*), SUM(outcome='accepted'), SUM(outcome='rejected'),
                    SUM(claimed_body_in_text='unknown'), SUM(text_truncated)
                FROM listing_observations WHERE source=?
            """, (self.source,)).fetchone()
            LOGGER.info("%s ObservationSummary source=%s stored=%s accepted=%s rejected=%s "
                        "body_not_established_in_received_text=%s truncated=%s "
                        "scope=retained_observations capture_version=1.0.0",
                        SYSTEM, self.source, *(int(x or 0) for x in row))
        except Exception as exc:
            LOGGER.warning("%s ObservationSummaryFailed source=%s error_type=%s",
                           SYSTEM, self.source, type(exc).__name__)
        finally:
            if connection is not None:
                connection.close()

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

    IMAGE_MAX_BYTES = 6_000_000
    OCR_MAX_PER_RUN = 24
    OCR_CACHE_NAME = "telegram_image_evidence.sqlite3"

    @staticmethod
    def _photo_url(block: str) -> str:
        """Return the primary Telegram preview image URL, if present."""
        soup = BeautifulSoup(block, "html.parser")
        for node in soup.select("a.tgme_widget_message_photo_wrap, a.tgme_widget_message_video_player"):
            style = html_lib.unescape(str(node.get("style", "")))
            match = re.search(r"background-image\s*:\s*url\((?:'|\")?(.*?)(?:'|\")?\)", style, re.I)
            if match:
                value = match.group(1).strip()
                if value.startswith("https://"):
                    return value
        image = soup.select_one("img[src]")
        if image is not None:
            value = html_lib.unescape(str(image.get("src", "")).strip())
            if value.startswith("https://"):
                return value
        return ""

    def _ocr_cache(self) -> sqlite3.Connection:
        path = self.context.runtime_dir / self.OCR_CACHE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_image_evidence (
                source_key TEXT PRIMARY KEY,
                image_url TEXT NOT NULL,
                image_sha256 TEXT NOT NULL,
                ocr_text TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        return connection

    def _ocr_image(self, source_key: str, image_url: str) -> str:
        """Best-effort Persian/English OCR over photo or video preview evidence.

        Two layout modes are tried because Telegram sources mix poster-style text,
        sparse overlays and photographed handwritten sheets. The highest-scoring
        normalized result is retained. OCR is evidence only: downstream parsers
        must still validate identity, year, mileage and full vehicle price.
        """
        if not image_url or shutil.which("tesseract") is None:
            LOGGER.info("%s TelegramImageEvidence post=%s status=ocr_unavailable", SYSTEM, source_key)
            return ""
        cache = self._ocr_cache()
        try:
            row = cache.execute(
                "SELECT image_url,ocr_text FROM telegram_image_evidence WHERE source_key=?",
                (source_key,),
            ).fetchone()
            if row and row[0] == image_url:
                return str(row[1] or "")
        finally:
            cache.close()

        try:
            response = self.session.get(image_url, timeout=(8, 20), stream=True, allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "image/" not in content_type:
                return ""
            data = bytearray()
            for chunk in response.iter_content(16_384):
                data.extend(chunk)
                if len(data) > self.IMAGE_MAX_BYTES:
                    return ""
        except requests.RequestException as exc:
            LOGGER.info("%s TelegramImageEvidence post=%s status=fetch_failed error_type=%s",
                        SYSTEM, source_key, type(exc).__name__)
            return ""

        digest = hashlib.sha256(data).hexdigest()
        suffix = ".jpg" if "jpeg" in content_type or "jpg" in content_type else ".png"
        temporary = None
        candidates: list[str] = []
        try:
            handle, name = tempfile.mkstemp(prefix="telegram-ocr-", suffix=suffix)
            temporary = Path(name)
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            for psm in (6, 11):
                result = subprocess.run(
                    ["tesseract", str(temporary), "stdout", "-l", "fas+eng", "--psm", str(psm)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30, check=False,
                )
                if result.returncode == 0:
                    value = source_normalizers_v2.normalize_text(result.stdout)[:8000]
                    if value and value not in candidates:
                        candidates.append(value)
        except (OSError, subprocess.SubprocessError):
            candidates = []
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        def score(value: str) -> tuple[int, int]:
            points = 0
            try:
                parsed = source_normalizers_v2.normalize_source_text(source="telegram", raw_text=value)
                points += 6 if parsed.price is not None else 0
                points += 4 if parsed.model_year is not None else 0
                points += 3 if parsed.mileage is not None else 0
                points += 1 if parsed.body_condition != "unknown" else 0
                brand, model, trim = extract_vehicle_identity(parsed.normalized_text)
                points += 5 if brand and model else 0
                points += 2 if trim else 0
            except Exception:
                pass
            visible = len(re.findall(r"[0-9A-Za-zآ-ی]", value))
            return points, visible

        ocr_text = max(candidates, key=score, default="")
        cache = self._ocr_cache()
        try:
            cache.execute(
                "INSERT INTO telegram_image_evidence VALUES(?,?,?,?,?) "
                "ON CONFLICT(source_key) DO UPDATE SET image_url=excluded.image_url,"
                "image_sha256=excluded.image_sha256,ocr_text=excluded.ocr_text,updated_at=excluded.updated_at",
                (source_key, image_url, digest, ocr_text, datetime.now(timezone.utc).isoformat()),
            )
            cache.commit()
        finally:
            cache.close()
        LOGGER.info("%s TelegramImageEvidence post=%s status=%s chars=%s mode=multi_pass_photo_or_video_preview",
                    SYSTEM, source_key, "captured" if ocr_text else "no_text", len(ocr_text))
        return ocr_text

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
        ocr_attempts = 0

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

                    text_match = self.TEXT_RE.search(block)
                    image_url = self._photo_url(block)
                    text = ""
                    if text_match:
                        raw = re.sub(r"<br\s*/?>", "\n", text_match.group(1), flags=re.I)
                        text = BeautifulSoup(
                            html_lib.unescape(raw), "html.parser"
                        ).get_text("\n", strip=True)

                    post_id = marker.group(2)

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

                    post_url = f"https://t.me/{channel}/{post_id}"
                    title = text.splitlines()[0] if text else ""
                    text_listing, text_reason = normalize_listing(
                        source=self.source, source_ad_id=key, url=post_url,
                        title=title, raw_text=text,
                    ) if text else (None, "missing_text")

                    evidence_text = text
                    listing, reason = text_listing, text_reason
                    ocr_text = ""
                    # Image/video-preview evidence is a complement, not merely a
                    # rescue path. This lets channels that put price/year/mileage
                    # only on posters or video thumbnails contribute to the bank.
                    if image_url and ocr_attempts < self.OCR_MAX_PER_RUN:
                        ocr_attempts += 1
                        ocr_text = self._ocr_image(key, image_url)

                    if ocr_text:
                        combined = "\n".join(x for x in (text, ocr_text) if x).strip()
                        combined_title = (text.splitlines()[0] if text else ocr_text.splitlines()[0])[:300]
                        combined_listing, combined_reason = normalize_listing(
                            source=self.source, source_ad_id=key, url=post_url,
                            title=combined_title, raw_text=combined,
                        )
                        if combined_listing is not None:
                            listing, reason = combined_listing, combined_reason
                            evidence_text, title = combined, combined_title
                            listing = replace(
                                listing, image_url=image_url, image_evidence_used=True,
                                raw_text=combined[:4000],
                            )
                        elif text_listing is None:
                            # Image was needed to recover the listing but the joint
                            # evidence is still insufficient/ambiguous. Keep it out.
                            listing, reason = None, combined_reason
                            evidence_text, title = combined, combined_title
                        elif (
                            "ambiguous_multiple_prices" in combined_reason
                            or "ambiguous_year" in combined_reason
                            or "conflict" in combined_reason
                        ):
                            # A validated text listing and image evidence disagree.
                            # Do not silently choose whichever makes a deal easier.
                            listing = None
                            reason = "image_text_conflict:" + combined_reason
                            evidence_text, title = combined, combined_title
                        else:
                            # OCR was noisy/unusable but did not establish a trusted
                            # contradiction. Preserve the validated text observation.
                            listing, reason = text_listing, text_reason
                            if listing is not None:
                                listing = replace(listing, image_url=image_url)
                    elif listing is not None and image_url:
                        listing = replace(listing, image_url=image_url)

                    self._capture_observation(
                        key, post_url, title, evidence_text, listing, reason,
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

        self._log_observation_summary()

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
        current_url = canonical_url(
            urljoin(
                self.listing_url,
                str(anchor.get("href", "")),
            )
        )

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

            candidate = node.parent

            # A listing card must never absorb neighbouring cards.  Count UNIQUE
            # listing URLs inside the prospective ancestor; image/title duplicates
            # for the same ad are fine, but a second ad means we crossed the card
            # boundary.  This is source-independent and avoids selecting a price
            # from another vehicle merely because both cards share a grid wrapper.
            listing_urls: set[str] = set()
            for nested in candidate.find_all("a", href=True):
                href = str(nested.get("href", "")).strip()
                if not self._is_candidate_href(href):
                    continue
                listing_urls.add(
                    canonical_url(
                        urljoin(
                            self.listing_url,
                            href,
                        )
                    )
                )

            if (
                len(listing_urls) > 1
                or (
                    current_url
                    and listing_urls
                    and current_url not in listing_urls
                )
            ):
                break

            node = candidate

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

                self._capture_observation(
                    self._id_from_url(url), url, title, text, listing, reason,
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

            self._log_observation_summary()

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



def _run_telegram_integration_self_test() -> None:
    """Offline regression tests for the complete normalizer -> listing path."""
    from dataclasses import fields
    from unittest.mock import patch

    if getattr(source_normalizers_v2, "API_VERSION", None) != 2:
        raise AssertionError("source_normalizers API version mismatch")

    common = "پژو 206 تیپ 5\nمدل 1397\nکارکرد 120 هزار کیلومتر\nبدون رنگ\n"
    def parse(text: str):
        return normalize_listing(
            source="telegram", source_ad_id="test:1", url="https://t.me/test/1",
            title=text.splitlines()[0], raw_text=text,
        )
    def require(condition, label):
        if not condition:
            raise AssertionError(label)

    # Network access is forbidden for the integration tests, even accidentally.
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Network access attempted in offline integration test")
    with patch.object(requests.sessions.Session, "request", forbidden_network):
        original = source_normalizers_v2.normalize_source_text
        with patch.object(source_normalizers_v2, "normalize_source_text", wraps=original) as spy:
            listing, reason = parse(common + "قیمت 1/250")
            require(reason == "accepted" and listing is not None, "shorthand must reach the listing")
            require(spy.call_count == 1, "normalizer must be called exactly once")
            require(spy.call_args.kwargs["source"] == "telegram", "exact normalizer contract")
            require(listing.price == 1_250_000_000, "structured price must be consumed")
            require(listing.model_year == 1397 and listing.mileage == 120000, "year and mileage")
            require(listing.body_condition == "clean", "body condition")
            require("body_condition" in {f.name for f in fields(listing)}, "Service schema compatibility")
            require(listing.comparison_key.endswith("|1397"), "stage3 identity-only comparison key")

        # The old parser must not accidentally handle a Telegram price or year.
        with patch(__name__ + ".parse_price", side_effect=AssertionError("legacy price parser called")), \
             patch(__name__ + ".parse_year", side_effect=AssertionError("legacy year parser called")):
            item, why = parse(common + "قیمت 1250")
            require(why == "accepted" and item.price == 1_250_000_000, "no legacy fallback")

        for price in ("قیمت: 1250", "قیمت 1,250,000,000 تومان", "قیمت ۱ میلیارد و ۲۵۰ میلیون تومان"):
            item, why = parse(common + price)
            require(why == "accepted" and item.price == 1_250_000_000, price)
        item, why = parse(common.replace("1397", "97") + "قیمت 1250")
        require(why == "accepted" and item.model_year == 1397, "short solar year")
        item, why = parse(common.replace("1397", "2017") + "قیمت 1250")
        require(why == "accepted" and item.model_year == 2017, "Gregorian year must not be converted")
        item, why = parse(common.replace("بدون رنگ", "بدون رنگ شاسی سالم تعویض روغن") + "قیمت 1250")
        require(why == "accepted" and item.body_condition == "clean", "negation/mechanical terms")
        for body, expected in (("یک لکه رنگ", "minor_paint"), ("دو لکه رنگ", "painted"), ("دور رنگ", "full_paint")):
            item, why = parse(common.replace("بدون رنگ", body) + "قیمت 1250")
            require(why == "accepted" and item.body_condition == expected, body)
        item, why = parse(common.replace("بدون رنگ", "وضعیت بدنه نامشخص") + "قیمت 1250")
        require(why == "accepted" and item is not None, "unknown body must reach Stage 3")
        item, why = parse(common.replace("کارکرد 120 هزار کیلومتر\n", "") + "قیمت 1250")
        require(why == "accepted" and item is not None and item.mileage is None, "unknown mileage must reach Stage 3")

        # Optional financing/transaction wording must not hide a trustworthy
        # full asking price. A finance component without a full price is blocked.
        for suffix in ("قیمت 1250\nحواله", "قیمت 1250\nاقساط", "قیمت 1250\nامکان خرید اقساطی"):
            item, why = parse(common + suffix)
            require(why == "accepted" and item is not None and item.price == 1_250_000_000, suffix)

        reject_cases = (
            (common + "پیش پرداخت 300 میلیون", "blocked_phrase:finance"),
            (common + "شماره تماس 09123456789", "invalid_price:"),
            (common + "قیمت 1250\nقیمت 1400", "invalid_price:ambiguous_multiple_prices"),
            (common.replace("مدل 1397\n", "") + "قیمت 1400", "invalid_year:"),
            # Missing mileage/body evidence is admitted in Stage 3 and ranked
            # below better-evidenced references instead of being discarded.
            (common.replace(" تیپ 5", "") + "قیمت 1250", "missing_trim"),
        )
        for text, expected_reason in reject_cases:
            item, reason = parse(text)
            require(item is None and reason.startswith(expected_reason), f"{expected_reason}: {reason}")

        # A TypeError in the module is no longer silently swallowed.
        with patch.object(source_normalizers_v2, "normalize_source_text", side_effect=TypeError("contract-test")):
            try:
                parse(common + "قیمت 1250")
            except TypeError as exc:
                require(str(exc) == "contract-test", "unexpected error")
            else:
                raise AssertionError("TypeError must not fall back to the legacy parser")

        # Verify complete Telegram HTML -> text listing and image-evidence flows without HTTP.
        import tempfile
        import html as _html
        with tempfile.TemporaryDirectory() as directory:
            text = common + "قیمت 1/250"
            markup = '<div data-post="test/1"><div class="tgme_widget_message_text">' + _html.escape(text).replace("\n", "<br>") + '</div></div>'
            context = CollectorContext(runtime_dir=Path(directory), diagnostics_dir=Path(directory), dry_network_delay=True)
            collector = TelegramCollector(context, channels=("test",))
            try:
                with patch.object(collector, "_get", return_value=markup):
                    result = collector.collect()
                require(result.fetched == 1 and result.accepted == 1 and result.rejected == 0, "Telegram HTML integration")
                require(result.error is None and result.listings[0].price == 1_250_000_000, "collector result")

                photo = "https://cdn.example.test/car.jpg"
                image_markup = (
                    '<div data-post="test/2">'
                    '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'' + photo + '\')"></a>'
                    '</div>'
                )
                ocr = common + "قیمت 1250"
                with patch.object(collector, "_get", return_value=image_markup), \
                     patch.object(collector, "_ocr_image", return_value=ocr) as ocr_spy:
                    image_result = collector.collect()
                require(image_result.accepted == 1 and image_result.rejected == 0, "Telegram image evidence integration")
                image_listing = image_result.listings[0]
                require(image_listing.price == 1_250_000_000, "OCR price must reach normalizer")
                require(image_listing.image_url == photo and image_listing.image_evidence_used, "Image provenance must survive")
                require(ocr_spy.call_count == 1, "Image OCR must run for image evidence")

                # A complete caption still receives complementary image evidence.
                complete_markup = (
                    '<div data-post="test/3">'
                    '<div class="tgme_widget_message_text">' + _html.escape(text).replace("\n", "<br>") + '</div>'
                    '<a class="tgme_widget_message_video_player" style="background-image:url(\'' + photo + '\')"></a>'
                    '</div>'
                )
                with patch.object(collector, "_get", return_value=complete_markup), \
                     patch.object(collector, "_ocr_image", return_value="پژو 206 تیپ 5\nمدل 1397\nکارکرد 120 هزار\nبدون رنگ\nقیمت 1250") as full_ocr:
                    complete_result = collector.collect()
                require(complete_result.accepted == 1, "Complete caption plus video preview must stay usable")
                require(complete_result.listings[0].image_evidence_used, "Video preview OCR must complement complete caption")
                require(full_ocr.call_count == 1, "Video preview OCR must run for complete caption")
            finally:
                collector.close()
    print("telegram normalization integration self-test: OK")


def _run_observation_capture_self_test() -> None:
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        context = CollectorContext(runtime_dir=root, diagnostics_dir=root / "diagnostics", dry_network_delay=True)
        def forbid(*args, **kwargs):
            raise AssertionError("Unexpected network attempt")
        with patch.object(requests.sessions.Session, "request", forbid):
            collector = TelegramCollector(context, channels=("test",))
            try:
                collector._capture_observation("test:1", "https://t.me/test/1", "sample",
                    "بدون رنگ\nقیمت نامشخص", None, "invalid_price")
                connection = sqlite3.connect(root / "listing_observations.sqlite3")
                try:
                    row = connection.execute("SELECT received_text,outcome,claimed_body_in_text FROM listing_observations").fetchone()
                    if row != ("بدون رنگ\nقیمت نامشخص", "rejected", "clean"):
                        raise AssertionError("Rejected text must be retained without becoming a market sample")
                finally:
                    connection.close()
                collector._capture_observation("test:1", "https://t.me/test/1", "sample",
                    "وضعیت نامشخص", None, "unknown_body_condition")
                connection = sqlite3.connect(root / "listing_observations.sqlite3")
                try:
                    rows = connection.execute("SELECT received_text,claimed_body_in_text FROM listing_observations").fetchall()
                    if rows != [("وضعیت نامشخص", "unknown")]:
                        raise AssertionError("Recapture must not keep obsolete clean claims")
                finally:
                    connection.close()
            finally:
                collector.close()
    print("collector observation capture self-test: OK")

def run_self_test() -> None:

    _run_observation_capture_self_test()

    _run_telegram_integration_self_test()

    sample = (
        "پژو 206 تیپ 2 "
        "مدل 1383 "
        "کارکرد 169000 بدون رنگ "
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
        listing.body_condition
        == "clean"
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
        blocked is not None
        and why == "accepted"
        and blocked.price == 500_000_000
    )



    financing, financing_reason = normalize_listing(
        source="test",
        source_ad_id="3",
        url="https://example.com/a/3",
        title="پژو 206 تیپ 2 مدل 1383",
        raw_text="پیش پرداخت 500 میلیون کارکرد 100000",
    )
    assert financing is None
    assert financing_reason.startswith("invalid_price:")

    # A valid full asking price must survive optional financing/transaction UI
    # wording for every web source; we must not delete these listings blindly.
    for source in ("divar", "bama", "sheypoor", "karnameh", "khodro45", "formula", "hamrah_mechanic"):
        item, why = normalize_listing(
            source=source, source_ad_id="finance-safe",
            url="https://example.com/a/finance-safe",
            title="پژو 206 تیپ 2 مدل 1383",
            raw_text="کارکرد 100000 بدون رنگ قیمت 595,500,000 تومان امکان خرید اقساطی",
        )
        assert item is not None and why == "accepted" and item.price == 595_500_000, (source, why)

    # Conversely, a finance component without a trustworthy full price must never
    # enter market history as if it were the vehicle's total asking price.
    bad, why = normalize_listing(
        source="sheypoor", source_ad_id="finance-component",
        url="https://example.com/a/finance-component",
        title="پژو 206 تیپ 2 مدل 1383",
        raw_text="کارکرد 100000 بدون رنگ پیش پرداخت 300 میلیون",
    )
    assert bad is None and why.startswith("invalid_price:"), why

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

