#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet, InvalidToken
from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageFilter,
    ImageOps,
    features,
)


ROOT = Path(__file__).resolve().parent

STAGE4_VERSION = '2.9.0'
RENDER_REVISION = 'r9'
BUY_REQUEST_RENDER_REVISION = 'r3'


TARGET_CHANNELS = (
    'select_carr',
    'select_carrr',
)


BUFFER_API = (
    os.getenv(
        'BUFFER_API_URL',
        'https://api.buffer.com',
    ).strip()
    or 'https://api.buffer.com'
)


BUFFER_API_KEY = os.getenv(
    'BUFFER_API_KEY',
    '',
).strip()


TELEGRAM_BOT_TOKEN = os.getenv(
    'TELEGRAM_BOT_TOKEN',
    '',
).strip()

STAGE4_REPORT_CHAT = (
    os.getenv(
        'STAGE4_REPORT_CHAT',
        '@Select_car_analyz',
    ).strip()
    or '@Select_car_analyz'
)


SOURCE_CHANNEL_URL = (
    os.getenv(
        'SELECT_CARR_SOURCE_CHANNEL_URL',
        'https://t.me/s/DO_L4',
    ).strip()
    or 'https://t.me/s/DO_L4'
)


MARKET_DB = Path(
    os.getenv(
        'SELECT_CARR_MARKET_DB',
        ROOT
        / 'select_carr_market_runtime'
        / 'select_carr_market.sqlite3',
    )
)


ENC_BANK = Path(
    os.getenv(
        'SELECT_CARR_ENCRYPTED_DB',
        ROOT
        / 'private_data'
        / 'select_carr_banks.sqlite3.enc',
    )
)


DATA_SECRET = os.getenv(
    'SELECT_CARR_DATA_SECRET',
    '',
).strip()


STORY_MIN_GAP_MINUTES = 0
STORY_BATCH_MAX = int(os.getenv('STAGE4_STORY_BATCH_MAX', '10'))
STORY_PUBLISH_DELAY_SECONDS = float(os.getenv('STAGE4_STORY_PUBLISH_DELAY_SECONDS', '2'))


POST_CAPTION = (
    'برای دریافت\n'
    '✅بهترین فرصت های خرید و فروش خودروی خودتان\n'
    '✅و قیمت روز خودروها\n'
    '✅و قیمت طلا و دلار ،\n'
    'حتما با ما همراه باشد .\n'
    '@select_carr\n'
    '@select_carrr'
)


MEDIA_BRANCH = os.getenv(
    'SELECT_CARR_STAGE4_MEDIA_BRANCH',
    'select-carr-stage4-media',
).strip()


TEMPLATE_DIR = Path(
    os.getenv(
        'STAGE4_TEMPLATE_DIR',
        ROOT
        / 'assets'
        / 'stage4_templates',
    )
)


BRAND_LOGO_DIR = Path(
    os.getenv(
        'STAGE4_BRAND_LOGO_DIR',
        ROOT
        / 'assets'
        / 'stage4_brand_logos',
    )
)


OUTPUT_DIR = Path(
    os.getenv(
        'STAGE4_OUTPUT_DIR',
        ROOT
        / 'stage4_runtime',
    )
)


PUBLISH_STATE_PATH = (
    OUTPUT_DIR
    / 'publish_state.json'
)


BUY_REQUEST_STORY_ENABLED = True


TEMPLATES = {
    'opportunity':
        TEMPLATE_DIR
        / 'luxury_mercedes_showroom_poster.png',

    'buy_request':
        TEMPLATE_DIR
        / 'luxury_car_showroom_mystery_offer.png',

    'market':
        TEMPLATE_DIR
        / 'luxury_gold_market_showroom_board.png',

    'cars':
        TEMPLATE_DIR
        / 'luxury_gold_car_showroom_price_list.png',
}


EXPECTED_TEMPLATE_SIZES = {
    'opportunity': (
        835,
        1465,
    ),
    'buy_request': (
        869,
        1476,
    ),
    'market': (
        1117,
        1460,
    ),
    'cars': (
        1178,
        1466,
    ),
}


MAX_ZERO_CAR_SLIDES = 10
ZERO_CARS_PER_SLIDE = 16

MAX_ZERO_CARS = (
    MAX_ZERO_CAR_SLIDES
    * ZERO_CARS_PER_SLIDE
)


MAX_PUBLISH_STATE_ENTRIES = 500


SOURCE_SCAN_PAGES = int(
    os.getenv(
        'STAGE4_SOURCE_SCAN_PAGES',
        '12',
    )
)


MARKET_POST_MAX_AGE_HOURS = int(
    os.getenv(
        'STAGE4_MARKET_POST_MAX_AGE_HOURS',
        '24',
    )
)


CAR_POST_MAX_AGE_HOURS = int(
    os.getenv(
        'STAGE4_CAR_POST_MAX_AGE_HOURS',
        '36',
    )
)


OPPORTUNITY_MAX_AGE_HOURS = int(
    os.getenv(
        'STAGE4_OPPORTUNITY_MAX_AGE_HOURS',
        '168',
    )
)


STORY_BACKFILL_MAX_AGE_HOURS = int(
    os.getenv(
        'STAGE4_STORY_BACKFILL_MAX_AGE_HOURS',
        '168',
    )
)


STORY_QUEUE_POLICY_VERSION = 2


REFERENCE_LOOKBACK_DAYS = 20


ARABIC_RE = re.compile(
    '[\\u0600-\\u06FF\\u0750-\\u077F\\u08A0-\\u08FF]'
)


BIDI_CONTROL_RE = re.compile(
    '[\\u061C\\u200E\\u200F\\u202A-\\u202E\\u2066-\\u2069]'
)


CONTROL_RE = re.compile(
    '[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\x7F]'
)


BODY_LABELS = {
    'factory': 'قابل قبول',
    'clean': 'قابل قبول',
    'minor_paint': 'لکه رنگ',
    'painted': 'رنگ‌شده',
    'full_paint': 'تمام رنگ',
    'replaced': 'قطعه تعویضی',
    'accident': 'آسیب‌دیده',
    'unknown': 'قابل قبول',
    '': 'قابل قبول',
}


BRAND_DISPLAY_FA = {
    'hyundai': 'هیوندای',
    'kia': 'کیا',
    'peugeot': 'پژو',
    'renault': 'رنو',
    'toyota': 'تویوتا',
    'lexus': 'لکسوس',
    'porsche': 'پورشه',
    'mercedes-benz': 'مرسدس بنز',
    'mercedes benz': 'مرسدس بنز',
    'nissan': 'نیسان',
    'mazda': 'مزدا',
    'mitsubishi': 'میتسوبیشی',
    'honda': 'هوندا',
    'chery': 'چری',
    'saipa': 'سایپا',
    'iran khodro': 'ایران خودرو',
}


MODEL_DISPLAY_FA = {
    'quick': 'کوییک',
    'shahin': 'شاهین',
    'tara': 'تارا',
    'dena': 'دنا',
    'saina': 'ساینا',
    'tiba': 'تیبا',
}


SOURCE_LABELS = {
    'telegram': 'تلگرام',
    'bale': 'بله',
    'divar': 'دیوار',
    'bama': 'باما',
    'sheypoor': 'شیپور',
    'karnameh': 'کارنامه',
    'khodro45': 'خودرو۴۵',
    'formula': 'فرمولا',
    'hamrah_mechanic': 'همراه مکانیک',
    'hamrahmechanic': 'همراه مکانیک',
}


PERSIAN_MONTHS = (
    'فروردین',
    'اردیبهشت',
    'خرداد',
    'تیر',
    'مرداد',
    'شهریور',
    'مهر',
    'آبان',
    'آذر',
    'دی',
    'بهمن',
    'اسفند',
)


TEHRAN = ZoneInfo(
    'Asia/Tehran'
)


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(
    value: object,
) -> datetime | None:

    raw = str(
        value
        or ''
    ).strip()

    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(
            raw.replace(
                'Z',
                '+00:00',
            )
        )

    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def fa_to_en(
    value: object,
) -> str:

    return str(
        value
        or ''
    ).translate(
        str.maketrans(
            '۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩',
            '01234567890123456789',
        )
    )


def en_to_fa(
    value: object,
) -> str:

    return str(
        value
        or ''
    ).translate(
        str.maketrans(
            '0123456789',
            '۰۱۲۳۴۵۶۷۸۹',
        )
    )


def sanitize_text(
    value: object,
    *,
    multiline: bool = False,
    max_chars: int | None = None,
) -> str:

    text = unicodedata.normalize(
        'NFKC',
        str(
            value
            or ''
        ),
    )

    text = BIDI_CONTROL_RE.sub(
        '',
        text,
    )

    text = CONTROL_RE.sub(
        ' ',
        text,
    )

    text = (
        text
        .replace(
            '\r\n',
            '\n',
        )
        .replace(
            '\r',
            '\n',
        )
    )

    if multiline:
        lines = []

        for line in text.split(
            '\n'
        ):
            line = re.sub(
                '[ \\t\\u00A0]+',
                ' ',
                line,
            ).strip()

            if line:
                lines.append(
                    line
                )

        text = '\n'.join(
            lines
        )

    else:
        text = re.sub(
            '\\s+',
            ' ',
            text,
        ).strip()

    if (
        max_chars is not None
        and len(text) > max_chars
    ):
        text = (
            text[
                :max(
                    1,
                    max_chars - 1,
                )
            ]
            .rstrip()
            + '…'
        )

    return text


def normalize_space(
    value: object,
) -> str:

    return re.sub(
        '\\s+',
        ' ',
        fa_to_en(
            value
        ),
    ).strip()


def money(
    value: object,
) -> str:

    if value in (
        None,
        '',
    ):
        return '-'

    try:
        return f'{int(value):,}'

    except Exception:
        return sanitize_text(
            value,
            max_chars=30,
        )


def format_discount(
    value: object,
) -> str:

    try:
        number = float(
            value
        )

    except Exception:
        return '-'

    shown = (
        str(
            int(
                round(
                    number
                )
            )
        )
        if abs(
            number
            - round(number)
        )
        < 0.05
        else f'{number:.1f}'
    )

    return shown + '٪'


def body_label(
    value: object,
) -> str:

    raw = sanitize_text(
        value,
        max_chars=30,
    )

    return BODY_LABELS.get(
        raw,
        raw
        or 'سالم',
    )


def technical_label(
    row: dict[str, Any],
) -> str:

    explicit = sanitize_text(
        row.get(
            'mechanical_status'
        ),
        max_chars=22,
    )

    return (
        explicit
        or 'فنی سالم'
    )


def compact_vehicle_title(
    row: dict[str, Any],
) -> str:

    brand = sanitize_text(
        row.get('brand'),
        max_chars=35,
    )

    model = sanitize_text(
        row.get('model'),
        max_chars=45,
    )

    trim = sanitize_text(
        row.get('trim'),
        max_chars=35,
    )

    year = sanitize_text(
        row.get('model_year'),
        max_chars=8,
    )

    brand_lower = brand.casefold()
    model_lower = model.casefold()

    model = MODEL_DISPLAY_FA.get(
        model_lower,
        model,
    )

    if ARABIC_RE.search(
        model
    ):
        brand = BRAND_DISPLAY_FA.get(
            brand_lower,
            brand,
        )

        title = ' '.join(
            x
            for x
            in (
                brand,
                model,
                trim,
                year,
            )
            if x
        )

    elif not ARABIC_RE.search(
        brand + model
    ):
        latin_trim = (
            trim
            if (
                trim
                and not ARABIC_RE.search(trim)
            )
            else ''
        )

        title = ' '.join(
            x
            for x
            in (
                brand,
                model,
                latin_trim,
                year,
            )
            if x
        )

    else:
        title = ' '.join(
            x
            for x
            in (
                brand,
                model,
                trim,
                year,
            )
            if x
        )

    if (
        len(title) > 42
        and trim
    ):
        title = ' '.join(
            x
            for x
            in (
                brand,
                model,
                year,
            )
            if x
        )

    return (
        sanitize_text(
            title,
            max_chars=46,
        )
        or 'خودرو'
    )


def safe_filename_token(
    value: object,
) -> str:

    text = (
        normalize_space(
            value
        )
        .casefold()
        .replace(
            ' ',
            '_',
        )
        .replace(
            '-',
            '_',
        )
    )

    return re.sub(
        '[^\\w\\u0600-\\u06ff]+',
        '',
        text,
        flags=re.UNICODE,
    ).strip('_')


def source_label(
    value: object,
) -> str:

    raw = sanitize_text(
        value,
        max_chars=30,
    ).casefold()

    return SOURCE_LABELS.get(
        raw,
        raw
        or 'منبع',
    )


def _font_candidates(
    bold: bool,
) -> list[Path]:

    env_key = (
        'STAGE4_FONT_BOLD'
        if bold
        else 'STAGE4_FONT_REGULAR'
    )

    env_path = os.getenv(
        env_key,
        '',
    ).strip()

    if env_path:
        return [
            Path(
                env_path
            )
        ]

    return [
        ROOT
        / 'assets'
        / 'fonts'
        / (
            'Vazirmatn-Bold.ttf'
            if bold
            else 'Vazirmatn-Regular.ttf'
        )
    ]


def find_font(
    bold: bool = False,
) -> str:

    candidate = _font_candidates(
        bold
    )[0]

    if candidate.is_file():
        return str(
            candidate
        )

    raise RuntimeError(
        'Locked Vazirmatn font is missing: '
        + str(candidate)
    )


