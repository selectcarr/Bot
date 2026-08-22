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


LOGGER = logging.getLogger(
    "accurate_average.collectors"
)

SYSTEM = "[ACCURATE-SYSTEM]"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6"
    ),
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

BLOCK_STATUSES = {
    403,
    429,
}

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
    ENGLISH_DIGITS * 2,
)

BRAND_ALIASES = {
    "پژو": "Peugeot",
    "ایران خودرو": "Iran Khodro",
    "ایرانخودرو": "Iran Khodro",
    "سایپا": "Saipa",
    "رنو": "Renault",
    "کیا": "Kia",
    "هیوندای": "Hyundai",
    "تویوتا": "Toyota",
    "مرسدس بنز": "Mercedes-Benz",
    "مرسدس": "Mercedes-Benz",
    "بنز": "Mercedes-Benz",
    "بی ام و": "BMW",
    "ب ام و": "BMW",
    "bmw": "BMW",
    "مزدا": "Mazda",
    "نیسان": "Nissan",
    "میتسوبیشی": "Mitsubishi",
    "هوندا": "Honda",
    "لکسوس": "Lexus",
    "پورشه": "Porsche",
    "مازراتی": "Maserati",
    "ام وی ام": "MVM",
    "mvm": "MVM",
    "فونیکس": "Fownix",
    "چری": "Chery",
    "جک": "JAC",
    "هایما": "Haima",
    "چانگان": "Changan",
    "لاماری": "Lamari",
    "فیدلیتی": "Fidelity",
    "دیگنیتی": "Dignity",
    "فولکس واگن": "Volkswagen",
    "فولکس‌واگن": "Volkswagen",
    "اودی": "Audi",
    "آئودی": "Audi",
}

MODEL_ALIASES = {
    "پژو 206": (
        "Peugeot",
        "206",
    ),
    "206": (
        "Peugeot",
        "206",
    ),

    "پژو 207": (
        "Peugeot",
        "207",
    ),
    "207": (
        "Peugeot",
        "207",
    ),

    "پژو 405": (
        "Peugeot",
        "405",
    ),
    "405": (
        "Peugeot",
        "405",
    ),

    "پژو پارس": (
        "Peugeot",
        "Pars",
    ),
    "پرشیا": (
        "Peugeot",
        "Pars",
    ),
    "پارس": (
        "Peugeot",
        "Pars",
    ),

    "سمند": (
        "Iran Khodro",
        "Samand",
    ),
    "سورن پلاس": (
        "Iran Khodro",
        "Samand",
    ),
    "سورن": (
        "Iran Khodro",
        "Samand",
    ),

    "دنا پلاس": (
        "Iran Khodro",
        "Dena",
    ),
    "دنا": (
        "Iran Khodro",
        "Dena",
    ),

    "تارا": (
        "Iran Khodro",
        "Tara",
    ),
    "رانا": (
        "Iran Khodro",
        "Runna",
    ),
    "ریرا": (
        "Iran Khodro",
        "Reera",
    ),

    "پراید": (
        "Saipa",
        "Pride",
    ),
    "تیبا": (
        "Saipa",
        "Tiba",
    ),
    "ساینا": (
        "Saipa",
        "Saina",
    ),
    "کوییک": (
        "Saipa",
        "Quick",
    ),
    "کوئیک": (
        "Saipa",
        "Quick",
    ),
    "شاهین": (
        "Saipa",
        "Shahin",
    ),
    "اطلس": (
        "Saipa",
        "Atlas",
    ),
    "سهند": (
        "Saipa",
        "Sahand",
    ),

    "ال 90": (
        "Renault",
        "L90",
    ),
    "ال90": (
        "Renault",
        "L90",
    ),
    "ال نود": (
        "Renault",
        "L90",
    ),
    "تندر 90": (
        "Renault",
        "L90",
    ),

    "سراتو": (
        "Kia",
        "Cerato",
    ),
    "اپتیما": (
        "Kia",
        "Optima",
    ),
    "اسپورتیج": (
        "Kia",
        "Sportage",
    ),
    "سورنتو": (
        "Kia",
        "Sorento",
    ),

    "النترا": (
        "Hyundai",
        "Elantra",
    ),
    "سوناتا": (
        "Hyundai",
        "Sonata",
    ),
    "توسان": (
        "Hyundai",
        "Tucson",
    ),
    "سانتافه": (
        "Hyundai",
        "Santa Fe",
    ),

    "کمری": (
        "Toyota",
        "Camry",
    ),
    "کرولا": (
        "Toyota",
        "Corolla",
    ),
    "پرادو": (
        "Toyota",
        "Prado",
    ),
    "لندکروزر": (
        "Toyota",
        "Land Cruiser",
    ),
    "راف فور": (
        "Toyota",
        "RAV4",
    ),

    "x22": (
        "MVM",
        "X22",
    ),
    "x33": (
        "MVM",
        "X33",
    ),
    "x55": (
        "MVM",
        "X55",
    ),

    "تیگو 7": (
        "Chery",
        "Tiggo 7",
    ),
    "تیگو 8": (
        "Chery",
        "Tiggo 8",
    ),
    "آریزو 5": (
        "Chery",
        "Arrizo 5",
    ),
    "اریزو 5": (
        "Chery",
        "Arrizo 5",
    ),

    "لاماری ایما": (
        "Lamari",
        "Eama",
    ),
    "لاماری": (
        "Lamari",
        "Eama",
    ),

    "فیدلیتی": (
        "Fidelity",
        "Fidelity",
    ),
    "دیگنیتی": (
        "Dignity",
        "Dignity",
    ),

    "جک s3": (
        "JAC",
        "S3",
    ),
    "جک s5": (
        "JAC",
        "S5",
    ),
    "جک j4": (
        "JAC",
        "J4",
    ),
}

