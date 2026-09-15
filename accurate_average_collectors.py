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

# آگهی‌هایی که نباید وارد بانک قیمت نقدی شوند.
BLOCKED_PHRASES = (
    "اقساط",
    "اقساطی",
    "قسطی",
    "لیزینگ",
    "پیش پرداخت",
    "پیش‌پرداخت",
    "پیشپرداخت",
    "مبلغ اولیه",
    "پرداخت اولیه",
    "ودیعه",
    "قسط ماهانه",
    "پرداخت ماهانه",
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

    # zero / used
    condition: str

    # clean / minor_paint / painted /
    # full_paint / replaced / accident / unknown
    body_condition: str

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
                self.body_condition,
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
        r"[_/\\|,:;؛،!?؟()\[\]{}