def find_latin_font(
    bold: bool = False,
) -> str:

    env_key = (
        'STAGE4_FONT_LATIN_BOLD'
        if bold
        else 'STAGE4_FONT_LATIN_REGULAR'
    )

    env_path = os.getenv(
        env_key,
        '',
    ).strip()

    if env_path:
        candidate = Path(
            env_path
        )

        if candidate.is_file():
            return str(
                candidate
            )

    for candidate in (
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        if bold
        else Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ):
        if candidate.is_file():
            return str(
                candidate
            )

    raise RuntimeError(
        'Latin font is missing'
    )


def font(
    size: int,
    *,
    bold: bool = False,
    latin: bool = False,
) -> ImageFont.FreeTypeFont:

    return ImageFont.truetype(
        (
            find_latin_font(bold)
            if latin
            else find_font(bold)
        ),
        max(
            8,
            int(size),
        ),
    )


def layout_kwargs(
    text: str,
) -> dict[str, str]:

    if ARABIC_RE.search(
        text
    ):
        return {
            'direction': 'rtl',
            'language': 'fa',
        }

    return {
        'direction': 'ltr',
        'language': 'en',
    }


def validate_text_engine() -> dict[str, str]:

    if not features.check(
        'raqm'
    ):
        raise RuntimeError(
            'Pillow RAQM is unavailable; '
            'Stage 4 refuses unsafe Persian rendering.'
        )

    regular = find_font(False)
    bold = find_font(True)
    latin_regular = find_latin_font(False)
    latin_bold = find_latin_font(True)

    probe = Image.new(
        'RGB',
        (
            500,
            100,
        ),
        'white',
    )

    ImageDraw.Draw(
        probe
    ).textbbox(
        (
            0,
            0,
        ),
        'هیوندای سانتافه 1402',
        font=ImageFont.truetype(
            regular,
            28,
        ),
        direction='rtl',
        language='fa',
    )

    ImageDraw.Draw(
        probe
    ).textbbox(
        (
            0,
            0,
        ),
        'Mercedes-Benz E300',
        font=ImageFont.truetype(
            latin_regular,
            28,
        ),
        direction='ltr',
        language='en',
    )

    return {
        'raqm': 'true',
        'regular_font': regular,
        'bold_font': bold,
        'latin_regular_font': latin_regular,
        'latin_bold_font': latin_bold,
    }


def draw_center(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: object,
    *,
    color: str,
    maximum: int,
    minimum: int = 10,
    bold: bool = True,
    spacing: int = 4,
) -> None:

    raw = sanitize_text(
        text,
        multiline=True,
    )

    if not raw:
        return

    kwargs = layout_kwargs(
        raw
    )

    use_latin_font = (
        not ARABIC_RE.search(raw)
        and bool(re.search(r'[A-Za-z0-9%+.,:-]', raw))
    )

    selected = font(
        minimum,
        bold=bold,
        latin=use_latin_font,
    )

    bbox = (
        0,
        0,
        0,
        0,
    )

    for size in range(
        maximum,
        minimum - 1,
        -1,
    ):
        candidate_font = font(
            size,
            bold=bold,
            latin=use_latin_font,
        )

        candidate_bbox = (
            draw.multiline_textbbox(
                (
                    0,
                    0,
                ),
                raw,
                font=candidate_font,
                spacing=spacing,
                align='center',
                **kwargs,
            )
        )

        if (
            candidate_bbox[2]
            - candidate_bbox[0]
            <= box[2]
            - box[0]
            and candidate_bbox[3]
            - candidate_bbox[1]
            <= box[3]
            - box[1]
        ):
            selected = candidate_font
            bbox = candidate_bbox
            break

    else:
        bbox = (
            draw.multiline_textbbox(
                (
                    0,
                    0,
                ),
                raw,
                font=selected,
                spacing=spacing,
                align='center',
                **kwargs,
            )
        )

    width = (
        bbox[2]
        - bbox[0]
    )

    height = (
        bbox[3]
        - bbox[1]
    )

    x = (
        box[0]
        + (
            box[2]
            - box[0]
            - width
        )
        / 2
        - bbox[0]
    )

    y = (
        box[1]
        + (
            box[3]
            - box[1]
            - height
        )
        / 2
        - bbox[1]
    )

    draw.multiline_text(
        (
            x,
            y,
        ),
        raw,
        font=selected,
        fill=color,
        spacing=spacing,
        align='center',
        **kwargs,
    )


def fill_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    color: str,
) -> None:

    draw.rectangle(
        box,
        fill=color,
    )


def erase_region_vertical(
    img: Image.Image,
    box: tuple[int, int, int, int],
    edge_height: int = 5,
) -> None:

    x0 = max(
        0,
        box[0],
    )

    y0 = max(
        0,
        box[1],
    )

    x1 = min(
        img.width,
        box[2],
    )

    y1 = min(
        img.height,
        box[3],
    )

    if (
        x1 <= x0
        or y1 <= y0
    ):
        return

    pixels = img.load()

    span = max(
        1,
        y1
        - y0
        - 1,
    )

    for x in range(
        x0,
        x1,
    ):
        top_samples = [
            pixels[
                x,
                y,
            ]
            for y
            in range(
                max(
                    0,
                    y0 - edge_height,
                ),
                y0,
            )
        ] or [
            pixels[
                x,
                y0,
            ]
        ]

        bottom_samples = [
            pixels[
                x,
                y,
            ]
            for y
            in range(
                y1,
                min(
                    img.height,
                    y1 + edge_height,
                ),
            )
        ] or [
            pixels[
                x,
                y1 - 1,
            ]
        ]

        top = tuple(
            sum(
                pixel[index]
                for pixel
                in top_samples
            )
            // len(top_samples)
            for index
            in range(3)
        )

        bottom = tuple(
            sum(
                pixel[index]
                for pixel
                in bottom_samples
            )
            // len(bottom_samples)
            for index
            in range(3)
        )

        for y in range(
            y0,
            y1,
        ):
            factor = (
                y - y0
            ) / span

            pixels[
                x,
                y,
            ] = tuple(
                int(
                    top[index]
                    * (
                        1 - factor
                    )
                    + bottom[index]
                    * factor
                )
                for index
                in range(3)
            )


def erase_region_horizontal(
    img: Image.Image,
    box: tuple[int, int, int, int],
    edge_width: int = 6,
) -> None:

    x0 = max(
        0,
        box[0],
    )

    y0 = max(
        0,
        box[1],
    )

    x1 = min(
        img.width,
        box[2],
    )

    y1 = min(
        img.height,
        box[3],
    )

    if (
        x1 <= x0
        or y1 <= y0
    ):
        return

    pixels = img.load()

    span = max(
        1,
        x1
        - x0
        - 1,
    )

    for y in range(
        y0,
        y1,
    ):
        left_samples = [
            pixels[
                x,
                y,
            ]
            for x
            in range(
                max(
                    0,
                    x0 - edge_width,
                ),
                x0,
            )
        ] or [
            pixels[
                x0,
                y,
            ]
        ]

        right_samples = [
            pixels[
                x,
                y,
            ]
            for x
            in range(
                x1,
                min(
                    img.width,
                    x1 + edge_width,
                ),
            )
        ] or [
            pixels[
                x1 - 1,
                y,
            ]
        ]

        left = tuple(
            sum(
                pixel[index]
                for pixel
                in left_samples
            )
            // len(left_samples)
            for index
            in range(3)
        )

        right = tuple(
            sum(
                pixel[index]
                for pixel
                in right_samples
            )
            // len(right_samples)
            for index
            in range(3)
        )

        for x in range(
            x0,
            x1,
        ):
            factor = (
                x - x0
            ) / span

            pixels[
                x,
                y,
            ] = tuple(
                int(
                    left[index]
                    * (
                        1 - factor
                    )
                    + right[index]
                    * factor
                )
                for index
                in range(3)
            )


def paste_contain(
    base: Image.Image,
    source: Path,
    box: tuple[int, int, int, int],
) -> None:

    logo = Image.open(
        source
    ).convert(
        'RGBA'
    )

    box_width = max(
        1,
        box[2] - box[0],
    )

    box_height = max(
        1,
        box[3] - box[1],
    )

    ratio = min(
        box_width / logo.width,
        box_height / logo.height,
    )

    logo = logo.resize(
        (
            max(
                1,
                int(
                    logo.width * ratio
                ),
            ),
            max(
                1,
                int(
                    logo.height * ratio
                ),
            ),
        ),
        Image.LANCZOS,
    )

    x = (
        box[0]
        + (
            box_width
            - logo.width
        )
        // 2
    )

    y = (
        box[1]
        + (
            box_height
            - logo.height
        )
        // 2
    )

    rgba = base.convert(
        'RGBA'
    )

    rgba.alpha_composite(
        logo,
        (
            x,
            y,
        ),
    )

    base.paste(
        rgba.convert('RGB')
    )


def find_brand_logo(
    brand: object,
) -> Path | None:

    if not BRAND_LOGO_DIR.is_dir():
        return None

    raw = sanitize_text(
        brand,
        max_chars=80,
    )

    for name in (
        raw,
        safe_filename_token(raw),
        raw.replace(' ', '_'),
        raw.replace(' ', '-'),
    ):
        for extension in (
            '.png',
            '.webp',
            '.jpg',
            '.jpeg',
        ):
            path = (
                BRAND_LOGO_DIR
                / (
                    name
                    + extension
                )
            )

            if path.is_file():
                return path

    return None


def brand_badge_token(
    brand: object,
) -> str:

    raw = sanitize_text(
        brand,
        max_chars=30,
    )

    if not raw:
        return 'SC'

    words = [
        x
        for x
        in re.split(
            '[\\s_-]+',
            raw,
        )
        if x
    ]

    if ARABIC_RE.search(
        raw
    ):
        return raw[:2]

    if len(words) >= 2:
        return (
            words[0][:1]
            + words[1][:1]
        ).upper()

    return raw[:2].upper()


def paint_brand_badge(
    img: Image.Image,
    box: tuple[int, int, int, int],
    brand: object,
) -> None:

    draw = ImageDraw.Draw(
        img
    )

    cx = (
        box[0]
        + box[2]
    ) // 2

    cy = (
        box[1]
        + box[3]
    ) // 2

    radius = max(
        10,
        min(
            box[2] - box[0],
            box[3] - box[1],
        )
        // 2
        - 3,
    )

    circle = (
        cx - radius,
        cy - radius,
        cx + radius,
        cy + radius,
    )

    draw.ellipse(
        circle,
        fill='#F5B942',
        outline='#0B0B0B',
        width=3,
    )

    logo = find_brand_logo(
        brand
    )

    if logo:
        paste_contain(
            img,
            logo,
            (
                circle[0] + 12,
                circle[1] + 12,
                circle[2] - 12,
                circle[3] - 12,
            ),
        )

    else:
        draw_center(
            ImageDraw.Draw(img),
            circle,
            brand_badge_token(
                brand
            ),
            color='#0B0B0B',
            maximum=18,
            minimum=10,
            bold=True,
        )


def buffer_call(
    query: str,
) -> dict[str, Any]:

    if not BUFFER_API_KEY:
        raise RuntimeError('BUFFER_API_KEY is missing')

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                BUFFER_API,
                data=json.dumps({'query': query}).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {BUFFER_API_KEY}',
                },
                method='POST',
            )
            with urllib.request.urlopen(request, timeout=35) as response:
                payload = json.loads(response.read().decode('utf-8'))

            if payload.get('errors'):
                raise RuntimeError(json.dumps(payload['errors'], ensure_ascii=False))
            return payload.get('data') or {}
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f'Buffer request failed after retries: {last_error}')