TRIM_ALIASES = {
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

    "دنده ای": "دنده‌ای",
    "دنده‌ای": "دنده‌ای",
    "اتومات": "اتوماتیک",
    "اتوماتیک": "اتوماتیک",
    "mc": "MC",

    "سورن پلاس": "سورن پلاس",
    "سورن": "سورن",
    "ef7": "EF7",
    "lx": "LX",

    "پلاس توربو اتوماتیک": (
        "پلاس توربو اتوماتیک"
    ),
    "پلاس توربو": "پلاس توربو",
    "پلاس": "پلاس",

    "e1": "E1",
    "e2": "E2",

    "r": "R",
    "s": "S",
    "rs": "RS",
    "g": "G",
    "gl": "GL",

    "1600": "1600",
    "2000": "2000",

    "premium": "Premium",
    "ie": "IE",
}

TRIM_REQUIRED_MODELS = {
    (
        "Peugeot",
        "206",
    ),
    (
        "Peugeot",
        "207",
    ),
    (
        "Peugeot",
        "405",
    ),
    (
        "Peugeot",
        "Pars",
    ),
    (
        "Iran Khodro",
        "Samand",
    ),
    (
        "Iran Khodro",
        "Dena",
    ),
    (
        "Saipa",
        "Pride",
    ),
    (
        "Saipa",
        "Quick",
    ),
    (
        "Saipa",
        "Shahin",
    ),
    (
        "Renault",
        "L90",
    ),
    (
        "Kia",
        "Cerato",
    ),
    (
        "MVM",
        "X55",
    ),
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
            "ي",
            "ی",
        )
        .replace(
            "ك",
            "ک",
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

    cleaned = (
        normalize_digits(
            raw
        )
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

    normalized = (
        normalize_for_match(
            text
        )
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

    patterns = (
        (
            (
                r"(?:قیمت|مبلغ|فروش)?"
                r"\s*"
                r"([0-9][0-9,،٬.\s]{0,18})"
                r"\s*"
                r"(?:میلیارد|ملیارد)"
            ),
            1_000_000_000,
        ),
        (
            (
                r"(?:قیمت|مبلغ|فروش)?"
                r"\s*"
                r"([0-9][0-9,،٬.\s]{0,18})"
                r"\s*"
                r"(?:میلیون|ملیون)"
            ),
            1_000_000,
        ),
        (
            (
                r"([0-9][0-9,،٬.\s]{5,20})"
                r"\s*"
                r"(?:تومان|تومن)"
            ),
            1,
        ),
    )

    for (
        pattern,
        multiplier,
    ) in patterns:

        match = re.search(
            pattern,
            normalized,
        )

        if not match:
            continue

        number = _integer(
            match.group(
                1
            )
        )

        if not number:
            continue

        value = (
            number
            * multiplier
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

    normalized = (
        normalize_for_match(
            text
        )
    )

    candidates: list[
        str
    ] = []

    for pattern in (
        (
            r"(?:مدل|سال)"
            r"\s*"
            r"([0-9]{2,4})"
        ),
        (
            r"(?<!\d)"
            r"(13\d{2}|14\d{2}|20\d{2})"
            r"(?!\d)"
        ),
    ):
        candidates.extend(
            re.findall(
                pattern,
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

    normalized = (
        normalize_for_match(
            text
        )
    )

    if re.search(
        (
            r"(?:کارکرد\s*)?"
            r"صفر"
            r"\s*"
            r"(?:کیلومتر|km)?"
        ),
        normalized,
    ):
        return 0

    match = re.search(
        (
            r"کارکرد"
            r"\s*"
            r"([0-9]"
            r"[0-9,،٬.\s]{0,15})"
            r"(?:\s*"
            r"(?:کیلومتر|کیلو|km))?"
        ),
        normalized,
    )

    if not match:
        match = re.search(
            (
                r"([0-9]"
                r"[0-9,،٬.\s]{2,15})"
                r"\s*"
                r"(?:کیلومتر|کیلو|km)"
            ),
            normalized,
        )

    if not match:
        return None

    mileage = _integer(
        match.group(
            1
        )
    )

    if mileage is None:
        return None

    if not (
        0
        <= mileage
        <= 2_000_000
    ):
        return None

    return mileage


def parse_condition(
    text: str,
    mileage: int | None,
) -> str:

    normalized = (
        normalize_for_match(
            text
        )
    )

    if (
        mileage == 0
        or any(
            phrase
            in normalized
            for phrase in (
                "صفر کیلومتر",
                "صفرکیلومتر",
                "خشک",
                "تحویل فوری صفر",
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
            "بدون رنگ",
            "بیرنگ",
            "تمیز",
            "شاسی",
        )
    ):
        return "used"

    return "unknown"


def find_blocked_phrase(
    text: str,
) -> str | None:

    normalized = (
        normalize_for_match(
            text
        )
    )

    for phrase in (
        BLOCKED_PHRASES
    ):
        if (
            normalize_for_match(
                phrase
            )
            in normalized
        ):
            return phrase

    return None


def extract_vehicle_identity(
    text: str,
) -> tuple[
    str | None,
    str | None,
    str,
]:

    normalized = (
        normalize_for_match(
            text
        )
    )

    model_match: tuple[
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
            re.search(
                (
                    r"(?<!\w)"
                    + re.escape(
                        normalized_alias
                    )
                    + r"(?!\w)"
                ),
                normalized,
            )
            and len(
                normalized_alias
            ) > best_length
        ):
            model_match = pair

            best_length = len(
                normalized_alias
            )

    if not model_match:
        return (
            None,
            None,
            "",
        )

    brand, model = (
        model_match
    )

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
            re.search(
                (
                    r"(?<!\w)"
                    + re.escape(
                        normalized_alias
                    )
                    + r"(?!\w)"
                ),
                normalized,
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
                r"پراید"
                r"\s*"
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
                r"سراتو"
                r"\s*"
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
    ) == (
        "MVM",
        "X55",
    ):

        if (
            "premium"
            in normalized
            or "پریمیوم"
            in normalized
        ):
            trim = "Premium"

        elif re.search(
            r"\bie\b",
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

    text = (
        f"{title}\n"
        f"{raw_text}"
    ).strip()

    blocked = (
        find_blocked_phrase(
            text
        )
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

    year = parse_year(
        text
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
        text
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

    clean_url = (
        canonical_url(
            url
        )
    )

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

        visible = (
            normalize_for_match(
                " ".join(
                    soup.stripped_strings
                )
            )
        )

        for marker in (
            BLOCK_MARKERS
        ):
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

            raise (
                SourceBlockedError(
                    (
                        f"{self.source} "
                        f"blocked: "
                        f"status="
                        f"{response.status_code} "
                        f"marker={marker!r}"
                    )
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

            for item in (
                items
                if isinstance(
                    items,
                    list,
                )
                else []
            ):
                if (
                    isinstance(
                        item,
                        dict,
                    )
                    and item.get(
                        "name"
                    )
                ):
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

        except (
            OSError,
            json.JSONDecodeError,
        ):
            return

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

        for channel in (
            self.channels
        ):
            try:
                url = (
                    "https://t.me/s/"
                    f"{channel}"
                )

                html = self._get(
                    url
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
                    if (
                        index + 1
                        < len(
                            markers
                        )
                    ):
                        end = (
                            markers[
                                index + 1
                            ]
                            .start()
                        )
                    else:
                        end = len(
                            html
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
                            if text
                            else ""
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

                break

            except Exception as exc:
                errors.append(
                    (
                        f"{channel}:"
                        f"{exc}"
                    )
                )

        blocked = bool(
            errors
            and "blocked"
            in " ".join(
                errors
            ).lower()
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

    def _is_candidate_href(
        self,
        href: str,
    ) -> bool:

        if not href:
            return False

        absolute = urljoin(
            self.listing_url,
            href,
        )

        host = urlsplit(
            absolute
        ).netloc.lower()

        if (
            self.allowed_hosts
            and not any(
                (
                    host == allowed
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
                absolute
            )
            for pattern
            in self.href_patterns
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

            node = anchor

            for _ in range(
                4
            ):
                if (
                    node.parent
                    is None
                ):
                    break

                parent_text = (
                    " ".join(
                        node.parent
                        .stripped_strings
                    )
                )

                if (
                    20
                    <= len(
                        parent_text
                    )
                    <= 2000
                ):
                    node = (
                        node.parent
                    )
                else:
                    break

            text = (
                " ".join(
                    node.stripped_strings
                )
                or " ".join(
                    anchor.stripped_strings
                )
            )

            title = (
                " ".join(
                    anchor.stripped_strings
                )
                or text[:200]
            )

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

        if token:
            return token

        return (
            hashlib.sha256(
                url.encode()
            )
            .hexdigest()[:24]
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
            r"/v/",
            re.I,
        ),
    )


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
            r"/car/detail/",
            re.I,
        ),
        re.compile(
            (
                r"/car/"
                r"[^/?#]+/"
                r"[0-9A-Za-z_-]+"
            ),
            re.I,
        ),
    )


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
            r"/used-car/[^?#]+",
            re.I,
        ),
        re.compile(
            r"/cars?/[^?#]+",
            re.I,
        ),
    )


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
            r"/car/[^?#]+",
            re.I,
        ),
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
            r"/v/",
            re.I,
        ),
        re.compile(
            (
                r"/s/"
                r"[^?#]+/"
                r"[0-9A-Za-z_-]+"
            ),
            re.I,
        ),
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
            r"/cars?/[^?#]+",
            re.I,
        ),
        re.compile(
            (
                r"/buy-used-cars/"
                r"[^?#]+"
            ),
            re.I,
        ),
    )


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
                r"/car[s-].*sale/"
                r"[^?#]+"
            ),
            re.I,
        ),
        re.compile(
            (
                r"/cars-for-sale/"
                r"[^?#]+"
            ),
            re.I,
        ),
    )


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
        "قیمت 595,500,000 تومان "
        "بدون رنگ"
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
            "قیمت 500 میلیون"
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


if __name__ == "__main__":
    run_self_test()

    print(
        (
            "accurate_average_collectors "
            "self-test: OK"
        )
    )
