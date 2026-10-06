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

STAGE4_VERSION = '2.8.1'
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


STORY_MIN_GAP_MINUTES = 15


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
        1010,
        1787,
    ),
    'buy_request': (
        916,
        1695,
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
        '18',
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


def font(
    size: int,
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:

    return ImageFont.truetype(
        find_font(bold),
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

    return {
        'raqm': 'true',
        'regular_font': regular,
        'bold_font': bold,
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

    selected = font(
        minimum,
        bold=bold,
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
        raise RuntimeError(
            'BUFFER_API_KEY is missing'
        )

    request = urllib.request.Request(
        BUFFER_API,
        data=json.dumps(
            {
                'query': query,
            }
        ).encode('utf-8'),
        headers={
            'Content-Type':
                'application/json',

            'Authorization':
                f'Bearer {BUFFER_API_KEY}',
        },
        method='POST',
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        payload = json.loads(
            response
            .read()
            .decode('utf-8')
        )

    if payload.get('errors'):
        raise RuntimeError(
            json.dumps(
                payload['errors'],
                ensure_ascii=False,
            )
        )

    return (
        payload.get('data')
        or {}
    )


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


def validate_templates() -> int:
    text_engine = (
        validate_text_engine()
    )

    details = []

    for key, path in TEMPLATES.items():
        if not path.is_file():
            raise RuntimeError(
                f'Template missing: {path}'
            )

        try:
            with Image.open(path) as image:
                size = tuple(
                    image.size
                )

                image_format = (
                    image.format
                )

                image.verify()

        except Exception as exc:
            raise RuntimeError(
                f'Template unreadable: {path}'
            ) from exc

        expected = (
            EXPECTED_TEMPLATE_SIZES[
                key
            ]
        )

        if size != expected:
            raise RuntimeError(
                f'Locked template size mismatch '
                f'for {key}: '
                f'got={size}, '
                f'expected={expected}'
            )

        details.append(
            {
                'template': key,
                'file': str(path),
                'size': list(size),
                'format': image_format,
            }
        )

    print(
        json.dumps(
            {
                'status': 'ok',
                'templates': 4,
                'stage4_version':
                    STAGE4_VERSION,
                'text_engine':
                    text_engine,
                'buy_request_story_enabled':
                    BUY_REQUEST_STORY_ENABLED,
                'details':
                    details,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

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

    request = urllib.request.Request(
        url,
        headers={
            'User-Agent':
                'Mozilla/5.0 (X11; Linux x86_64) '
                'AppleWebKit/537.36 '
                'Chrome/131.0.0.0 Safari/537.36',

            'Accept-Language':
                'fa-IR,fa;q=0.9,en;q=0.6',
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=35,
    ) as response:
        return (
            response
            .read()
            .decode(
                'utf-8',
                errors='ignore',
            )
        )


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

    if (
        '⬅' not in line
        and '←' not in line
    ):
        return None

    parts = re.split(
        r'⬅️?|←',
        line,
        maxsplit=1,
    )

    if len(parts) != 2:
        return None

    left = (
        sanitize_text(
            parts[0],
            max_chars=60,
        )
        .strip(' -:|')
    )

    right = (
        sanitize_text(
            parts[1],
            max_chars=100,
        )
        .strip()
    )

    if (
        not left
        or not right
    ):
        return None

    match = re.search(
        r'(?<!\d)'
        r'(\d{1,2}[.]\d{1,4}|\d{4,13})'
        r'(?!\d)',
        fa_to_en(right),
    )

    if not match:
        return None

    parsed_price = (
        _car_price_values(
            match.group(1)
        )
    )

    if not parsed_price:
        return None

    price, display_price = (
        parsed_price
    )

    if price < 100_000_000:
        return None

    prefix = (
        sanitize_text(
            right[
                :match.start()
            ],
            max_chars=28,
        )
        .strip(' :/|-')
    )

    name = left
    variants: list[str] = []

    paren = re.match(
        r'^(.*?)\(([^)]{1,24})\)(.*)$',
        name,
    )

    if paren:
        base = sanitize_text(
            (
                paren.group(1)
                + ' '
                + paren.group(3)
            ).strip(),
            max_chars=34,
        )

        if base:
            name = base

        variants.append(
            sanitize_text(
                paren.group(2),
                max_chars=18,
            )
        )

    if (
        prefix
        and prefix not in {
            'قیمت',
            'تومان',
        }
    ):
        variants.append(
            prefix
        )

    variant = sanitize_text(
        ' '.join(
            v
            for v
            in variants
            if v
        ),
        max_chars=22,
    )

    return {
        'name':
            sanitize_text(
                name,
                max_chars=34,
            ),

        'variant':
            variant,

        'price':
            price,

        'display_price':
            display_price,
    }


def _is_zero_car_prices(
    post: TelegramPost,
) -> bool:

    normalized = normalize_space(
        post.text
    )

    if (
        'آخرین بروزرسانی قیمت خودروهای پرفروش پلاک ملی'
        not in normalized
    ):
        return False

    return (
        sum(
            1
            for line
            in post.text.splitlines()
            if _zero_car_line(line)
        )
        >= 8
    )


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


def render_opportunity(
    deal: dict[str, Any],
) -> Path:

    img = template(
        'opportunity'
    )

    draw = ImageDraw.Draw(img)

    title = compact_vehicle_title(
        deal
    )

    brand = sanitize_text(
        deal.get('brand'),
        max_chars=40,
    )

    discount = format_discount(
        deal.get('discount_percent')
    )

    year = (
        sanitize_text(
            deal.get('model_year'),
            max_chars=8,
        )
        or '-'
    )

    mileage = (
        'نامشخص'
        if deal.get('mileage')
        is None
        else (
            f"{money(deal.get('mileage'))}"
            '\nکیلومتر'
        )
    )

    technical = technical_label(
        deal
    )

    body = body_label(
        deal.get('body_condition')
    )

    # Correct only the opportunity header label. The rest of the
    # locked header remains untouched.
    header_label_box = (
        52,
        165,
        430,
        245,
    )

    fill_box(
        draw,
        header_label_box,
        '#0B0B0B',
    )

    draw_center(
        draw,
        (
            215,
            168,
            425,
            242,
        ),
        'فرصت',
        color='#FFFFFF',
        maximum=45,
        minimum=28,
        bold=True,
        spacing=1,
    )

    draw_center(
        draw,
        (
            58,
            168,
            220,
            242,
        ),
        'خرید',
        color='#F5B942',
        maximum=45,
        minimum=28,
        bold=True,
        spacing=1,
    )

    fill_box(
        draw,
        (
            678,
            151,
            784,
            211,
        ),
        '#E50914',
    )

    draw_center(
        draw,
        (
            678,
            151,
            784,
            211,
        ),
        discount,
        color='#FFFFFF',
        maximum=33,
        minimum=19,
    )

    paint_brand_badge(
        img,
        (
            68,
            1015,
            166,
            1098,
        ),
        brand,
    )

    title_box = (
        176,
        1028,
        676,
        1088,
    )

    erase_region_vertical(
        img,
        title_box,
    )

    draw = ImageDraw.Draw(img)

    draw_center(
        draw,
        title_box,
        title,
        color='#0B0B0B',
        maximum=33,
        minimum=15,
    )

    fields = [
        (
            (
                132,
                1134,
                245,
                1181,
            ),
            year,
            25,
            14,
        ),
        (
            (
                322,
                1128,
                484,
                1183,
            ),
            mileage,
            21,
            12,
        ),
        (
            (
                546,
                1133,
                706,
                1182,
            ),
            sanitize_text(
                technical,
                max_chars=22,
            ),
            21,
            12,
        ),
        (
            (
                785,
                1133,
                932,
                1182,
            ),
            sanitize_text(
                body,
                max_chars=22,
            ),
            21,
            12,
        ),
    ]

    for (
        box,
        text,
        maximum,
        minimum,
    ) in fields:
        fill_box(
            draw,
            box,
            '#0B0B0B',
        )

        draw_center(
            draw,
            box,
            text,
            color='#F4EFE6',
            maximum=maximum,
            minimum=minimum,
            spacing=1,
        )

    for box, value in [
        (
            (
                155,
                1220,
                414,
                1261,
            ),
            deal.get(
                'candidate_price'
            ),
        ),
        (
            (
                594,
                1220,
                855,
                1261,
            ),
            deal.get(
                'average_price'
            ),
        ),
    ]:
        fill_box(
            draw,
            box,
            '#0B0B0B',
        )

        draw_center(
            draw,
            box,
            money(value),
            color='#FFD56A',
            maximum=29,
            minimum=15,
        )

    comparison_box = (
        98,
        1300,
        912,
        1376,
    )

    fill_box(
        draw,
        comparison_box,
        '#0B0B0B',
    )

    line1, _line2 = (
        comparison_lines(
            deal
        )
    )

    draw_center(
        draw,
        (
            108,
            1304,
            902,
            1372,
        ),
        line1,
        color='#FFFFFF',
        maximum=38,
        minimum=22,
        bold=True,
        spacing=1,
    )

    # Keep the locked source-logo row and place only real source counts
    # in the large-number positions shown in the approved sample.
    breakdown = deal_reference_breakdown(
        deal
    )

    source_slots = {
        'divar': (
            167,
            1421,
            237,
            1476,
        ),
        'hamrah_mechanic': (
            421,
            1421,
            491,
            1476,
        ),
        'hamrahmechanic': (
            421,
            1421,
            491,
            1476,
        ),
        'karnameh': (
            665,
            1421,
            735,
            1476,
        ),
        'telegram': (
            879,
            1421,
            949,
            1476,
        ),
    }

    normalized_counts: dict[
        str,
        int,
    ] = {}

    for source, count in breakdown.items():
        source_key = str(
            source
            or ''
        ).strip().casefold()

        if source_key == 'hamrahmechanic':
            source_key = 'hamrah_mechanic'

        normalized_counts[
            source_key
        ] = (
            normalized_counts.get(
                source_key,
                0,
            )
            + int(count)
        )

    for source_key, box in source_slots.items():
        canonical_key = (
            'hamrah_mechanic'
            if source_key
            == 'hamrahmechanic'
            else source_key
        )

        count = normalized_counts.get(
            canonical_key,
            0,
        )

        if count <= 0:
            continue

        fill_box(
            draw,
            box,
            '#0B0B0B',
        )

        draw_center(
            draw,
            box,
            en_to_fa(count),
            color='#F5B942',
            maximum=42,
            minimum=28,
            bold=True,
            spacing=1,
        )

    path = output_path(
        'story_opportunity.jpeg'
    )

    img.save(
        path,
        'JPEG',
        quality=96,
        subsampling=0,
    )

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


def valid_public_buy_request(
    row: dict[str, Any],
) -> bool:

    if str(
        row.get('request_type')
        or ''
    ) != 'buy':
        return False

    if str(
        row.get('status')
        or ''
    ) != 'active':
        return False

    if not sanitize_text(
        row.get('model'),
        max_chars=120,
    ):
        return False

    if not sanitize_text(
        row.get('phone'),
        max_chars=30,
    ):
        return False

    try:
        budget = int(
            row.get('budget')
            or 0
        )

    except Exception:
        budget = 0

    if budget <= 0:
        return False

    try:
        asking_price = int(
            row.get('asking_price')
            or 0
        )

    except Exception:
        asking_price = 0

    if asking_price > 0:
        return False

    payment = sanitize_text(
        row.get('payment_type'),
        max_chars=40,
    ).casefold()

    if payment not in {
        value.casefold()
        for value
        in BUY_PAYMENT_TYPES
    }:
        return False

    if not sanitize_text(
        row.get(
            'promoted_request_id'
        ),
        max_chars=120,
    ):
        return False

    marker_text = (
        _request_marker_text(row)
    )

    if any(
        marker in marker_text
        for marker
        in BUY_TEST_MARKERS
    ):
        return False

    if any(
        marker in marker_text
        for marker
        in BUY_SELL_MARKERS
    ):
        return False

    expires = parse_iso(
        row.get('expires_at')
    )

    if (
        expires is not None
        and expires <= utc_now()
    ):
        return False

    return True


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
) -> Path:

    img = template(
        'buy_request'
    )

    draw = ImageDraw.Draw(img)

    title = sanitize_text(
        request_row.get('model')
        or 'خودرو',
        max_chars=42,
    )

    brand = infer_brand(
        title
    )

    budget_value = (
        request_row.get('budget')
    )

    budget = (
        f'قدرت خرید تا '
        f'{compact_money(budget_value)}'
        if budget_value
        else 'درخواست خرید فعال'
    )

    year_pref = extract_year_range(
        request_row.get('model'),
        request_row.get('description'),
    )

    mileage_value = (
        request_row.get('mileage')
    )

    mileage_pref = (
        f'تا {money(mileage_value)} کیلومتر'
        if mileage_value not in (
            None,
            '',
        )
        else 'ذکر نشده'
    )

    condition_pref = (
        request_condition_text(
            request_row
        )
    )

    priority_pref = (
        request_priority_text(
            request_row
        )
    )

    budget_box = (
        225,
        982,
        690,
        1055,
    )

    draw.rounded_rectangle(
        budget_box,
        radius=12,
        fill='#0B0B0B',
        outline='#C88A16',
        width=2,
    )

    draw_center(
        draw,
        budget_box,
        budget,
        color='#FFD56A',
        maximum=38,
        minimum=20,
        bold=True,
        spacing=1,
    )

    paint_brand_badge(
        img,
        (
            76,
            1066,
            202,
            1193,
        ),
        brand,
    )

    draw = ImageDraw.Draw(img)

    for box, text, maximum in [
        (
            (
                224,
                1083,
                620,
                1144,
            ),
            title,
            32,
        ),
        (
            (
                270,
                1144,
                593,
                1190,
            ),
            year_pref,
            25,
        ),
    ]:
        erase_region_horizontal(
            img,
            box,
        )

        draw = ImageDraw.Draw(img)

        draw_center(
            draw,
            box,
            text,
            color='#0B0B0B',
            maximum=maximum,
            minimum=12,
            bold=True,
        )

    fields = [
        (
            (
                126,
                1244,
                226,
                1290,
            ),
            year_pref,
        ),
        (
            (
                321,
                1242,
                442,
                1292,
            ),
            mileage_pref,
        ),
        (
            (
                538,
                1242,
                649,
                1292,
            ),
            condition_pref,
        ),
        (
            (
                744,
                1242,
                842,
                1292,
            ),
            priority_pref,
        ),
    ]

    for box, text in fields:
        fill_box(
            draw,
            box,
            '#0B0B0B',
        )

        draw_center(
            draw,
            box,
            text,
            color='#F4EFE6',
            maximum=19,
            minimum=10,
            bold=True,
        )

    path = output_path(
        'story_buy_request.jpeg'
    )

    img.save(
        path,
        'JPEG',
        quality=96,
        subsampling=0,
    )

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


def _build_story_queue(
    result: dict[str, Any],
) -> None:

    state = load_publish_state()

    initialized_now = (
        initialize_story_cursors(
            state
        )
    )

    if not story_time_allowed():
        result[
            'story_queue_status'
        ] = 'outside_allowed_window'

        return

    if not story_gap_allowed(
        state
    ):
        result[
            'story_queue_status'
        ] = 'minimum_gap_not_elapsed'

        return

    cursors = (
        state.get('story_cursors')
        or {}
    )

    opportunity = (
        _next_opportunity_after(
            cursors.get(
                'opportunity'
            )
            or {},
            state,
        )
    )

    buy_request = (
        _next_buy_after(
            cursors.get(
                'buy_request'
            )
            or {},
            state,
        )
    )

    candidates: list[
        tuple[
            datetime,
            str,
            dict[str, Any],
        ]
    ] = []

    if opportunity:
        dt = parse_iso(
            opportunity.get(
                'effective_at'
            )
        )

        if dt:
            candidates.append(
                (
                    dt,
                    'opportunity',
                    opportunity,
                )
            )

    if buy_request:
        dt = parse_iso(
            buy_request.get(
                'created_at'
            )
        )

        if dt:
            candidates.append(
                (
                    dt,
                    'buy_request',
                    buy_request,
                )
            )

    if not candidates:
        result[
            'story_queue_status'
        ] = (
            'initialized_recent_backfill_empty'
            if initialized_now
            else 'no_new_story'
        )

        return

    _dt, kind, row = min(
        candidates,
        key=lambda item:
            (
                item[0],
                item[1],
            ),
    )

    if kind == 'opportunity':
        event_key = (
            _story_entity_id(
                kind,
                row,
            )
        )

        result[
            'stories'
        ].append(
            {
                'kind':
                    'opportunity',

                'entity_id':
                    event_key,

                'key':
                    _story_content_key(
                        kind,
                        row,
                    ),

                'path':
                    str(
                        render_opportunity(
                            row
                        )
                    ),

                'cursor_after':
                    {
                        'kind':
                            'opportunity',

                        'at':
                            str(
                                row.get(
                                    'effective_at'
                                )
                                or _deal_effective_at(
                                    row
                                )
                            ),

                        'key':
                            event_key,
                    },
            }
        )

    else:
        submission_id = (
            _story_entity_id(
                kind,
                row,
            )
        )

        result[
            'stories'
        ].append(
            {
                'kind':
                    'buy_request',

                'entity_id':
                    submission_id,

                'key':
                    _story_content_key(
                        kind,
                        row,
                    ),

                'path':
                    str(
                        render_buy_request(
                            row
                        )
                    ),

                'cursor_after':
                    {
                        'kind':
                            'buy_request',

                        'at':
                            str(
                                row.get(
                                    'created_at'
                                )
                                or ''
                            ),

                        'key':
                            submission_id,
                    },
            }
        )

    result[
        'story_queue_status'
    ] = (
        'story_ready_after_recent_backfill_init'
        if initialized_now
        else 'story_ready'
    )


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

    posts = (
        posts
        if posts is not None
        else telegram_posts()
    )

    today = (
        _today_tehran()
    )

    def parsed(
        post: TelegramPost,
    ) -> dict[str, Any] | None:

        result: dict[
            str,
            Any,
        ] = {
            'usd':
                _market_rate_display(
                    post.text,
                    'usd',
                ),

            'gold18':
                _market_rate_display(
                    post.text,
                    'gold18',
                ),

            'coin':
                _market_rate_display(
                    post.text,
                    'coin',
                ),

            'ounce':
                _market_rate_display(
                    post.text,
                    'ounce',
                ),
        }

        if not all(
            result.get(key)
            for key
            in (
                'usd',
                'gold18',
                'coin',
                'ounce',
            )
        ):
            return None

        result[
            'source_post_id'
        ] = post.post_id

        result[
            'source_published_at'
        ] = (
            post.published_at
            .isoformat()
            if post.published_at
            else None
        )

        result[
            'source_tehran_date'
        ] = (
            str(
                _tehran_date(post)
            )
            if _tehran_date(post)
            else None
        )

        result[
            'target_tehran_date'
        ] = str(today)

        return result

    for post in reversed(posts):
        if (
            _tehran_date(post)
            == today
            and _is_market_rates(post)
        ):
            item = parsed(post)

            if item:
                return item

    if allow_previous_day:
        for post in reversed(posts):
            post_date = (
                _tehran_date(post)
            )

            if (
                post_date is not None
                and post_date < today
                and _is_market_rates(
                    post
                )
            ):
                item = parsed(
                    post
                )

                if item:
                    item[
                        'fallback_previous_day'
                    ] = True

                    return item

    return None


def latest_zero_car_prices(
    posts: list[TelegramPost] | None = None,
    *,
    allow_previous_day: bool = False,
) -> dict[str, Any] | None:

    posts = (
        posts
        if posts is not None
        else telegram_posts()
    )

    today = (
        _today_tehran()
    )

    def parsed(
        post: TelegramPost,
    ) -> dict[str, Any] | None:

        items: list[
            dict[str, Any]
        ] = []

        for line in post.text.splitlines():
            item = _zero_car_line(
                line.strip()
            )

            if item:
                items.append(item)

            if len(items) >= MAX_ZERO_CARS:
                break

        if len(items) < 8:
            return None

        return {
            'date_label':
                extract_date_label(
                    post.text
                ),

            'source_post_id':
                post.post_id,

            'source_published_at':
                (
                    post.published_at
                    .isoformat()
                    if post.published_at
                    else None
                ),

            'source_tehran_date':
                (
                    str(
                        _tehran_date(post)
                    )
                    if _tehran_date(post)
                    else None
                ),

            'target_tehran_date':
                str(today),

            'items':
                items,
        }

    for post in reversed(posts):
        if (
            _tehran_date(post)
            == today
            and _is_zero_car_prices(
                post
            )
        ):
            item = parsed(
                post
            )

            if item:
                return item

    if allow_previous_day:
        for post in reversed(posts):
            post_date = (
                _tehran_date(post)
            )

            if (
                post_date is not None
                and post_date < today
                and _is_zero_car_prices(
                    post
                )
            ):
                item = parsed(
                    post
                )

                if item:
                    item[
                        'fallback_previous_day'
                    ] = True

                    return item

    return None


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

    if (
        story_time_allowed(local)
        and story_gap_allowed(state)
    ):
        due.append(
            'story_queue'
        )

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

    validate_templates()

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


def publish_built(
    mode: str,
) -> int:

    manifest_path = (
        OUTPUT_DIR
        / 'manifest.json'
    )

    if not manifest_path.is_file():
        raise RuntimeError(
            'manifest.json missing'
        )

    manifest = json.loads(
        manifest_path
        .read_text(
            encoding='utf-8'
        )
    )

    channels = buffer_channels()
    state = load_publish_state()

    for name in TARGET_CHANNELS:
        if name not in channels:
            raise RuntimeError(
                f'Missing Buffer channel: {name}'
            )

    published_now = 0
    skipped_duplicates = 0
    skipped_no_content = 0

    if mode in {
        'opportunity',
        'story_queue',
    }:
        if not story_time_allowed():
            print(
                json.dumps(
                    {
                        'status':
                            'story_window_closed',

                        'tehran_time':
                            datetime.now(
                                TEHRAN
                            ).isoformat(),
                    },
                    ensure_ascii=False,
                )
            )

            return 0

        stories = (
            manifest.get('stories')
            or []
        )

        if mode == 'opportunity':
            stories = [
                item
                for item
                in stories
                if item.get('kind')
                == 'opportunity'
            ]

        if not stories:
            skipped_no_content += 1

        for story in stories[:1]:
            key = str(
                story['key']
            )

            kind = str(
                story.get('kind')
                or ''
            )

            entity_id = str(
                story.get('entity_id')
                or key.rsplit(
                    ':',
                    1,
                )[-1]
            )

            url = github_raw_url(
                Path(
                    story['path']
                ).name
            )

            for channel_name in TARGET_CHANNELS:
                if _story_entity_published_for_channel(
                    state,
                    channel_name,
                    kind,
                    entity_id,
                ):
                    skipped_duplicates += 1
                    continue

                post_id = publish_image(
                    str(
                        channels[
                            channel_name
                        ][
                            'id'
                        ]
                    ),
                    url,
                    post_type='story',
                )

                mark_published(
                    state,
                    channel_name,
                    key,
                    post_id,
                )

                state[
                    'story_last_published_at'
                ] = iso_now()

                save_publish_state(
                    state
                )

                published_now += 1

            if (
                mode == 'story_queue'
                and story.get(
                    'cursor_after'
                )
                and all(
                    _story_entity_published_for_channel(
                        state,
                        channel_name,
                        kind,
                        entity_id,
                    )
                    for channel_name
                    in TARGET_CHANNELS
                )
            ):
                cursor = (
                    story[
                        'cursor_after'
                    ]
                )

                state.setdefault(
                    'story_cursors',
                    {},
                )[
                    str(
                        cursor['kind']
                    )
                ] = {
                    'at':
                        str(
                            cursor['at']
                        ),

                    'key':
                        str(
                            cursor['key']
                        ),
                }

                save_publish_state(
                    state
                )

    elif mode == 'market':
        market = manifest.get(
            'market_post'
        )

        if not market:
            skipped_no_content += 1

        else:
            key = str(
                market['key']
            )

            url = github_raw_url(
                Path(
                    market['path']
                ).name
            )

            for channel_name in TARGET_CHANNELS:
                if is_published(
                    state,
                    channel_name,
                    key,
                ):
                    skipped_duplicates += 1
                    continue

                post_id = publish_image(
                    str(
                        channels[
                            channel_name
                        ][
                            'id'
                        ]
                    ),
                    url,
                    post_type='post',
                    text=POST_CAPTION,
                )

                mark_published(
                    state,
                    channel_name,
                    key,
                    post_id,
                )

                published_now += 1

    elif mode == 'cars':
        slides = (
            manifest.get(
                'car_price_slides'
            )
            or []
        )

        if not slides:
            skipped_no_content += 1

        else:
            key = str(
                manifest.get(
                    'car_price_key'
                )
                or slides[0]['key']
            )

            urls = [
                github_raw_url(
                    Path(
                        slide['path']
                    ).name
                )
                for slide
                in slides
            ]

            for channel_name in TARGET_CHANNELS:
                if is_published(
                    state,
                    channel_name,
                    key,
                ):
                    skipped_duplicates += 1
                    continue

                if len(urls) == 1:
                    post_id = publish_image(
                        str(
                            channels[
                                channel_name
                            ][
                                'id'
                            ]
                        ),
                        urls[0],
                        post_type='post',
                        text=POST_CAPTION,
                    )

                else:
                    post_id = publish_carousel(
                        str(
                            channels[
                                channel_name
                            ][
                                'id'
                            ]
                        ),
                        urls,
                        caption=POST_CAPTION,
                    )

                mark_published(
                    state,
                    channel_name,
                    key,
                    post_id,
                )

                published_now += 1

    else:
        raise ValueError(
            f'Unsupported publish mode: {mode}'
        )

    print(
        json.dumps(
            {
                'status':
                    'publish_complete',

                'mode':
                    mode,

                'published_now':
                    published_now,

                'skipped_duplicates':
                    skipped_duplicates,

                'skipped_no_content':
                    skipped_no_content,

                'buy_request_story_enabled':
                    True,
            },
            ensure_ascii=False,
        )
    )

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
        return validate_templates()

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