def telegram_notify(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not STAGE4_REPORT_CHAT:
        print(json.dumps({
            'status': 'telegram_notification_skipped',
            'reason': 'missing_bot_token_or_chat',
        }, ensure_ascii=False))
        return False

    endpoint = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = urllib.parse.urlencode({
        'chat_id': STAGE4_REPORT_CHAT,
        'text': text,
        'disable_web_page_preview': 'true',
    }).encode('utf-8')

    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(endpoint, data=payload, method='POST')
            with urllib.request.urlopen(req, timeout=20) as response:
                body = json.loads(response.read().decode('utf-8'))
            return bool(body.get('ok'))
        except Exception as exc:
            if attempt == 3:
                print(json.dumps({
                    'status': 'telegram_notification_failed',
                    'error': str(exc),
                }, ensure_ascii=False))
                return False
            time.sleep(2 ** (attempt - 1))
    return False


def buffer_channels() -> dict[
    str,
    dict[str, Any],
]:

    data = buffer_call(
        'query { account { organizations { id name } } }'
    )

    found: dict[
        str,
        dict[str, Any],
    ] = {}

    for organization in (
        (
            data.get('account')
            or {}
        )
        .get('organizations')
        or []
    ):
        organization_id = str(
            organization['id']
        )

        channels = (
            buffer_call(
                f'''
                query {{
                  channels(
                    input: {{
                      organizationId:
                        "{organization_id}"
                    }}
                  ) {{
                    id
                    name
                    displayName
                    service
                    isDisconnected
                    isLocked
                  }}
                }}
                '''
            )
            .get('channels')
            or []
        )

        for channel in channels:
            name = str(
                channel.get('name')
                or ''
            ).strip().lower()

            if name in TARGET_CHANNELS:
                found[
                    name
                ] = dict(
                    channel
                )

    return found


def check_buffer() -> int:
    channels = buffer_channels()

    missing = [
        name
        for name
        in TARGET_CHANNELS
        if name not in channels
    ]

    if missing:
        raise RuntimeError(
            'Missing Buffer channels: '
            + ', '.join(missing)
        )

    for name in TARGET_CHANNELS:
        row = channels[name]

        if (
            str(
                row.get('service')
                or ''
            ).lower()
            != 'instagram'
        ):
            raise RuntimeError(
                f'{name} is not Instagram'
            )

        if (
            row.get('isDisconnected')
            is True
        ):
            raise RuntimeError(
                f'{name} is disconnected'
            )

        if (
            row.get('isLocked')
            is True
        ):
            raise RuntimeError(
                f'{name} is locked'
            )

    print(
        json.dumps(
            {
                'status': 'ok',
                'stage4_version':
                    STAGE4_VERSION,
                'channels':
                    list(TARGET_CHANNELS),
            },
            ensure_ascii=False,
        )
    )

    return 0


def _template_keys_for_mode(mode: str = 'all') -> tuple[str, ...]:
    mapping = {
        'opportunity': ('opportunity',),
        'story_queue': ('opportunity', 'buy_request'),
        'market': ('market',),
        'cars': ('cars',),
        'all': ('opportunity', 'buy_request', 'market', 'cars'),
    }
    return mapping.get(mode, mapping['all'])


def validate_templates(mode: str = 'all') -> int:
    text_engine = validate_text_engine()
    details = []
    keys = _template_keys_for_mode(mode)

    for key in keys:
        path = TEMPLATES[key]
        if not path.is_file():
            raise RuntimeError(f'Template missing: {path}')

        try:
            with Image.open(path) as image:
                size = tuple(image.size)
                image_format = image.format
                image.verify()
        except Exception as exc:
            raise RuntimeError(f'Template unreadable: {path}') from exc

        expected = EXPECTED_TEMPLATE_SIZES[key]
        if size != expected:
            raise RuntimeError(
                f'Locked template size mismatch for {key}: '
                f'got={size}, expected={expected}'
            )

        details.append({
            'template': key,
            'file': str(path),
            'size': list(size),
            'format': image_format,
        })

    print(json.dumps({
        'status': 'ok',
        'templates': len(details),
        'validated_mode': mode,
        'stage4_version': STAGE4_VERSION,
        'text_engine': text_engine,
        'buy_request_story_enabled': BUY_REQUEST_STORY_ENABLED,
        'details': details,
    }, ensure_ascii=False, indent=2))
    return 0


def _market_norm(
    value: object,
) -> str:

    text = str(
        value
        or ''
    ).strip().lower()

    for before, after in (
        ('\u200c', ' '),
        ('\u200f', ' '),
        ('\u200e', ' '),
        ('ي', 'ی'),
        ('ك', 'ک'),
        ('ۀ', 'ه'),
        ('ة', 'ه'),
    ):
        text = text.replace(
            before,
            after,
        )

    text = re.sub(
        '[_/\\\\|,:;؛،!?؟()\\[\\]{}]+',
        ' ',
        text,
    )

    return re.sub(
        '\\s+',
        ' ',
        text,
    ).strip()


def _deal_group_key(
    deal: dict[str, Any],
) -> str:

    return '|'.join(
        (
            _market_norm(
                deal.get('brand')
            ),
            _market_norm(
                deal.get('model')
            ),
            _market_norm(
                deal.get('trim')
            ),
            str(
                int(
                    deal.get(
                        'model_year'
                    )
                    or 0
                )
            ),
        )
    )


def _canon_url(
    value: object,
) -> str:

    text = str(
        value
        or ''
    ).strip()

    return (
        text.rstrip('/')
        if len(text) > 8
        else text
    )


def latest_market_deal() -> dict[
    str,
    Any,
] | None:

    if not MARKET_DB.is_file():
        return None

    connection = sqlite3.connect(
        MARKET_DB
    )

    connection.row_factory = (
        sqlite3.Row
    )

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM market_deals
            WHERE status IN (
                'sent',
                'suppressed_system_b'
            )
            ORDER BY
                COALESCE(
                    sent_at,
                    queued_at,
                    candidate_seen_at
                ) DESC
            LIMIT 20
            """
        ).fetchall()

        cutoff = (
            utc_now()
            - timedelta(
                hours=max(
                    1,
                    OPPORTUNITY_MAX_AGE_HOURS,
                )
            )
        )

        for row in rows:
            item = dict(
                row
            )

            effective = (
                parse_iso(
                    item.get('sent_at')
                )
                or parse_iso(
                    item.get('queued_at')
                )
                or parse_iso(
                    item.get(
                        'candidate_seen_at'
                    )
                )
            )

            if (
                effective is not None
                and effective >= cutoff
            ):
                return item

        return None

    finally:
        connection.close()


def deal_reference_breakdown(
    deal: dict[str, Any],
) -> Counter[str]:

    if not MARKET_DB.is_file():
        return Counter()

    as_of = (
        parse_iso(
            deal.get('sent_at')
        )
        or parse_iso(
            deal.get('queued_at')
        )
        or parse_iso(
            deal.get('candidate_seen_at')
        )
        or utc_now()
    )

    cutoff = (
        as_of
        - timedelta(
            days=REFERENCE_LOOKBACK_DAYS
        )
    )

    connection = sqlite3.connect(
        MARKET_DB
    )

    connection.row_factory = (
        sqlite3.Row
    )

    try:
        tables = {
            str(
                row[0]
            )
            for row
            in connection.execute(
                "SELECT name "
                "FROM sqlite_master "
                "WHERE type='table'"
            )
        }

        if (
            'market_listings'
            not in tables
        ):
            return Counter()

        rows = [
            dict(row)
            for row
            in connection.execute(
                """
                SELECT
                    source_key,
                    source,
                    url,
                    last_seen
                FROM market_listings
                WHERE group_key=?
                  AND source_key<>?
                  AND price>0
                  AND julianday(last_seen)>=julianday(?)
                  AND julianday(last_seen)<=julianday(?)
                ORDER BY
                    last_seen DESC,
                    source_key
                """,
                (
                    _deal_group_key(deal),
                    str(
                        deal.get('source_key')
                        or ''
                    ),
                    cutoff.isoformat(),
                    as_of.isoformat(),
                ),
            )
        ]

    finally:
        connection.close()

    unique: list[
        dict[str, Any]
    ] = []

    seen_keys = set()
    seen_urls = set()

    for row in rows:
        key = str(
            row.get('source_key')
            or ''
        )

        url = _canon_url(
            row.get('url')
        )

        if (
            key in seen_keys
            or (
                url
                and url in seen_urls
            )
        ):
            continue

        seen_keys.add(key)

        if url:
            seen_urls.add(url)

        unique.append(row)

    return Counter(
        str(
            row.get('source')
            or 'unknown'
        )
        for row
        in unique
    )


def comparison_lines(
    deal: dict[str, Any],
) -> tuple[str, str]:

    try:
        total = int(
            deal.get('sample_count')
            or 0
        )

    except Exception:
        total = 0

    breakdown = (
        deal_reference_breakdown(
            deal
        )
    )

    source_count = len(
        breakdown
    )

    if (
        total > 0
        and source_count > 0
    ):
        line1 = (
            f'با {en_to_fa(total)} نمونه مشابه '
            f'از {en_to_fa(source_count)} منبع مقایسه شده'
        )

    elif total > 0:
        line1 = (
            f'با {en_to_fa(total)} نمونه مشابه مقایسه شده'
        )

    else:
        line1 = (
            'با نمونه‌های مشابه بازار مقایسه شده'
        )

    if not breakdown:
        return (
            line1,
            'بانک چندمنبعی Select Carr',
        )

    ordered = sorted(
        breakdown.items(),
        key=lambda item: (
            -item[1],
            source_label(item[0]),
        ),
    )

    exact_counts = (
        total > 0
        and sum(
            count
            for _,
            count
            in ordered
        )
        == total
    )

    parts = []

    for source, count in ordered[:4]:
        label = source_label(
            source
        )

        if label == 'همراه مکانیک':
            label = 'همراه‌مکانیک'

        parts.append(
            (
                f'{label}: {en_to_fa(count)}'
                if exact_counts
                else label
            )
        )

    if len(ordered) > 4:
        parts.append(
            f'+{en_to_fa(len(ordered) - 4)} منبع'
        )

    return (
        line1,
        ' • '.join(parts),
    )


def comparison_caption(
    deal: dict[str, Any],
) -> str:

    line1, line2 = (
        comparison_lines(
            deal
        )
    )

    return (
        line1
        + '\n'
        + line2
    )


@dataclass(
    frozen=True
)
class TelegramPost:
    post_id: str
    text: str
    published_at: datetime | None


def normalized_source_channel_url() -> str:
    raw = (
        SOURCE_CHANNEL_URL.strip()
        or 'https://t.me/s/DO_L4'
    )

    if raw.startswith('@'):
        return (
            'https://t.me/s/'
            + raw[1:]
        )

    parsed = urllib.parse.urlparse(
        raw
    )

    if (
        parsed.netloc.lower()
        in {
            't.me',
            'www.t.me',
        }
    ):
        path = (
            parsed.path
            .strip('/')
        )

        if path.startswith('s/'):
            username = (
                path[2:]
                .split('/')[0]
            )

        else:
            username = (
                path
                .split('/')[0]
            )

        return (
            'https://t.me/s/'
            + username
        )

    return raw.rstrip('/')


def require_source_channel() -> str:
    url = (
        normalized_source_channel_url()
    )

    if not url:
        raise RuntimeError(
            'Telegram source channel URL is empty'
        )

    return url


def _fetch_html(
    url: str,
) -> str:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    'User-Agent': (
                        'Mozilla/5.0 (X11; Linux x86_64) '
                        'AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'
                    ),
                    'Accept-Language': 'fa-IR,fa;q=0.9,en;q=0.6',
                },
            )
            with urllib.request.urlopen(request, timeout=35) as response:
                return response.read().decode('utf-8', errors='ignore')
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f'Telegram source fetch failed after retries: {last_error}')


def _telegram_body_text(
    node: Any,
) -> str:

    body = (
        BeautifulSoup(
            str(node),
            'html.parser',
        )
        .select_one(
            '.tgme_widget_message_text'
        )
    )

    if body is None:
        return ''

    for br in body.find_all('br'):
        br.replace_with('\n')

    text = body.get_text(
        '',
        strip=False,
    )

    lines: list[str] = []
    previous_blank = False

    for raw_line in text.splitlines():
        line = re.sub(
            '[ \\t]+',
            ' ',
            raw_line,
        ).strip()

        if not line:
            if (
                lines
                and not previous_blank
            ):
                lines.append('')

            previous_blank = True
            continue

        lines.append(line)
        previous_blank = False

    while (
        lines
        and lines[-1] == ''
    ):
        lines.pop()

    return sanitize_text(
        '\n'.join(lines),
        multiline=True,
    )


def telegram_posts(
    max_pages: int = SOURCE_SCAN_PAGES,
) -> list[TelegramPost]:

    base = require_source_channel()

    collected: dict[
        str,
        TelegramPost,
    ] = {}

    before: int | None = None

    for _page in range(
        max(
            1,
            max_pages,
        )
    ):
        url = (
            base
            if before is None
            else f'{base}?before={before}'
        )

        soup = BeautifulSoup(
            _fetch_html(url),
            'html.parser',
        )

        page_ids: list[int] = []
        found_any = False

        for node in soup.select(
            'div.tgme_widget_message[data-post]'
        ):
            data_post = str(
                node.get('data-post')
                or ''
            ).strip()

            if (
                not data_post
                or '/' not in data_post
            ):
                continue

            channel_name, post_id = (
                data_post.rsplit(
                    '/',
                    1,
                )
            )

            if (
                channel_name.casefold()
                != 'DO_L4'.casefold()
            ):
                continue

            try:
                numeric_id = int(
                    post_id
                )

                page_ids.append(
                    numeric_id
                )

            except ValueError:
                continue

            text_node = (
                node.select_one(
                    '.tgme_widget_message_text'
                )
            )

            if text_node is None:
                continue

            text = _telegram_body_text(
                text_node
            )

            if not text:
                continue

            time_node = (
                node.select_one(
                    'time[datetime]'
                )
            )

            published = parse_iso(
                time_node.get('datetime')
                if time_node
                else None
            )

            collected[
                post_id
            ] = TelegramPost(
                post_id=post_id,
                text=text,
                published_at=published,
            )

            found_any = True

        if (
            not found_any
            or not page_ids
        ):
            break

        oldest = min(
            page_ids
        )

        if (
            before is not None
            and oldest >= before
        ):
            break

        before = oldest

    return sorted(
        collected.values(),
        key=lambda post: (
            post.published_at
            or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            (
                int(post.post_id)
                if post.post_id.isdigit()
                else 0
            ),
        ),
    )


def _tehran_date(
    post: TelegramPost,
):
    if post.published_at is None:
        return None

    return (
        post.published_at
        .astimezone(TEHRAN)
        .date()
    )


def _today_tehran():
    return (
        datetime.now(
            TEHRAN
        ).date()
    )


def _first_price_match(
    text: str,
    pattern: str,
) -> tuple[int, str] | None:

    normalized = fa_to_en(
        text
    )

    match = re.search(
        pattern,
        normalized,
        flags=re.I,
    )

    if not match:
        return None

    token = (
        match.group(1)
        .replace(
            ',',
            '',
        )
        .strip()
    )

    try:
        number = int(
            float(token)
        )

    except ValueError:
        return None

    return (
        number,
        match.group(1),
    )


def _market_rate_display(
    text: str,
    kind: str,
) -> dict[str, str] | None:

    patterns = {
        'usd':
            r'دلار\s*[:：]\s*([0-9][0-9,.]*)',

        'gold18':
            r'گرم\s*18\s*عیار(?:\s*\([^)]*\))?\s*[:：]\s*([0-9][0-9,.]*)',

        'coin':
            r'سکه\s*(?:جدید|امامی)\s*[:：]\s*([0-9][0-9,.]*)',

        'ounce':
            r'اونس\s*[:：]\s*([0-9][0-9,.]*)',
    }

    found = _first_price_match(
        text,
        patterns[kind],
    )

    if not found:
        return None

    number, _raw_token = found

    unit = {
        'usd': 'تومان',
        'gold18': 'هزار تومان',
        'coin': 'هزار تومان',
        'ounce': 'دلار',
    }[
        kind
    ]

    return {
        'display':
            f'{number:,}',

        'unit':
            unit,
    }


def _is_market_rates(
    post: TelegramPost,
) -> bool:

    text = normalize_space(
        post.text
    )

    return (
        'نرخ فروش' in text
        and 'دلار' in text
        and 'ارز' in text
        and (
            'سکه' in text
            or 'طلا' in text
        )
    )


def extract_date_label(
    text: str,
) -> str:

    normalized = fa_to_en(
        text
    )

    months = '|'.join(
        map(
            re.escape,
            PERSIAN_MONTHS,
        )
    )

    match = re.search(
        rf'({months})\s*(?:[0-3]?\d\s*)?(1[34]\d{{2}})',
        normalized,
    )

    if match:
        return (
            f'{match.group(1)} '
            f'{match.group(2)}'
        )

    match = re.search(
        rf'(1[34]\d{{2}})\s*({months})',
        normalized,
    )

    if match:
        return (
            f'{match.group(2)} '
            f'{match.group(1)}'
        )

    return 'قیمت روز'


def _car_price_values(
    token: str,
) -> tuple[int, str] | None:

    raw = (
        fa_to_en(token)
        .strip()
        .replace(
            ' ',
            '',
        )
    )

    if not raw:
        return None

    if re.fullmatch(
        r'\d{1,2}[.]\d{1,4}',
        raw,
    ):
        try:
            toman = int(
                round(
                    float(raw)
                    * 1_000_000_000
                )
            )

        except ValueError:
            return None

        million = int(
            round(
                toman
                / 1_000_000
            )
        )

        return (
            toman,
            f'{million:,}',
        )

    digits = re.sub(
        r'\D',
        '',
        raw,
    )

    if not digits:
        return None

    number = int(
        digits
    )

    if number >= 100_000_000:
        toman = number

        million = (
            toman
            / 1_000_000
        )

        display = (
            f'{int(million):,}'
            if float(
                million
            ).is_integer()
            else (
                f'{million:,.1f}'
                .rstrip('0')
                .rstrip('.')
            )
        )

        return (
            toman,
            display,
        )

    if (
        1000
        <= number
        <= 99999
    ):
        toman = (
            number
            * 1_000_000
        )

        return (
            toman,
            f'{number:,}',
        )

    return None


def _zero_car_line(
    line: str,
) -> dict[str, Any] | None:
    raw_line = sanitize_text(line, max_chars=180)
    if not raw_line:
        return None

    # Primary source format: vehicle ⬅ price / variant:price
    parts = re.split(r'⬅️?|←|➡️?|=>|→', raw_line, maxsplit=1)

    if len(parts) == 2:
        left = sanitize_text(parts[0], max_chars=70).strip(' -:|')
        right = sanitize_text(parts[1], max_chars=120).strip()
    else:
        # Tolerate simple "vehicle : price" rows while avoiding headings.
        m = re.match(
            r'^(.{2,70}?)\s*[:：]\s*([0-9۰-۹٠-٩][0-9۰-۹٠-٩,./ ]{2,40})$',
            raw_line,
        )
        if not m:
            return None
        left = sanitize_text(m.group(1), max_chars=70).strip(' -:|')
        right = sanitize_text(m.group(2), max_chars=120).strip()

    if not left or not right:
        return None

    match = re.search(
        r'(?<!\d)(\d{1,3}(?:[.,]\d{1,4})?|\d{4,13})(?!\d)',
        fa_to_en(right),
    )
    if not match:
        return None

    parsed_price = _car_price_values(match.group(1).replace(',', ''))
    if not parsed_price:
        return None

    price, display_price = parsed_price
    if price < 100_000_000:
        return None

    prefix = sanitize_text(right[:match.start()], max_chars=32).strip(' :/|-')
    name = left
    variants: list[str] = []

    paren = re.match(r'^(.*?)\(([^)]{1,28})\)(.*)$', name)
    if paren:
        base = sanitize_text((paren.group(1) + ' ' + paren.group(3)).strip(), max_chars=42)
        if base:
            name = base
        variants.append(sanitize_text(paren.group(2), max_chars=22))

    if prefix and prefix not in {'قیمت', 'تومان', 'میلیون'}:
        variants.append(prefix)

    variant = sanitize_text(' '.join(v for v in variants if v), max_chars=28)
    return {
        'name': sanitize_text(name, max_chars=42),
        'variant': variant,
        'price': price,
        'display_price': display_price,
    }


def _plain_car_price_token(line: str) -> tuple[int, str] | None:
    raw = fa_to_en(sanitize_text(line, max_chars=40)).replace(' ', '')
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d{3,5}(?:\.\d{1,2})?)', raw):
        return None
    return _car_price_values(raw.replace(',', ''))


def _looks_like_vehicle_name(line: str) -> bool:
    text = sanitize_text(line, max_chars=70)
    if not text or len(text) < 2:
        return False
    low = normalize_space(text).casefold()
    blockers = (
        'قیمت روز', 'نبض بازار', 'آخرین بروزرسانی', 'نسبت به',
        'ارقام زیر', 'اعلامی فروشندگان', 'کاهش قیمت', 'افزایش قیمت',
        'هشدار', 'میلیون تومان', 'تومان', 'دلار', 'بازار خودرو',
    )
    if any(x in low for x in blockers):
        return False
    if text.startswith('#') or re.fullmatch(r'[+\-⬆️⬇️0-9۰-۹., ]+', text):
        return False
    return bool(ARABIC_RE.search(text) or re.search(r'[A-Za-z]', text))


def extract_zero_car_items(text: str) -> list[dict[str, Any]]:
    lines = [sanitize_text(x, max_chars=180) for x in text.splitlines()]
    lines = [x for x in lines if x]
    items: list[dict[str, Any]] = []

    # Format A: arrow / colon rows.
    for line in lines:
        item = _zero_car_line(line)
        if item:
            items.append(item)
            if len(items) >= MAX_ZERO_CARS:
                break

    # Format B: "نبض بازار" style, name on one line and price on the next.
    if len(items) < 8:
        section_active = False
        paired: list[dict[str, Any]] = []
        i = 0
        while i < len(lines) - 1:
            line = lines[i]
            if line.startswith('#') and any(
                token in line
                for token in ('سایپا', 'ایران', 'خودرو', 'مونتاژ', 'وارداتی')
            ):
                section_active = True
                i += 1
                continue

            if section_active and _looks_like_vehicle_name(line):
                parsed = _plain_car_price_token(lines[i + 1])
                if parsed:
                    price, display = parsed
                    if price >= 100_000_000:
                        paired.append({
                            'name': sanitize_text(line, max_chars=42),
                            'variant': '',
                            'price': price,
                            'display_price': display,
                        })
                        i += 2
                        continue
            i += 1
        if len(paired) >= 8:
            items = paired[:MAX_ZERO_CARS]

    # Deduplicate exact visible rows without inventing data.
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for item in items:
        key = (normalize_space(item.get('name')).casefold(), int(item.get('price') or 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= MAX_ZERO_CARS:
            break
    return out


def _is_zero_car_prices(
    post: TelegramPost,
) -> bool:
    items = extract_zero_car_items(post.text)
    if len(items) < 8:
        return False
    normalized = normalize_space(post.text).casefold()
    signals = ('خودرو', 'ساینا', 'کوییک', 'شاهین', 'سورن', 'تارا', 'پژو', '#سایپا', '#ایران')
    return sum(1 for s in signals if s in normalized) >= 2


def template(
    name: str,
) -> Image.Image:

    path = TEMPLATES[
        name
    ]

    if not path.is_file():
        raise RuntimeError(
            f'Template missing: {path}'
        )

    return (
        Image.open(path)
        .convert('RGB')
    )


def output_path(
    name: str,
) -> Path:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    return (
        OUTPUT_DIR
        / name
    )


def _scaled_box(
    box: tuple[int, int, int, int],
    *,
    base: tuple[int, int],
    actual: tuple[int, int],
) -> tuple[int, int, int, int]:
    sx = actual[0] / base[0]
    sy = actual[1] / base[1]
    return tuple(int(round(v * (sx if i % 2 == 0 else sy))) for i, v in enumerate(box))


def render_opportunity(
    deal: dict[str, Any],
    output_name: str = 'story_opportunity.jpeg',
) -> Path:
    img = template('opportunity')
    draw = ImageDraw.Draw(img)

    title = compact_vehicle_title(deal)
    brand = sanitize_text(deal.get('brand'), max_chars=40)
    discount = format_discount(deal.get('discount_percent')).replace('٪', '%')
    year = sanitize_text(deal.get('model_year'), max_chars=8) or '-'
    mileage = 'نامشخص' if deal.get('mileage') is None else f"{money(deal.get('mileage'))}\nکیلومتر"
    technical = technical_label(deal)
    body = body_label(deal.get('body_condition'))

    # Fixed header/artwork stay untouched. Only dynamic percentage is replaced.
    percent_box = (572, 151, 650, 204)
    fill_box(draw, percent_box, '#E50914')
    draw_center(draw, percent_box, discount, color='#FFFFFF', maximum=26, minimum=15, bold=True)

    # Brand + title strip.
    paint_brand_badge(img, (57, 846, 137, 911), brand)
    title_box = (150, 851, 565, 905)
    erase_region_horizontal(img, title_box)
    draw = ImageDraw.Draw(img)
    draw_center(draw, title_box, title, color='#0B0B0B', maximum=27, minimum=13, bold=True)

    # Value rows only; labels/icons from the locked template remain visible.
    fields = [
        ((105, 950, 198, 980), year, 20, 11),
        ((285, 949, 400, 985), mileage, 17, 9),
        ((470, 950, 590, 980), sanitize_text(technical, max_chars=22), 17, 9),
        ((662, 950, 766, 980), sanitize_text(body, max_chars=22), 17, 9),
    ]
    for box, text, maximum, minimum in fields:
        fill_box(draw, box, '#0B0B0B')
        draw_center(draw, box, text, color='#F4EFE6', maximum=maximum, minimum=minimum, spacing=1, bold=True)

    for box, value in [
        ((130, 1024, 325, 1056), deal.get('candidate_price')),
        ((500, 1024, 708, 1056), deal.get('average_price')),
    ]:
        fill_box(draw, box, '#0B0B0B')
        draw_center(draw, box, money(value), color='#FFD56A', maximum=22, minimum=12, bold=True)

    breakdown = deal_reference_breakdown(deal)
    try:
        total = int(deal.get('sample_count') or 0)
    except Exception:
        total = 0
    source_count = len(breakdown)

    # Exact approved sentence layout, with real counts highlighted in gold.
    headline_box = (50, 1070, 785, 1131)
    fill_box(draw, headline_box, '#0B0B0B')
    draw_center(draw, (690, 1080, 780, 1123), 'با', color='#FFFFFF', maximum=27, minimum=18, bold=True)
    draw_center(draw, (620, 1074, 690, 1126), en_to_fa(total) if total else '-', color='#F5B942', maximum=40, minimum=24, bold=True)
    draw_center(draw, (410, 1080, 620, 1123), 'نمونه مشابه از', color='#FFFFFF', maximum=27, minimum=16, bold=True)
    draw_center(draw, (350, 1074, 415, 1126), en_to_fa(source_count) if source_count else '-', color='#F5B942', maximum=36, minimum=23, bold=True)
    draw_center(draw, (80, 1080, 355, 1123), 'منبع مقایسه شده', color='#FFFFFF', maximum=27, minimum=16, bold=True)

    normalized_counts: dict[str, int] = {}
    for source, count in breakdown.items():
        source_key = str(source or '').strip().casefold()
        if source_key == 'hamrahmechanic':
            source_key = 'hamrah_mechanic'
        normalized_counts[source_key] = normalized_counts.get(source_key, 0) + int(count)

    source_slots = {
        'divar': (142, 1162, 198, 1205),
        'hamrah_mechanic': (323, 1162, 380, 1205),
        'karnameh': (506, 1162, 564, 1205),
        'telegram': (690, 1162, 748, 1205),
    }
    for source_key, box in source_slots.items():
        fill_box(draw, box, '#0B0B0B')
        count = normalized_counts.get(source_key, 0)
        if count > 0:
            draw_center(draw, box, en_to_fa(count), color='#F5B942', maximum=31, minimum=20, bold=True, spacing=1)

    path = output_path(output_name)
    img.save(path, 'JPEG', quality=96, subsampling=0)
    return path


def render_market_post(
    values: dict[str, Any],
) -> Path:

    img = template(
        'market'
    )

    draw = ImageDraw.Draw(img)

    regions = {
        'gold18': (
            (
                178,
                1000,
                503,
                1093,
            ),
            (
                182,
                1004,
                499,
                1052,
            ),
            (
                216,
                1057,
                465,
                1087,
            ),
        ),

        'usd': (
            (
                616,
                1000,
                952,
                1093,
            ),
            (
                620,
                1004,
                948,
                1052,
            ),
            (
                655,
                1057,
                913,
                1087,
            ),
        ),

        'coin': (
            (
                178,
                1224,
                503,
                1323,
            ),
            (
                182,
                1228,
                499,
                1276,
            ),
            (
                216,
                1281,
                465,
                1312,
            ),
        ),

        'ounce': (
            (
                616,
                1224,
                952,
                1323,
            ),
            (
                620,
                1228,
                948,
                1276,
            ),
            (
                655,
                1281,
                913,
                1312,
            ),
        ),
    }

    for key, (
        region,
        number_box,
        unit_box,
    ) in regions.items():

        fill_box(
            draw,
            region,
            '#0B0B0B',
        )

        item = (
            values.get(key)
            or {}
        )

        display = (
            sanitize_text(
                (
                    item.get('display')
                    if isinstance(
                        item,
                        dict,
                    )
                    else item
                ),
                max_chars=20,
            )
            or '-'
        )

        unit = sanitize_text(
            (
                item.get('unit')
                if isinstance(
                    item,
                    dict,
                )
                else ''
            ),
            max_chars=18,
        )

        draw_center(
            draw,
            number_box,
            display,
            color='#FFFFFF',
            maximum=48,
            minimum=20,
        )

        draw_center(
            draw,
            unit_box,
            unit,
            color='#F4EFE6',
            maximum=18,
            minimum=10,
            bold=False,
        )

    path = output_path(
        'post_market.jpeg'
    )

    img.save(
        path,
        'JPEG',
        quality=96,
        subsampling=0,
    )

    return path


def chunks(
    items: list[Any],
    size: int,
) -> list[list[Any]]:

    return [
        items[
            index:
            index + size
        ]
        for index
        in range(
            0,
            len(items),
            size,
        )
    ]


def _paint_floor_texture(
    img: Image.Image,
    box: tuple[int, int, int, int],
) -> None:

    x0 = max(
        0,
        int(box[0]),
    )

    y0 = max(
        0,
        int(box[1]),
    )

    x1 = min(
        img.width,
        int(box[2]),
    )

    y1 = min(
        img.height,
        int(box[3]),
    )

    if (
        x1 <= x0
        or y1 <= y0
    ):
        return

    source_y0 = min(
        img.height - 2,
        1380,
    )

    source_y1 = min(
        img.height,
        1430,
    )

    if source_y1 <= source_y0:
        return

    strip = img.crop(
        (
            x0,
            source_y0,
            x1,
            source_y1,
        )
    )

    width = (
        x1 - x0
    )

    height = (
        y1 - y0
    )

    tile_height = max(
        12,
        strip.height,
    )

    strip = strip.resize(
        (
            width,
            tile_height,
        ),
        Image.Resampling.LANCZOS,
    )

    patch = Image.new(
        'RGB',
        (
            width,
            height,
        ),
    )

    offset = 0
    tile_index = 0

    while offset < height:
        tile = (
            strip
            if tile_index % 2 == 0
            else ImageOps.flip(strip)
        )

        patch.paste(
            tile,
            (
                0,
                offset,
            ),
        )

        offset += tile.height
        tile_index += 1

    patch = (
        patch
        .crop(
            (
                0,
                0,
                width,
                height,
            )
        )
        .filter(
            ImageFilter.GaussianBlur(
                radius=0.45
            )
        )
    )

    img.paste(
        patch,
        (
            x0,
            y0,
        ),
    )


def render_car_prices(
    data: dict[str, Any],
) -> list[Path]:

    items = list(
        data.get('items')
        or []
    )[:MAX_ZERO_CARS]

    if not items:
        return []

    pages = chunks(
        items,
        ZERO_CARS_PER_SLIDE,
    )

    paths: list[Path] = []

    row_bands = [
        (505, 599),
        (609, 689),
        (699, 778),
        (788, 868),
        (878, 958),
        (968, 1048),
        (1058, 1140),
        (1150, 1242),
    ]

    for page_number, page_items in enumerate(
        pages,
        start=1,
    ):
        img = template(
            'cars'
        )

        draw = ImageDraw.Draw(img)

        calendar_icon = img.crop(
            (
                66,
                115,
                101,
                158,
            )
        )

        fill_box(
            draw,
            (
                58,
                96,
                241,
                188,
            ),
            '#0B0B0B',
        )

        img.paste(
            calendar_icon,
            (
                66,
                115,
            ),
        )

        draw = ImageDraw.Draw(img)

        draw_center(
            draw,
            (
                112,
                101,
                239,
                143,
            ),
            en_to_fa(
                sanitize_text(
                    data.get('date_label')
                    or 'قیمت روز',
                    max_chars=20,
                )
            ),
            color='#FFFFFF',
            maximum=20,
            minimum=11,
            bold=True,
        )

        draw_center(
            draw,
            (
                106,
                146,
                239,
                180,
            ),
            'قیمت ها به میلیون تومان',
            color='#F4EFE6',
            maximum=12,
            minimum=8,
            bold=False,
        )

        item_count = len(
            page_items
        )

        used_rows = (
            item_count
            + 1
        ) // 2

        if (
            item_count % 2 == 1
            and used_rows > 0
        ):
            odd_y0, odd_y1 = (
                row_bands[
                    used_rows - 1
                ]
            )

            _paint_floor_texture(
                img,
                (
                    596,
                    odd_y0 - 7,
                    1118,
                    odd_y1 + 7,
                ),
            )

        if used_rows < len(
            row_bands
        ):
            unused_y0 = (
                row_bands[
                    used_rows
                ][0]
            )

            unused_y1 = (
                row_bands[-1][1]
            )

            _paint_floor_texture(
                img,
                (
                    0,
                    unused_y0 - 7,
                    img.width,
                    unused_y1 + 7,
                ),
            )

        draw = ImageDraw.Draw(img)

        for row_index, (
            y0,
            y1,
        ) in enumerate(
            row_bands
        ):
            for side_index, side in enumerate(
                (
                    'left',
                    'right',
                )
            ):
                slot = (
                    row_index * 2
                    + side_index
                )

                if slot >= len(
                    page_items
                ):
                    continue

                item = page_items[
                    slot
                ]

                global_index = (
                    (
                        page_number - 1
                    )
                    * ZERO_CARS_PER_SLIDE
                    + slot
                    + 1
                )

                if side == 'left':
                    panel = (
                        69,
                        y0,
                        573,
                        y1,
                    )

                    index_box = (
                        83,
                        y0 + 17,
                        132,
                        min(
                            y1 - 12,
                            y0 + 60,
                        ),
                    )

                    name_box = (
                        151,
                        y0 + 8,
                        318,
                        min(
                            y1 - 34,
                            y0 + 40,
                        ),
                    )

                    variant_box = (
                        151,
                        y0 + 39,
                        318,
                        y1 - 8,
                    )

                    separator_x = 330

                    price_box = (
                        351,
                        y0 + 9,
                        540,
                        min(
                            y1 - 22,
                            y0 + 48,
                        ),
                    )

                else:
                    panel = (
                        604,
                        y0,
                        1107,
                        y1,
                    )

                    index_box = (
                        618,
                        y0 + 17,
                        667,
                        min(
                            y1 - 12,
                            y0 + 60,
                        ),
                    )

                    name_box = (
                        688,
                        y0 + 8,
                        858,
                        min(
                            y1 - 34,
                            y0 + 40,
                        ),
                    )

                    variant_box = (
                        688,
                        y0 + 39,
                        858,
                        y1 - 8,
                    )

                    separator_x = 873

                    price_box = (
                        897,
                        y0 + 9,
                        1087,
                        min(
                            y1 - 22,
                            y0 + 48,
                        ),
                    )

                draw.rounded_rectangle(
                    panel,
                    radius=14,
                    fill='#0B0B0B',
                    outline='#C88A16',
                    width=2,
                )

                draw.line(
                    (
                        separator_x,
                        y0 + 10,
                        separator_x,
                        y1 - 10,
                    ),
                    fill='#C88A16',
                    width=2,
                )

                draw.rounded_rectangle(
                    index_box,
                    radius=7,
                    fill='#F5B942',
                )

                draw_center(
                    draw,
                    index_box,
                    f'{global_index:02d}',
                    color='#0B0B0B',
                    maximum=17,
                    minimum=11,
                    bold=True,
                )

                draw_center(
                    draw,
                    name_box,
                    sanitize_text(
                        item.get('name'),
                        max_chars=24,
                    ),
                    color='#F5B942',
                    maximum=32,
                    minimum=13,
                    bold=True,
                )

                draw_center(
                    draw,
                    variant_box,
                    sanitize_text(
                        item.get('variant'),
                        max_chars=18,
                    ),
                    color='#F4EFE6',
                    maximum=16,
                    minimum=10,
                    bold=False,
                )

                draw_center(
                    draw,
                    price_box,
                    sanitize_text(
                        item.get('display_price')
                        or money(
                            item.get('price')
                        ),
                        max_chars=16,
                    ),
                    color='#FFFFFF',
                    maximum=31,
                    minimum=14,
                    bold=True,
                )

        fill_box(
            draw,
            (
                966,
                1294,
                1048,
                1337,
            ),
            '#0B0B0B',
        )

        draw_center(
            draw,
            (
                966,
                1294,
                1048,
                1337,
            ),
            (
                f'{en_to_fa(page_number)} '
                f'از {en_to_fa(len(pages))}'
            ),
            color='#FFFFFF',
            maximum=18,
            minimum=10,
            bold=True,
        )

        path = output_path(
            f'post_car_prices_{page_number:02d}.jpeg'
        )

        img.save(
            path,
            'JPEG',
            quality=96,
            subsampling=0,
        )

        paths.append(
            path
        )

    return paths


def publication_token(
    channel: str,
    key: str,
) -> str:

    return (
        channel
        + '|'
        + key
    )


def is_published(
    state: dict[str, Any],
    channel: str,
    key: str,
) -> bool:

    return (
        publication_token(
            channel,
            key,
        )
        in (
            state.get('published')
            or {}
        )
    )


def mark_published(
    state: dict[str, Any],
    channel: str,
    key: str,
    post_id: str,
) -> None:

    state.setdefault(
        'published',
        {},
    )[
        publication_token(
            channel,
            key,
        )
    ] = {
        'channel': channel,
        'content_key': key,
        'post_id': post_id,
        'published_at': iso_now(),
    }

    save_publish_state(
        state
    )


def github_raw_url(
    name: str,
) -> str:

    repository = os.getenv(
        'GITHUB_REPOSITORY',
        '',
    ).strip()

    if not repository:
        raise RuntimeError(
            'GITHUB_REPOSITORY missing'
        )

    return (
        'https://raw.githubusercontent.com/'
        + repository
        + '/'
        + MEDIA_BRANCH
        + '/'
        + urllib.parse.quote(name)
    )


def graphql_string(
    value: str,
) -> str:

    return json.dumps(
        value,
        ensure_ascii=False,
    )


def publish_image(
    channel_id: str,
    url: str,
    *,
    post_type: str,
    text: str = '',
) -> str:

    query = f"""
    mutation {{
      createPost(
        input: {{
          text:
            {graphql_string(text)}

          channelId:
            {graphql_string(channel_id)}

          schedulingType:
            automatic

          mode:
            shareNow

          assets: [
            {{
              image: {{
                url:
                  {graphql_string(url)}
              }}
            }}
          ]

          metadata: {{
            instagram: {{
              type:
                {post_type}

              shouldShareToFeed:
                {
                    "false"
                    if post_type == "story"
                    else "true"
                }
            }}
          }}
        }}
      ) {{
        ... on PostActionSuccess {{
          post {{
            id
            status
          }}
        }}

        ... on MutationError {{
          message
        }}
      }}
    }}
    """

    result = (
        buffer_call(
            query
        )
        .get('createPost')
    )

    if not result:
        raise RuntimeError(
            'Buffer createPost empty response'
        )

    if result.get('message'):
        raise RuntimeError(
            result['message']
        )

    post = result.get('post')

    if not post:
        raise RuntimeError(
            'Buffer did not return post'
        )

    return str(
        post['id']
    )


def publish_carousel(
    channel_id: str,
    urls: list[str],
    *,
    caption: str,
) -> str:

    if len(urls) < 2:
        raise ValueError(
            'Carousel requires at least two images'
        )

    assets = '\n'.join(
        (
            '{ image: { url: '
            + graphql_string(url)
            + ' } }'
        )
        for url
        in urls
    )

    query = f"""
    mutation {{
      createPost(
        input: {{
          text:
            {graphql_string(caption)}

          channelId:
            {graphql_string(channel_id)}

          schedulingType:
            automatic

          mode:
            shareNow

          assets: [
            {assets}
          ]

          metadata: {{
            instagram: {{
              type:
                post

              shouldShareToFeed:
                true
            }}
          }}
        }}
      ) {{
        ... on PostActionSuccess {{
          post {{
            id
            status
          }}
        }}

        ... on MutationError {{
          message
        }}
      }}
    }}
    """

    result = (
        buffer_call(query)
        .get('createPost')
    )

    if not result:
        raise RuntimeError(
            'Buffer carousel empty response'
        )

    if result.get('message'):
        raise RuntimeError(
            result['message']
        )

    post = result.get('post')

    if not post:
        raise RuntimeError(
            'Buffer did not return carousel'
        )

    return str(
        post['id']
    )


def compact_money(
    value: object,
) -> str:

    try:
        number = int(value)

    except Exception:
        return money(value)

    if number >= 1_000_000_000:
        value_b = (
            number
            / 1_000_000_000
        )

        shown = (
            f'{value_b:.1f}'
            .rstrip('0')
            .rstrip('.')
        )

        return (
            f'{shown} میلیارد'
        )

    if number >= 1_000_000:
        value_m = (
            number
            / 1_000_000
        )

        shown = (
            f'{value_m:.0f}'
            if float(
                value_m
            ).is_integer()
            else (
                f'{value_m:.1f}'
                .rstrip('0')
                .rstrip('.')
            )
        )

        return (
            f'{shown} میلیون'
        )

    return f'{number:,}'


def extract_year_range(
    *values: object,
) -> str:

    text = ' '.join(
        fa_to_en(value)
        for value
        in values
        if str(
            value
            or ''
        ).strip()
    )

    years: list[str] = []

    for token in re.findall(
        r'(?<!\d)'
        r'(?:13\d{2}|14\d{2}|19\d{2}|20\d{2})'
        r'(?!\d)',
        text,
    ):
        if token not in years:
            years.append(token)

    if len(years) >= 2:
        return (
            f'{years[0]} '
            f'تا {years[1]}'
        )

    if len(years) == 1:
        return years[0]

    return 'ذکر نشده'


def infer_brand(
    text: object,
) -> str:

    raw = sanitize_text(
        text,
        max_chars=120,
    )

    if not raw:
        return ''

    known = [
        'Mercedes-Benz',
        'Mercedes Benz',
        'BMW',
        'Porsche',
        'Toyota',
        'Lexus',
        'Hyundai',
        'Kia',
        'Peugeot',
        'Renault',
        'Saipa',
        'Iran Khodro',
        'Chery',
        'KMC',
        'Lamari',
        'Mazda',
        'Nissan',
        'Mitsubishi',
        'Honda',
        'مرسدس بنز',
        'بی ام و',
        'پورشه',
        'تویوتا',
        'لکسوس',
        'هیوندای',
        'کیا',
        'پژو',
        'رنو',
        'سایپا',
        'ایران خودرو',
        'چری',
        'کی ام سی',
        'لاماری',
        'مزدا',
        'نیسان',
        'میتسوبیشی',
        'هوندا',
    ]

    low = raw.casefold()

    for item in known:
        if item.casefold() in low:
            return item

    return (
        raw.split()[0]
        if raw.split()
        else ''
    )


def _fernet() -> Fernet:
    if not DATA_SECRET:
        raise RuntimeError(
            'SELECT_CARR_DATA_SECRET is missing'
        )

    key = base64.urlsafe_b64encode(
        hashlib.sha256(
            (
                'select-carr-bank-v1:'
                + DATA_SECRET
            ).encode('utf-8')
        ).digest()
    )

    return Fernet(key)


def _decrypt_bank(
    target: Path,
) -> None:

    if not ENC_BANK.is_file():
        raise RuntimeError(
            'Encrypted Select Carr bank not found'
        )

    try:
        target.write_bytes(
            _fernet().decrypt(
                ENC_BANK.read_bytes()
            )
        )

    except InvalidToken as exc:
        raise RuntimeError(
            'Select Carr bank decrypt failed'
        ) from exc


def _with_bank_rows(
    sql: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:

    with tempfile.TemporaryDirectory() as td:
        db_path = (
            Path(td)
            / 'select_carr.sqlite3'
        )

        _decrypt_bank(
            db_path
        )

        con = sqlite3.connect(
            db_path
        )

        con.row_factory = (
            sqlite3.Row
        )

        try:
            tables = {
                row[0]
                for row
                in con.execute(
                    "SELECT name "
                    "FROM sqlite_master "
                    "WHERE type='table'"
                )
            }

            if (
                'sc_intake_submissions'
                not in tables
            ):
                return []

            return [
                dict(row)
                for row
                in con.execute(
                    sql,
                    params,
                ).fetchall()
            ]

        finally:
            con.close()


BUY_PAYMENT_TYPES = {
    'نقدی',
    'اقساطی',
    'cash',
    'installment',
}


BUY_TEST_MARKERS = (
    'test',
    'تست',
    'آزمایشی',
    'آزمایش',
)


BUY_SELL_MARKERS = (
    'sell',
    'seller',
    'sale',
    'فروش',
    'فروشنده',
)


def _request_marker_text(
    row: dict[str, Any],
) -> str:

    return normalize_space(
        ' '.join(
            str(
                row.get(key)
                or ''
            )
            for key
            in (
                'source',
                'campaign_code',
                'content_code',
                'purpose',
                'description',
                'model',
            )
        )
    ).casefold()


def public_buy_request_rejection_reason(
    row: dict[str, Any],
) -> str | None:
    if str(row.get('request_type') or '') != 'buy':
        return 'not_buy'
    if str(row.get('status') or '') != 'active':
        return 'not_active'
    if not sanitize_text(row.get('model'), max_chars=120):
        return 'missing_model'
    if not sanitize_text(row.get('phone'), max_chars=30):
        return 'missing_phone'
    try:
        budget = int(row.get('budget') or 0)
    except Exception:
        budget = 0
    if budget <= 0:
        return 'invalid_budget'
    try:
        asking_price = int(row.get('asking_price') or 0)
    except Exception:
        asking_price = 0
    if asking_price > 0:
        return 'has_asking_price'
    payment = sanitize_text(row.get('payment_type'), max_chars=40).casefold()
    if payment not in {value.casefold() for value in BUY_PAYMENT_TYPES}:
        return 'invalid_payment_type'
    if not sanitize_text(row.get('promoted_request_id'), max_chars=120):
        return 'missing_promoted_request_id'
    marker_text = _request_marker_text(row)
    if any(marker in marker_text for marker in BUY_TEST_MARKERS):
        return 'test_request'
    if any(marker in marker_text for marker in BUY_SELL_MARKERS):
        return 'seller_as_buyer'
    expires = parse_iso(row.get('expires_at'))
    if expires is not None and expires <= utc_now():
        return 'expired'
    return None


def valid_public_buy_request(
    row: dict[str, Any],
) -> bool:
    return public_buy_request_rejection_reason(row) is None


def latest_buy_request() -> dict[
    str,
    Any,
] | None:

    rows = _with_bank_rows(
        """
        SELECT *
        FROM sc_intake_submissions
        WHERE request_type='buy'
          AND status='active'
          AND model<>''
          AND (
                expires_at IS NULL
                OR julianday(expires_at) > julianday(?)
              )
        ORDER BY
            created_at DESC,
            submission_id DESC
        """,
        (
            iso_now(),
        ),
    )

    for row in rows:
        if valid_public_buy_request(
            row
        ):
            return row

    return None


def request_condition_text(
    row: dict[str, Any],
) -> str:

    parts = [
        sanitize_text(
            row.get(
                'vehicle_condition'
            ),
            max_chars=22,
        ),
        sanitize_text(
            row.get(
                'body_condition'
            ),
            max_chars=22,
        ),
        sanitize_text(
            row.get(
                'mechanical_status'
            ),
            max_chars=22,
        ),
    ]

    parts = [
        part
        for part
        in parts
        if part
    ]

    return (
        ' / '.join(
            dict.fromkeys(
                parts
            )
        )
        if parts
        else 'قابل قبول'
    )


def request_priority_text(
    row: dict[str, Any],
) -> str:

    return (
        sanitize_text(
            row.get('urgency'),
            max_chars=24,
        )
        or sanitize_text(
            row.get('purpose'),
            max_chars=24,
        )
        or 'قیمت مناسب'
    )


def render_buy_request(
    request_row: dict[str, Any],
    output_name: str = 'story_buy_request.jpeg',
) -> Path:
    img = template('buy_request')
    draw = ImageDraw.Draw(img)

    title = sanitize_text(request_row.get('model') or 'خودرو', max_chars=42)
    brand = infer_brand(title)
    budget_value = request_row.get('budget')
    budget = f'قدرت خرید تا {compact_money(budget_value)}' if budget_value else 'درخواست خرید فعال'
    year_pref = extract_year_range(request_row.get('model'), request_row.get('description'))
    mileage_value = request_row.get('mileage')
    mileage_pref = f'تا {money(mileage_value)} کیلومتر' if mileage_value not in (None, '') else 'ذکر نشده'
    condition_pref = request_condition_text(request_row)
    priority_pref = request_priority_text(request_row)

    # Locked header/car/CTA stay untouched.
    budget_box = (218, 870, 651, 945)
    fill_box(draw, budget_box, '#0B0B0B')
    draw_center(draw, budget_box, budget, color='#FFD56A', maximum=30, minimum=16, bold=True, spacing=1)

    # Replace sample Mercedes title area with real request data.
    paint_brand_badge(img, (120, 948, 205, 1016), brand)
    title_area = (240, 949, 600, 1014)
    erase_region_horizontal(img, title_area)
    draw = ImageDraw.Draw(img)
    draw_center(draw, (255, 950, 590, 984), title, color='#0B0B0B', maximum=25, minimum=12, bold=True)
    draw_center(draw, (310, 983, 545, 1012), year_pref, color='#0B0B0B', maximum=18, minimum=10, bold=True)

    fields = [
        ((95, 1050, 216, 1104), year_pref),
        ((255, 1050, 421, 1104), mileage_pref),
        ((465, 1050, 625, 1104), condition_pref),
        ((660, 1050, 820, 1104), priority_pref),
    ]
    for box, text in fields:
        fill_box(draw, box, '#0B0B0B')
        draw_center(draw, box, text, color='#F4EFE6', maximum=15, minimum=8, bold=True, spacing=1)

    path = output_path(output_name)
    img.save(path, 'JPEG', quality=96, subsampling=0)
    return path


def _deal_effective_at(
    row: dict[str, Any],
) -> str:

    return str(
        row.get('sent_at')
        or row.get('queued_at')
        or row.get(
            'candidate_seen_at'
        )
        or ''
    )


def load_publish_state() -> dict[
    str,
    Any,
]:

    default = {
        'version': 2,
        'published': {},
        'story_cursors': {},
        'story_last_published_at':
            None,
        'story_queue_initialized':
            False,
    }

    if not PUBLISH_STATE_PATH.is_file():
        return default

    try:
        data = json.loads(
            PUBLISH_STATE_PATH
            .read_text(
                encoding='utf-8'
            )
        )

    except Exception:
        return default

    if not isinstance(
        data,
        dict,
    ):
        return default

    result = dict(default)
    result.update(data)

    if not isinstance(
        result.get('published'),
        dict,
    ):
        result['published'] = {}

    if not isinstance(
        result.get('story_cursors'),
        dict,
    ):
        result[
            'story_cursors'
        ] = {}

    return result


def save_publish_state(
    state: dict[str, Any],
) -> None:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    published = (
        state.get('published')
        if isinstance(
            state.get('published'),
            dict,
        )
        else {}
    )

    if len(published) > MAX_PUBLISH_STATE_ENTRIES:
        published = dict(
            sorted(
                published.items(),
                key=lambda item:
                    str(
                        (
                            item[1]
                            or {}
                        ).get(
                            'published_at',
                            '',
                        )
                    ),
                reverse=True,
            )[
                :MAX_PUBLISH_STATE_ENTRIES
            ]
        )

    payload = dict(state)

    payload['version'] = 2
    payload['published'] = published

    payload['story_cursors'] = (
        payload.get('story_cursors')
        if isinstance(
            payload.get(
                'story_cursors'
            ),
            dict,
        )
        else {}
    )

    PUBLISH_STATE_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )


def _story_entity_id(
    kind: str,
    row: dict[str, Any],
) -> str:

    if kind == 'opportunity':
        return str(
            row.get('event_key')
            or row.get('source_key')
            or ''
        )

    if kind == 'buy_request':
        return str(
            row.get('submission_id')
            or ''
        )

    return ''


def _story_content_key(
    kind: str,
    row: dict[str, Any],
) -> str:

    entity_id = _story_entity_id(
        kind,
        row,
    )

    if kind == 'opportunity':
        return (
            f'opportunity:'
            f'{RENDER_REVISION}:'
            f'{entity_id}'
        )

    if kind == 'buy_request':
        return (
            f'buy_request:'
            f'{BUY_REQUEST_RENDER_REVISION}:'
            f'{entity_id}'
        )

    raise ValueError(
        f'Unsupported story kind: {kind}'
    )


def _story_entity_published_for_channel(
    state: dict[str, Any],
    channel: str,
    kind: str,
    entity_id: str,
) -> bool:

    if not entity_id:
        return False

    published = (
        state.get('published')
        or {}
    )

    prefix = (
        'opportunity:'
        if kind == 'opportunity'
        else 'buy_request:'
    )

    suffix = ':' + entity_id

    for item in published.values():
        if not isinstance(
            item,
            dict,
        ):
            continue

        if str(
            item.get('channel')
            or ''
        ) != channel:
            continue

        content_key = str(
            item.get('content_key')
            or ''
        )

        if (
            content_key.startswith(prefix)
            and content_key.endswith(suffix)
        ):
            return True

    return False


def _story_entity_fully_published(
    state: dict[str, Any],
    kind: str,
    row: dict[str, Any],
) -> bool:

    entity_id = _story_entity_id(
        kind,
        row,
    )

    if not entity_id:
        return False

    return all(
        _story_entity_published_for_channel(
            state,
            channel,
            kind,
            entity_id,
        )
        for channel in TARGET_CHANNELS
    )


def _safe_story_start_cursor() -> dict[
    str,
    str,
]:

    cutoff = (
        utc_now()
        - timedelta(
            hours=max(
                1,
                STORY_BACKFILL_MAX_AGE_HOURS,
            )
        )
    )

    return {
        'at': cutoff.isoformat(),
        'key': '',
    }


def initialize_story_cursors(
    state: dict[str, Any],
) -> bool:

    current_policy = int(
        state.get(
            'story_queue_policy_version'
        )
        or 0
    )

    if (
        state.get(
            'story_queue_initialized'
        )
        and current_policy
        >= STORY_QUEUE_POLICY_VERSION
    ):
        return False

    safe_cursor = (
        _safe_story_start_cursor()
    )

    state[
        'story_cursors'
    ] = {
        'opportunity':
            dict(safe_cursor),

        'buy_request':
            dict(safe_cursor),
    }

    state[
        'story_queue_initialized'
    ] = True

    state[
        'story_queue_policy_version'
    ] = STORY_QUEUE_POLICY_VERSION

    state[
        'story_queue_initialized_at'
    ] = iso_now()

    save_publish_state(
        state
    )

    return True


def _next_opportunity_after(
    cursor: dict[str, str],
    state: dict[str, Any],
) -> dict[str, Any] | None:

    if not MARKET_DB.is_file():
        return None

    at = str(
        cursor.get('at')
        or '1970-01-01T00:00:00+00:00'
    )

    key = str(
        cursor.get('key')
        or ''
    )

    cutoff = (
        utc_now()
        - timedelta(
            hours=max(
                1,
                STORY_BACKFILL_MAX_AGE_HOURS,
            )
        )
    ).isoformat()

    con = sqlite3.connect(
        MARKET_DB
    )

    con.row_factory = (
        sqlite3.Row
    )

    try:
        rows = con.execute(
            """
            SELECT *,
                COALESCE(
                    sent_at,
                    queued_at,
                    candidate_seen_at
                ) AS effective_at
            FROM market_deals
            WHERE status IN (
                'sent',
                'suppressed_system_b'
            )
              AND julianday(
                    COALESCE(
                        sent_at,
                        queued_at,
                        candidate_seen_at
                    )
                  ) >= julianday(?)
              AND (
                    julianday(
                        COALESCE(
                            sent_at,
                            queued_at,
                            candidate_seen_at
                        )
                    ) > julianday(?)
                    OR (
                        julianday(
                            COALESCE(
                                sent_at,
                                queued_at,
                                candidate_seen_at
                            )
                        ) = julianday(?)
                        AND event_key > ?
                    )
                  )
            ORDER BY
                julianday(
                    COALESCE(
                        sent_at,
                        queued_at,
                        candidate_seen_at
                    )
                ) ASC,
                event_key ASC
            LIMIT 500
            """,
            (
                cutoff,
                at,
                at,
                key,
            ),
        ).fetchall()

        for row in rows:
            item = dict(row)

            if not _story_entity_fully_published(
                state,
                'opportunity',
                item,
            ):
                return item

        return None

    finally:
        con.close()


def _next_buy_after(
    cursor: dict[str, str],
    state: dict[str, Any],
) -> dict[str, Any] | None:

    at = str(
        cursor.get('at')
        or '1970-01-01T00:00:00+00:00'
    )

    key = str(
        cursor.get('key')
        or ''
    )

    cutoff = (
        utc_now()
        - timedelta(
            hours=max(
                1,
                STORY_BACKFILL_MAX_AGE_HOURS,
            )
        )
    ).isoformat()

    rows = _with_bank_rows(
        """
        SELECT *
        FROM sc_intake_submissions
        WHERE request_type='buy'
          AND status='active'
          AND model<>''
          AND julianday(created_at) >= julianday(?)
          AND (
                expires_at IS NULL
                OR julianday(expires_at) > julianday(?)
              )
          AND (
                julianday(created_at) > julianday(?)
                OR (
                    julianday(created_at) = julianday(?)
                    AND submission_id > ?
                )
              )
        ORDER BY
            julianday(created_at) ASC,
            submission_id ASC
        """,
        (
            cutoff,
            iso_now(),
            at,
            at,
            key,
        ),
    )

    for row in rows:
        if not valid_public_buy_request(
            row
        ):
            continue

        if _story_entity_fully_published(
            state,
            'buy_request',
            row,
        ):
            continue

        return row

    return None


def story_time_allowed(
    now: datetime | None = None,
) -> bool:

    local = (
        now
        or datetime.now(
            TEHRAN
        )
    ).astimezone(
        TEHRAN
    )

    minutes = (
        local.hour * 60
        + local.minute
    )

    return (
        11 * 60
        <= minutes
        < 15 * 60
    ) or (
        19 * 60
        <= minutes
        < 23 * 60
    )


def story_gap_allowed(
    state: dict[str, Any],
) -> bool:

    last = parse_iso(
        state.get(
            'story_last_published_at'
        )
    )

    if last is None:
        return True

    return (
        utc_now()
        - last
        >= timedelta(
            minutes=
                STORY_MIN_GAP_MINUTES
        )
    )


def _recent_opportunity_candidates(
    state: dict[str, Any],
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not MARKET_DB.is_file():
        return []
    cutoff = (utc_now() - timedelta(hours=max(1, STORY_BACKFILL_MAX_AGE_HOURS))).isoformat()
    con = sqlite3.connect(MARKET_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT *, COALESCE(sent_at, queued_at, candidate_seen_at) AS effective_at
            FROM market_deals
            WHERE status IN ('sent','suppressed_system_b')
              AND julianday(COALESCE(sent_at, queued_at, candidate_seen_at)) >= julianday(?)
            ORDER BY julianday(COALESCE(sent_at, queued_at, candidate_seen_at)) ASC, event_key ASC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if not _story_entity_fully_published(state, 'opportunity', item):
                result.append(item)
        return result
    finally:
        con.close()


def _recent_buy_candidates(
    state: dict[str, Any],
    limit: int = 100,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cutoff = (utc_now() - timedelta(hours=max(1, STORY_BACKFILL_MAX_AGE_HOURS))).isoformat()
    rows = _with_bank_rows(
        """
        SELECT *
        FROM sc_intake_submissions
        WHERE request_type='buy'
          AND julianday(created_at) >= julianday(?)
        ORDER BY julianday(created_at) ASC, submission_id ASC
        LIMIT ?
        """,
        (cutoff, limit),
    )
    valid_rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    for row in rows:
        reason = public_buy_request_rejection_reason(row)
        if reason:
            reasons[reason] += 1
            continue
        if _story_entity_fully_published(state, 'buy_request', row):
            reasons['already_published'] += 1
            continue
        valid_rows.append(row)
    return valid_rows, {
        'buyer_seen': len(rows),
        'buyer_valid': len(valid_rows),
        'buyer_rejected': sum(reasons.values()),
        'buyer_rejection_reasons': dict(reasons),
    }


def _build_story_queue(
    result: dict[str, Any],
) -> None:
    state = load_publish_state()
    initialize_story_cursors(state)

    if not story_time_allowed():
        result['story_queue_status'] = 'outside_allowed_window'
        return

    opportunities = _recent_opportunity_candidates(state)
    buys, buyer_diag = _recent_buy_candidates(state)
    result.update(buyer_diag)
    result['opportunity_pending'] = len(opportunities)

    candidates: list[tuple[datetime, str, dict[str, Any]]] = []
    for row in opportunities:
        dt = parse_iso(row.get('effective_at'))
        if dt:
            candidates.append((dt, 'opportunity', row))
    for row in buys:
        dt = parse_iso(row.get('created_at'))
        if dt:
            candidates.append((dt, 'buy_request', row))

    candidates.sort(key=lambda item: (item[0], item[1]))
    selected = candidates[:max(1, STORY_BATCH_MAX)]

    if not selected:
        result['story_queue_status'] = 'no_new_story'
        return

    for index, (_dt, kind, row) in enumerate(selected, start=1):
        entity_id = _story_entity_id(kind, row)
        safe = hashlib.sha1(entity_id.encode('utf-8')).hexdigest()[:10]
        if kind == 'opportunity':
            path = render_opportunity(row, f'story_{index:02d}_opportunity_{safe}.jpeg')
        else:
            path = render_buy_request(row, f'story_{index:02d}_buy_request_{safe}.jpeg')

        result['stories'].append({
            'kind': kind,
            'entity_id': entity_id,
            'key': _story_content_key(kind, row),
            'path': str(path),
        })

    result['story_queue_status'] = 'story_batch_ready'
    result['story_batch_size'] = len(result['stories'])


def post_target_minutes(
    kind: str,
    weekday: int,
) -> int:

    market = {
        5: 13 * 60 + 30,
        6: 12 * 60,
        0: 14 * 60 + 30,
        1: 13 * 60 + 30,
        2: 13 * 60,
        3: 12 * 60,
        4: 11 * 60 + 30,
    }

    cars = {
        5: 21 * 60 + 30,
        6: 14 * 60 + 30,
        0: 22 * 60 + 30,
        1: 20 * 60 + 30,
        2: 22 * 60,
        3: 14 * 60,
        4: 14 * 60,
    }

    return (
        market
        if kind == 'market'
        else cars
    )[
        weekday
    ]


def previous_day_fallback_allowed(
    kind: str,
    now: datetime | None = None,
) -> bool:

    if kind != 'cars':
        return False

    local = (
        now
        or datetime.now(
            TEHRAN
        )
    ).astimezone(
        TEHRAN
    )

    if local.weekday() not in {
        3,
        4,
    }:
        return False

    current_minutes = (
        local.hour * 60
        + local.minute
    )

    return (
        current_minutes
        >= post_target_minutes(
            kind,
            local.weekday(),
        )
        + 120
    )


def latest_market_prices(
    posts: list[TelegramPost] | None = None,
    *,
    allow_previous_day: bool = False,
) -> dict[str, Any] | None:
    posts = posts if posts is not None else telegram_posts()
    today = _today_tehran()

    def parsed(post: TelegramPost) -> dict[str, Any] | None:
        result: dict[str, Any] = {
            'usd': _market_rate_display(post.text, 'usd'),
            'gold18': _market_rate_display(post.text, 'gold18'),
            'coin': _market_rate_display(post.text, 'coin'),
            'ounce': _market_rate_display(post.text, 'ounce'),
        }
        if not all(result.get(key) for key in ('usd','gold18','coin','ounce')):
            return None
        result['source_post_id'] = post.post_id
        result['source_published_at'] = post.published_at.isoformat() if post.published_at else None
        result['source_tehran_date'] = str(_tehran_date(post)) if _tehran_date(post) else None
        result['target_tehran_date'] = str(today)
        return result

    for post in reversed(posts):
        if _tehran_date(post) == today and _is_market_rates(post):
            item = parsed(post)
            if item:
                return item

    cutoff = utc_now() - timedelta(hours=max(1, MARKET_POST_MAX_AGE_HOURS))
    for post in reversed(posts):
        if not post.published_at or post.published_at < cutoff or post.published_at > utc_now():
            continue
        if not _is_market_rates(post):
            continue
        item = parsed(post)
        if item:
            item['fallback_previous_day'] = _tehran_date(post) != today
            return item
    return None


def latest_zero_car_prices(
    posts: list[TelegramPost] | None = None,
    *,
    allow_previous_day: bool = False,
) -> dict[str, Any] | None:
    posts = posts if posts is not None else telegram_posts()
    today = _today_tehran()

    def parsed(post: TelegramPost) -> dict[str, Any] | None:
        items = extract_zero_car_items(post.text)[:MAX_ZERO_CARS]
        if len(items) < 8:
            return None
        return {
            'date_label': extract_date_label(post.text),
            'source_post_id': post.post_id,
            'source_published_at': post.published_at.isoformat() if post.published_at else None,
            'source_tehran_date': str(_tehran_date(post)) if _tehran_date(post) else None,
            'target_tehran_date': str(today),
            'items': items,
        }

    for post in reversed(posts):
        if _tehran_date(post) == today:
            item = parsed(post)
            if item and _is_zero_car_prices(post):
                return item

    if allow_previous_day:
        for post in reversed(posts):
            post_date = _tehran_date(post)
            if post_date is not None and post_date < today:
                item = parsed(post)
                if item and _is_zero_car_prices(post):
                    item['fallback_previous_day'] = True
                    return item
    return None


def zero_car_diagnostics(posts: list[TelegramPost]) -> dict[str, Any]:
    candidates = []
    for post in posts:
        items = extract_zero_car_items(post.text)
        if items:
            candidates.append({
                'post_id': post.post_id,
                'published_at': post.published_at.isoformat() if post.published_at else None,
                'tehran_date': str(_tehran_date(post)) if _tehran_date(post) else None,
                'rows': len(items),
            })
    candidates.sort(key=lambda x: (x.get('published_at') or ''), reverse=True)
    return {
        'car_candidate_posts': len(candidates),
        'car_candidates_recent': candidates[:8],
        'car_max_rows_detected': max((x['rows'] for x in candidates), default=0),
    }


def _fully_published_key(
    state: dict[str, Any],
    key: str,
) -> bool:

    return all(
        is_published(
            state,
            channel,
            key,
        )
        for channel in TARGET_CHANNELS
    )


def scheduled_due_modes(
    now: datetime | None = None,
) -> dict[str, Any]:

    local = (
        now
        or datetime.now(
            TEHRAN
        )
    ).astimezone(
        TEHRAN
    )

    state = load_publish_state()

    current_minutes = (
        local.hour * 60
        + local.minute
    )

    today = str(
        local.date()
    )

    due: list[str] = []

    if story_time_allowed(local):
        due.append('story_queue')

    market_key = (
        'market:'
        + today
    )

    if (
        current_minutes
        >= post_target_minutes(
            'market',
            local.weekday(),
        )
        and not _fully_published_key(
            state,
            market_key,
        )
    ):
        due.append(
            'market'
        )

    cars_key = (
        'cars:'
        + today
    )

    if (
        current_minutes
        >= post_target_minutes(
            'cars',
            local.weekday(),
        )
        and not _fully_published_key(
            state,
            cars_key,
        )
    ):
        due.append(
            'cars'
        )

    return {
        'status': 'ok',
        'tehran_time':
            local.isoformat(),
        'tehran_date':
            today,
        'weekday':
            local.weekday(),
        'due_modes':
            due,
    }


def print_scheduled_plan() -> int:

    print(
        json.dumps(
            scheduled_due_modes(),
            ensure_ascii=False,
        )
    )

    return 0


def build_content(
    mode: str = 'all',
) -> dict[str, Any]:

    validate_templates(mode)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for old_file in OUTPUT_DIR.glob(
        '*.jpeg'
    ):
        old_file.unlink()

    manifest_path = (
        OUTPUT_DIR
        / 'manifest.json'
    )

    if manifest_path.exists():
        manifest_path.unlink()

    result: dict[
        str,
        Any,
    ] = {
        'version':
            STAGE4_VERSION,

        'render_revision':
            RENDER_REVISION,

        'generated_at':
            iso_now(),

        'mode':
            mode,

        'stories':
            [],

        'market_post':
            None,

        'car_price_key':
            None,

        'car_price_slides':
            [],

        'buy_request_story':
            {
                'enabled': True,
                'source':
                    'sc_intake_submissions',
            },
    }

    if mode == 'story_queue':
        _build_story_queue(
            result
        )

    elif mode in {
        'all',
        'opportunity',
    }:
        deal = (
            latest_market_deal()
        )

        if deal:
            result[
                'stories'
            ].append(
                {
                    'kind':
                        'opportunity',

                    'entity_id':
                        str(
                            deal.get(
                                'event_key'
                            )
                            or deal.get(
                                'source_key'
                            )
                            or ''
                        ),

                    'key':
                        (
                            f'opportunity:'
                            f'{RENDER_REVISION}:'
                            + str(
                                deal.get(
                                    'event_key'
                                )
                                or deal.get(
                                    'source_key'
                                )
                                or ''
                            )
                        ),

                    'path':
                        str(
                            render_opportunity(
                                deal
                            )
                        ),

                    'sample_count':
                        int(
                            deal.get(
                                'sample_count'
                            )
                            or 0
                        ),

                    'comparison_caption':
                        comparison_caption(
                            deal
                        ),
                }
            )

        if mode == 'all':
            buy = (
                latest_buy_request()
            )

            if buy:
                result[
                    'stories'
                ].append(
                    {
                        'kind':
                            'buy_request',

                        'entity_id':
                            str(
                                buy.get(
                                    'submission_id'
                                )
                                or ''
                            ),

                        'key':
                            (
                                f'buy_request:'
                                f'{BUY_REQUEST_RENDER_REVISION}:'
                                + str(
                                    buy.get(
                                        'submission_id'
                                    )
                                    or ''
                                )
                            ),

                        'path':
                            str(
                                render_buy_request(
                                    buy
                                )
                            ),
                    }
                )

    posts: list[
        TelegramPost
    ] | None = None

    if mode in {
        'all',
        'market',
        'cars',
    }:
        posts = (
            telegram_posts()
        )

        result[
            'source_posts_scanned'
        ] = len(posts)

        if mode in {'all', 'cars'}:
            result.update(zero_car_diagnostics(posts))

    if mode in {
        'all',
        'market',
    }:
        market = latest_market_prices(
            posts,
            allow_previous_day=False,
        )

        if market:
            target_date = str(
                market.get(
                    'target_tehran_date'
                )
                or _today_tehran()
            )

            result[
                'market_post'
            ] = {
                'key':
                    (
                        'market:'
                        + target_date
                    ),

                'path':
                    str(
                        render_market_post(
                            market
                        )
                    ),

                'source_post_id':
                    market.get(
                        'source_post_id'
                    ),

                'source_published_at':
                    market.get(
                        'source_published_at'
                    ),

                'source_tehran_date':
                    market.get(
                        'source_tehran_date'
                    ),

                'fallback_previous_day':
                    bool(
                        market.get(
                            'fallback_previous_day'
                        )
                    ),
            }

    if mode in {
        'all',
        'cars',
    }:
        cars = latest_zero_car_prices(
            posts,
            allow_previous_day=(
                previous_day_fallback_allowed(
                    'cars'
                )
            ),
        )

        if cars:
            target_date = str(
                cars.get(
                    'target_tehran_date'
                )
                or _today_tehran()
            )

            car_key = (
                'cars:'
                + target_date
            )

            car_paths = (
                render_car_prices(
                    cars
                )
            )

            result[
                'car_price_key'
            ] = car_key

            result[
                'car_price_slides'
            ] = [
                {
                    'key':
                        f'{car_key}:{index:02d}',

                    'path':
                        str(path),
                }
                for index, path
                in enumerate(
                    car_paths,
                    start=1,
                )
            ]

            result[
                'car_source_post_id'
            ] = cars.get(
                'source_post_id'
            )

            result[
                'car_source_published_at'
            ] = cars.get(
                'source_published_at'
            )

            result[
                'car_source_tehran_date'
            ] = cars.get(
                'source_tehran_date'
            )

            result[
                'car_fallback_previous_day'
            ] = bool(
                cars.get(
                    'fallback_previous_day'
                )
            )

    manifest_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    print(
        json.dumps(
            {
                'status':
                    'built',

                'stage4_version':
                    STAGE4_VERSION,

                'mode':
                    mode,

                'stories':
                    len(
                        result['stories']
                    ),

                'story_kinds':
                    [
                        item['kind']
                        for item
                        in result['stories']
                    ],

                'market_post':
                    bool(
                        result[
                            'market_post'
                        ]
                    ),

                'car_price_slides':
                    len(
                        result[
                            'car_price_slides'
                        ]
                    ),

                'buy_request_story_enabled':
                    True,

                'source_posts_scanned':
                    result.get(
                        'source_posts_scanned',
                        0,
                    ),

                'car_candidate_posts':
                    result.get('car_candidate_posts', 0),

                'car_max_rows_detected':
                    result.get('car_max_rows_detected', 0),

                'buyer_seen':
                    result.get('buyer_seen', 0),

                'buyer_valid':
                    result.get('buyer_valid', 0),

                'buyer_rejected':
                    result.get('buyer_rejected', 0),

                'buyer_rejection_reasons':
                    result.get('buyer_rejection_reasons', {}),

                'story_queue_status':
                    result.get(
                        'story_queue_status'
                    ),

                'manifest':
                    str(
                        manifest_path
                    ),
            },
            ensure_ascii=False,
        )
    )

    return result


def _notification_text(
    content_type: str,
    content_key: str,
    channel_results: dict[str, str],
) -> str:
    local_time = datetime.now(TEHRAN).strftime('%Y-%m-%d %H:%M:%S')
    lines = [
        '✅ گزارش انتشار Select Carr',
        f'نوع: {content_type}',
        f'زمان تهران: {local_time}',
        f'شناسه: {content_key}',
    ]
    for channel in TARGET_CHANNELS:
        lines.append(f'@{channel}: {channel_results.get(channel, "نامشخص")}')
    return '\n'.join(lines)


def publish_built(
    mode: str,
) -> int:
    manifest_path = OUTPUT_DIR / 'manifest.json'
    if not manifest_path.is_file():
        raise RuntimeError('manifest.json missing')

    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    channels = buffer_channels()
    state = load_publish_state()
    for name in TARGET_CHANNELS:
        if name not in channels:
            raise RuntimeError(f'Missing Buffer channel: {name}')

    published_now = 0
    skipped_duplicates = 0
    skipped_no_content = 0
    failures: list[str] = []

    def publish_one_image(key: str, url: str, post_type: str, text: str = '') -> dict[str, str]:
        nonlocal published_now, skipped_duplicates
        results: dict[str, str] = {}
        for channel_name in TARGET_CHANNELS:
            if is_published(state, channel_name, key):
                skipped_duplicates += 1
                results[channel_name] = 'قبلاً منتشر شده'
                continue
            try:
                post_id = publish_image(
                    str(channels[channel_name]['id']),
                    url,
                    post_type=post_type,
                    text=text,
                )
                mark_published(state, channel_name, key, post_id)
                published_now += 1
                results[channel_name] = 'موفق ✅'
            except Exception as exc:
                results[channel_name] = 'ناموفق ❌'
                failures.append(f'{channel_name}|{key}|{exc}')
        return results

    if mode in {'opportunity', 'story_queue'}:
        if not story_time_allowed():
            print(json.dumps({
                'status': 'story_window_closed',
                'tehran_time': datetime.now(TEHRAN).isoformat(),
            }, ensure_ascii=False))
            return 0

        stories = manifest.get('stories') or []
        if mode == 'opportunity':
            stories = [x for x in stories if x.get('kind') == 'opportunity'][:1]
        if not stories:
            skipped_no_content += 1

        for idx, story in enumerate(stories):
            key = str(story['key'])
            kind = str(story.get('kind') or '')
            entity_id = str(story.get('entity_id') or key.rsplit(':', 1)[-1])
            url = github_raw_url(Path(story['path']).name)
            results: dict[str, str] = {}
            before = published_now

            for channel_name in TARGET_CHANNELS:
                if _story_entity_published_for_channel(state, channel_name, kind, entity_id):
                    skipped_duplicates += 1
                    results[channel_name] = 'قبلاً منتشر شده'
                    continue
                try:
                    post_id = publish_image(
                        str(channels[channel_name]['id']),
                        url,
                        post_type='story',
                    )
                    mark_published(state, channel_name, key, post_id)
                    published_now += 1
                    results[channel_name] = 'موفق ✅'
                except Exception as exc:
                    results[channel_name] = 'ناموفق ❌'
                    failures.append(f'{channel_name}|{key}|{exc}')

            if published_now > before:
                state['story_last_published_at'] = iso_now()
                save_publish_state(state)
                telegram_notify(_notification_text(
                    'استوری فرصت خرید' if kind == 'opportunity' else 'استوری درخواست خرید',
                    key,
                    results,
                ))

            if idx + 1 < len(stories) and STORY_PUBLISH_DELAY_SECONDS > 0:
                time.sleep(STORY_PUBLISH_DELAY_SECONDS)

    elif mode == 'market':
        market = manifest.get('market_post')
        if not market:
            skipped_no_content += 1
        else:
            key = str(market['key'])
            before = published_now
            results = publish_one_image(
                key,
                github_raw_url(Path(market['path']).name),
                'post',
                POST_CAPTION,
            )
            if published_now > before:
                telegram_notify(_notification_text('پست طلا و ارز', key, results))

    elif mode == 'cars':
        slides = manifest.get('car_price_slides') or []
        if not slides:
            skipped_no_content += 1
        else:
            key = str(manifest.get('car_price_key') or slides[0]['key'])
            urls = [github_raw_url(Path(slide['path']).name) for slide in slides]
            results: dict[str, str] = {}
            before = published_now
            for channel_name in TARGET_CHANNELS:
                if is_published(state, channel_name, key):
                    skipped_duplicates += 1
                    results[channel_name] = 'قبلاً منتشر شده'
                    continue
                try:
                    if len(urls) == 1:
                        post_id = publish_image(
                            str(channels[channel_name]['id']), urls[0],
                            post_type='post', text=POST_CAPTION,
                        )
                    else:
                        post_id = publish_carousel(
                            str(channels[channel_name]['id']), urls,
                            caption=POST_CAPTION,
                        )
                    mark_published(state, channel_name, key, post_id)
                    published_now += 1
                    results[channel_name] = 'موفق ✅'
                except Exception as exc:
                    results[channel_name] = 'ناموفق ❌'
                    failures.append(f'{channel_name}|{key}|{exc}')
            if published_now > before:
                telegram_notify(_notification_text('پست قیمت خودروهای صفر', key, results))
    else:
        raise ValueError(f'Unsupported publish mode: {mode}')

    print(json.dumps({
        'status': 'publish_complete' if not failures else 'publish_partial_failure',
        'mode': mode,
        'published_now': published_now,
        'skipped_duplicates': skipped_duplicates,
        'skipped_no_content': skipped_no_content,
        'failures': failures,
        'buy_request_story_enabled': True,
    }, ensure_ascii=False))

    if failures:
        raise RuntimeError('Some Stage 4 publications failed; successful channels were saved and only failures will retry.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        'action',
        choices=[
            'check_buffer',
            'validate_templates',
            'build',
            'publish',
            'schedule_plan',
        ],
    )

    parser.add_argument(
        '--mode',
        choices=[
            'all',
            'opportunity',
            'story_queue',
            'market',
            'cars',
        ],
        default='all',
    )

    args = parser.parse_args()

    if (
        args.action
        == 'check_buffer'
    ):
        return check_buffer()

    if (
        args.action
        == 'validate_templates'
    ):
        return validate_templates(args.mode)

    if (
        args.action
        == 'schedule_plan'
    ):
        return print_scheduled_plan()

    if (
        args.action
        == 'build'
    ):
        build_content(
            args.mode
        )

        return 0

    if (
        args.action
        == 'publish'
    ):
        if args.mode == 'all':
            raise RuntimeError(
                'Publishing all content at once '
                'is intentionally disabled.'
            )

        return publish_built(
            args.mode
        )

    return 2


if __name__ == '__main__':
    raise SystemExit(
        main()
    )
