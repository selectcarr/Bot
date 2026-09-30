#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, features

ROOT = Path(__file__).resolve().parent
STAGE4_VERSION = "2.2.0"

TARGET_CHANNELS = (
    "select_carr",
    "select_carrr",
)

BUFFER_API = (
    os.getenv(
        "BUFFER_API_URL",
        "https://api.buffer.com",
    ).strip()
    or "https://api.buffer.com"
)

BUFFER_API_KEY = os.getenv(
    "BUFFER_API_KEY",
    "",
).strip()

CONTENT_CHANNEL_URL = os.getenv(
    "SELECT_CARR_CONTENT_CHANNEL_URL",
    "https://t.me/s/select_carr",
).strip()

MARKET_DB = Path(
    os.getenv(
        "SELECT_CARR_MARKET_DB",
        ROOT
        / "select_carr_market_runtime"
        / "select_carr_market.sqlite3",
    )
)

MEDIA_BRANCH = os.getenv(
    "SELECT_CARR_STAGE4_MEDIA_BRANCH",
    "select-carr-stage4-media",
).strip()

TEMPLATE_DIR = Path(
    os.getenv(
        "STAGE4_TEMPLATE_DIR",
        ROOT
        / "assets"
        / "stage4_templates",
    )
)

BRAND_LOGO_DIR = Path(
    os.getenv(
        "STAGE4_BRAND_LOGO_DIR",
        ROOT
        / "assets"
        / "stage4_brand_logos",
    )
)

OUTPUT_DIR = Path(
    os.getenv(
        "STAGE4_OUTPUT_DIR",
        ROOT
        / "stage4_runtime",
    )
)

PUBLISH_STATE_PATH = (
    OUTPUT_DIR
    / "publish_state.json"
)

# ==========================================================
# PRIVACY / PUBLICATION RULE
# ==========================================================
#
# An active buyer request is NOT permission to publish it publicly.
#
# The current sc_intake_submissions source does not contain a dedicated,
# explicit public-story consent field.
#
# Therefore the Buy Request story is intentionally disabled.
#
# Its locked template remains validated and stored, but Stage 4 will not
# create or publish that story until a separate explicit-consent source
# is designed and approved.
#
# ==========================================================

BUY_REQUEST_STORY_ENABLED = False

TEMPLATES = {
    "opportunity":
        TEMPLATE_DIR
        / "luxury_mercedes_showroom_poster.png",

    "buy_request":
        TEMPLATE_DIR
        / "luxury_car_showroom_mystery_offer.png",

    "market":
        TEMPLATE_DIR
        / "luxury_gold_market_showroom_board.png",

    "cars":
        TEMPLATE_DIR
        / "luxury_gold_car_showroom_price_list.png",
}

EXPECTED_TEMPLATE_SIZES = {
    "opportunity": (
        1010,
        1787,
    ),
    "buy_request": (
        916,
        1695,
    ),
    "market": (
        1117,
        1460,
    ),
    "cars": (
        1178,
        1466,
    ),
}

MAX_ZERO_CAR_SLIDES = 5
ZERO_CARS_PER_SLIDE = 16
MAX_ZERO_CARS = (
    MAX_ZERO_CAR_SLIDES
    * ZERO_CARS_PER_SLIDE
)

MAX_PUBLISH_STATE_ENTRIES = 500

ARABIC_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]"
)

BIDI_CONTROL_RE = re.compile(
    r"[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]"
)

CONTROL_RE = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]"
)

BODY_LABELS = {
    "factory": "بدون رنگ",
    "clean": "بدون رنگ",
    "minor_paint": "لکه رنگ",
    "painted": "رنگ‌شده",
    "full_paint": "تمام رنگ",
    "replaced": "قطعه تعویضی",
    "accident": "آسیب‌دیده",
    "unknown": "سالم",
    "": "سالم",
}

BRAND_DISPLAY_FA = {
    "hyundai": "هیوندای",
    "kia": "کیا",
    "peugeot": "پژو",
    "renault": "رنو",
    "toyota": "تویوتا",
    "lexus": "لکسوس",
    "porsche": "پورشه",
    "mercedes-benz": "مرسدس بنز",
    "mercedes benz": "مرسدس بنز",
    "nissan": "نیسان",
    "mazda": "مزدا",
    "mitsubishi": "میتسوبیشی",
    "honda": "هوندا",
    "chery": "چری",
    "saipa": "سایپا",
    "iran khodro": "ایران خودرو",
}

PERSIAN_MONTHS = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)


# ==========================================================
# BASIC HELPERS
# ==========================================================


def iso_now() -> str:
    return (
        datetime.now(
            timezone.utc
        ).isoformat()
    )


def fa_to_en(
    value: object,
) -> str:
    return str(
        value
        or ""
    ).translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )


def sanitize_text(
    value: object,
    *,
    multiline: bool = False,
    max_chars: int | None = None,
) -> str:

    text = unicodedata.normalize(
        "NFKC",
        str(
            value
            or ""
        ),
    )

    text = BIDI_CONTROL_RE.sub(
        "",
        text,
    )

    text = CONTROL_RE.sub(
        " ",
        text,
    )

    text = (
        text
        .replace(
            "\r\n",
            "\n",
        )
        .replace(
            "\r",
            "\n",
        )
    )

    if multiline:

        lines = []

        for line in text.split(
            "\n"
        ):
            line = re.sub(
                r"[ \t\u00A0]+",
                " ",
                line,
            ).strip()

            if line:
                lines.append(
                    line
                )

        text = "\n".join(
            lines
        )

    else:

        text = re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    if (
        max_chars
        is not None
        and len(
            text
        )
        > max_chars
    ):
        text = (
            text[
                : max(
                    1,
                    max_chars - 1,
                )
            ]
            .rstrip()
            + "…"
        )

    return text


def normalize_space(
    value: object,
) -> str:

    return re.sub(
        r"\s+",
        " ",
        fa_to_en(
            value
        ),
    ).strip()


def money(
    value: object,
) -> str:

    if value in (
        None,
        "",
    ):
        return "-"

    try:
        return f"{int(value):,}"

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
        return "-"

    if (
        abs(
            number
            - round(
                number
            )
        )
        < 0.05
    ):
        text = str(
            int(
                round(
                    number
                )
            )
        )

    else:
        text = f"{number:.1f}"

    return (
        text
        + "٪"
    )


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
        or "سالم",
    )


def technical_label(
    row: dict[str, Any],
) -> str:

    explicit = sanitize_text(
        row.get(
            "mechanical_status"
        ),
        max_chars=22,
    )

    if explicit:
        return explicit

    return "فنی سالم"


def compact_vehicle_title(
    row: dict[str, Any],
) -> str:

    brand = sanitize_text(
        row.get(
            "brand"
        ),
        max_chars=35,
    )

    model = sanitize_text(
        row.get(
            "model"
        ),
        max_chars=45,
    )

    trim = sanitize_text(
        row.get(
            "trim"
        ),
        max_chars=35,
    )

    year = sanitize_text(
        row.get(
            "model_year"
        ),
        max_chars=8,
    )

    if ARABIC_RE.search(
        model
    ):
        brand = (
            BRAND_DISPLAY_FA.get(
                brand.casefold(),
                brand,
            )
        )

    parts = [
        item
        for item
        in (
            brand,
            model,
            trim,
            year,
        )
        if item
    ]

    title = " ".join(
        parts
    )

    if (
        len(
            title
        )
        > 42
        and trim
    ):
        title = " ".join(
            item
            for item
            in (
                brand,
                model,
                year,
            )
            if item
        )

    return (
        sanitize_text(
            title,
            max_chars=46,
        )
        or "خودرو"
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
            " ",
            "_",
        )
        .replace(
            "-",
            "_",
        )
    )

    return re.sub(
        r"[^\w\u0600-\u06ff]+",
        "",
        text,
        flags=re.UNICODE,
    ).strip(
        "_"
    )


# ==========================================================
# FONT + PERSIAN RTL
# ==========================================================


def _font_candidates(
    bold: bool,
) -> list[Path]:

    env_key = (
        "STAGE4_FONT_BOLD"
        if bold
        else "STAGE4_FONT_REGULAR"
    )

    result: list[Path] = []

    env_path = os.getenv(
        env_key,
        "",
    ).strip()

    if env_path:
        result.append(
            Path(
                env_path
            )
        )

    if bold:

        result.extend(
            [
                ROOT
                / "assets"
                / "fonts"
                / "Vazirmatn-Bold.ttf",

                ROOT
                / "assets"
                / "fonts"
                / "Vazirmatn-SemiBold.ttf",

                Path(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                ),

                Path(
                    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"
                ),
            ]
        )

    else:

        result.extend(
            [
                ROOT
                / "assets"
                / "fonts"
                / "Vazirmatn-Regular.ttf",

                Path(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                ),

                Path(
                    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"
                ),
            ]
        )

    output = []
    seen = set()

    for item in result:

        key = str(
            item
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        output.append(
            item
        )

    return output


def find_font(
    bold: bool = False,
) -> str:

    for candidate in _font_candidates(
        bold
    ):

        if candidate.is_file():
            return str(
                candidate
            )

    raise RuntimeError(
        "Persian font not found. "
        "Install DejaVu/Noto or add Vazirmatn under assets/fonts."
    )


def font(
    size: int,
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:

    return ImageFont.truetype(
        find_font(
            bold
        ),
        max(
            8,
            int(
                size
            ),
        ),
    )


def layout_kwargs(
    text: str,
) -> dict[str, str]:

    if ARABIC_RE.search(
        text
    ):
        return {
            "direction": "rtl",
            "language": "fa",
        }

    return {
        "direction": "ltr",
        "language": "en",
    }


def validate_text_engine() -> dict[str, str]:

    if not features.check(
        "raqm"
    ):
        raise RuntimeError(
            "Pillow RAQM is unavailable; "
            "Stage 4 refuses unsafe Persian rendering."
        )

    regular = find_font(
        False
    )

    bold = find_font(
        True
    )

    probe = Image.new(
        "RGB",
        (
            500,
            100,
        ),
        "white",
    )

    probe_draw = ImageDraw.Draw(
        probe
    )

    probe_font = ImageFont.truetype(
        regular,
        28,
    )

    probe_draw.textbbox(
        (
            0,
            0,
        ),
        "هیوندای سانتافه 1402",
        font=probe_font,
        direction="rtl",
        language="fa",
    )

    return {
        "raqm": "true",
        "regular_font": regular,
        "bold_font": bold,
    }


def draw_center(
    draw: ImageDraw.ImageDraw,
    box: tuple[
        int,
        int,
        int,
        int,
    ],
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

        candidate_bbox = draw.multiline_textbbox(
            (
                0,
                0,
            ),
            raw,
            font=candidate_font,
            spacing=spacing,
            align="center",
            **kwargs,
        )

        width = (
            candidate_bbox[2]
            - candidate_bbox[0]
        )

        height = (
            candidate_bbox[3]
            - candidate_bbox[1]
        )

        if (
            width
            <= box[2]
            - box[0]
            and height
            <= box[3]
            - box[1]
        ):
            selected = candidate_font
            bbox = candidate_bbox
            break

    else:

        bbox = draw.multiline_textbbox(
            (
                0,
                0,
            ),
            raw,
            font=selected,
            spacing=spacing,
            align="center",
            **kwargs,
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
            (
                box[2]
                - box[0]
            )
            - width
        )
        / 2
        - bbox[0]
    )

    y = (
        box[1]
        + (
            (
                box[3]
                - box[1]
            )
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
        align="center",
        **kwargs,
    )


def fill_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[
        int,
        int,
        int,
        int,
    ],
    color: str,
) -> None:

    draw.rectangle(
        box,
        fill=color,
    )


def erase_region_vertical(
    img: Image.Image,
    box: tuple[
        int,
        int,
        int,
        int,
    ],
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
        x1
        <= x0
        or y1
        <= y0
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
                    y0
                    - edge_height,
                ),
                y0,
            )
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
                    y1
                    + edge_height,
                ),
            )
        ]

        if not top_samples:
            top_samples = [
                pixels[
                    x,
                    y0,
                ]
            ]

        if not bottom_samples:
            bottom_samples = [
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
            // len(
                top_samples
            )
            for index
            in range(
                3
            )
        )

        bottom = tuple(
            sum(
                pixel[index]
                for pixel
                in bottom_samples
            )
            // len(
                bottom_samples
            )
            for index
            in range(
                3
            )
        )

        for y in range(
            y0,
            y1,
        ):

            factor = (
                (
                    y
                    - y0
                )
                / span
            )

            pixels[
                x,
                y,
            ] = tuple(
                int(
                    top[index]
                    * (
                        1
                        - factor
                    )
                    + bottom[index]
                    * factor
                )
                for index
                in range(
                    3
                )
            )


def paste_contain(
    base: Image.Image,
    source: Path,
    box: tuple[
        int,
        int,
        int,
        int,
    ],
) -> None:

    logo = Image.open(
        source
    ).convert(
        "RGBA"
    )

    box_width = max(
        1,
        box[2]
        - box[0],
    )

    box_height = max(
        1,
        box[3]
        - box[1],
    )

    ratio = min(
        box_width
        / logo.width,
        box_height
        / logo.height,
    )

    logo = logo.resize(
        (
            max(
                1,
                int(
                    logo.width
                    * ratio
                ),
            ),
            max(
                1,
                int(
                    logo.height
                    * ratio
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
        "RGBA"
    )

    rgba.alpha_composite(
        logo,
        (
            x,
            y,
        ),
    )

    base.paste(
        rgba.convert(
            "RGB"
        )
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

    names = (
        raw,
        safe_filename_token(
            raw
        ),
        raw.replace(
            " ",
            "_",
        ),
        raw.replace(
            " ",
            "-",
        ),
    )

    for name in names:

        for extension in (
            ".png",
            ".webp",
            ".jpg",
            ".jpeg",
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


def paint_brand_badge(
    img: Image.Image,
    box: tuple[
        int,
        int,
        int,
        int,
    ],
    brand: object,
) -> None:

    draw = ImageDraw.Draw(
        img
    )

    center_x = (
        box[0]
        + box[2]
    ) // 2

    center_y = (
        box[1]
        + box[3]
    ) // 2

    radius = max(
        10,
        min(
            box[2]
            - box[0],
            box[3]
            - box[1],
        )
        // 2
        - 3,
    )

    circle = (
        center_x
        - radius,
        center_y
        - radius,
        center_x
        + radius,
        center_y
        + radius,
    )

    draw.ellipse(
        circle,
        fill="#F5B942",
        outline="#0B0B0B",
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
                circle[0]
                + 12,
                circle[1]
                + 12,
                circle[2]
                - 12,
                circle[3]
                - 12,
            ),
        )

    else:

        draw_center(
            ImageDraw.Draw(
                img
            ),
            circle,
            "SC",
            color="#0B0B0B",
            maximum=19,
            minimum=12,
            bold=True,
        )


# ==========================================================
# BUFFER
# ==========================================================


def buffer_call(
    query: str,
) -> dict[str, Any]:

    if not BUFFER_API_KEY:
        raise RuntimeError(
            "BUFFER_API_KEY is missing"
        )

    request = urllib.request.Request(
        BUFFER_API,
        data=json.dumps(
            {
                "query": query,
            }
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type":
                "application/json",

            "Authorization":
                f"Bearer {BUFFER_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        payload = json.loads(
            response
            .read()
            .decode(
                "utf-8"
            )
        )

    if payload.get(
        "errors"
    ):
        raise RuntimeError(
            json.dumps(
                payload[
                    "errors"
                ],
                ensure_ascii=False,
            )
        )

    return (
        payload.get(
            "data"
        )
        or {}
    )


def buffer_channels() -> dict[
    str,
    dict[str, Any],
]:

    data = buffer_call(
        """
        query {
          account {
            organizations {
              id
              name
            }
          }
        }
        """
    )

    organizations = (
        data
        .get(
            "account",
            {},
        )
        .get(
            "organizations",
            [],
        )
    )

    found = {}

    for organization in organizations:

        organization_id = str(
            organization[
                "id"
            ]
        )

        channels = buffer_call(
            f"""
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
            """
        ).get(
            "channels",
            [],
        )

        for channel in channels:

            name = str(
                channel.get(
                    "name",
                    "",
                )
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
        if name
        not in channels
    ]

    if missing:
        raise RuntimeError(
            "Missing Buffer channels: "
            + ", ".join(
                missing
            )
        )

    for name in TARGET_CHANNELS:

        row = channels[
            name
        ]

        if (
            str(
                row.get(
                    "service",
                    "",
                )
            ).lower()
            != "instagram"
        ):
            raise RuntimeError(
                f"{name} is not Instagram"
            )

        if (
            row.get(
                "isDisconnected"
            )
            is True
        ):
            raise RuntimeError(
                f"{name} is disconnected"
            )

        if (
            row.get(
                "isLocked"
            )
            is True
        ):
            raise RuntimeError(
                f"{name} is locked"
            )

    print(
        json.dumps(
            {
                "status": "ok",
                "stage4_version":
                    STAGE4_VERSION,
                "channels":
                    list(
                        TARGET_CHANNELS
                    ),
            },
            ensure_ascii=False,
        )
    )

    return 0


# ==========================================================
# TEMPLATE VALIDATION
# ==========================================================


def validate_templates() -> int:

    text_engine = (
        validate_text_engine()
    )

    details = []

    for key, path in TEMPLATES.items():

        if not path.is_file():
            raise RuntimeError(
                f"Template missing: {path}"
            )

        try:

            with Image.open(
                path
            ) as image:

                size = tuple(
                    image.size
                )

                image_format = (
                    image.format
                )

                image.verify()

        except Exception as exc:

            raise RuntimeError(
                f"Template unreadable: {path}"
            ) from exc

        expected = (
            EXPECTED_TEMPLATE_SIZES[
                key
            ]
        )

        if size != expected:
            raise RuntimeError(
                f"Locked template size mismatch for {key}: "
                f"got={size}, expected={expected}"
            )

        details.append(
            {
                "template":
                    key,

                "file":
                    str(
                        path
                    ),

                "size":
                    list(
                        size
                    ),

                "format":
                    image_format,
            }
        )

    print(
        json.dumps(
            {
                "status":
                    "ok",

                "templates":
                    4,

                "stage4_version":
                    STAGE4_VERSION,

                "text_engine":
                    text_engine,

                "buy_request_story_enabled":
                    BUY_REQUEST_STORY_ENABLED,

                "details":
                    details,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


# ==========================================================
# DATA: LATEST OPPORTUNITY
# ==========================================================


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

        row = connection.execute(
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
            LIMIT 1
            """
        ).fetchone()

        return (
            dict(
                row
            )
            if row
            else None
        )

    finally:
        connection.close()


# ==========================================================
# DATA: TELEGRAM CONTENT CHANNEL
# ==========================================================


def telegram_messages() -> list[str]:

    request = urllib.request.Request(
        CONTENT_CHANNEL_URL,
        headers={
            "User-Agent":
                "Mozilla/5.0",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        raw = (
            response
            .read()
            .decode(
                "utf-8",
                errors="ignore",
            )
        )

    blocks = re.findall(
        r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
        raw,
        flags=(
            re.I
            | re.S
        ),
    )

    output = []

    for block in blocks:

        clean = re.sub(
            r"<br\s*/?>",
            "\n",
            block,
            flags=re.I,
        )

        clean = re.sub(
            r"<[^>]+>",
            "",
            clean,
        )

        clean = html.unescape(
            clean
        ).strip()

        if clean:
            output.append(
                clean
            )

    return output


def extract_price(
    text: str,
    labels: list[str],
) -> int | None:

    normalized = fa_to_en(
        text
    )

    for label in labels:

        pattern = (
            re.escape(
                label
            )
            + r".{0,40}?"
            + r"([0-9][0-9,\.]{2,})"
        )

        match = re.search(
            pattern,
            normalized,
            flags=(
                re.I
                | re.S
            ),
        )

        if match:

            digits = re.sub(
                r"\D",
                "",
                match.group(
                    1
                ),
            )

            if digits:
                return int(
                    digits
                )

    return None


def latest_market_prices() -> dict[
    str,
    Any,
] | None:

    for text in reversed(
        telegram_messages()
    ):

        normalized = normalize_space(
            text
        )

        if (
            "دلار"
            not in normalized
            or "طلا"
            not in normalized
        ):
            continue

        result = {
            "usd":
                extract_price(
                    text,
                    [
                        "دلار آزاد",
                        "دلار",
                    ],
                ),

            "gold18":
                extract_price(
                    text,
                    [
                        "طلای 18",
                        "طلای ۱۸",
                        "طلا 18",
                        "طلا ۱۸",
                    ],
                ),

            "coin":
                extract_price(
                    text,
                    [
                        "سکه امامی",
                        "سکه",
                    ],
                ),

            "ounce":
                extract_price(
                    text,
                    [
                        "اونس",
                        "انس",
                    ],
                ),
        }

        if (
            result[
                "usd"
            ]
            and result[
                "gold18"
            ]
        ):
            return result

    return None


def extract_date_label(
    text: str,
) -> str:

    normalized = fa_to_en(
        text
    )

    months = "|".join(
        map(
            re.escape,
            PERSIAN_MONTHS,
        )
    )

    match = re.search(
        rf"({months})\s*(1[34]\d{{2}})",
        normalized,
    )

    if match:
        return (
            f"{match.group(1)} "
            f"{match.group(2)}"
        )

    match = re.search(
        rf"(1[34]\d{{2}})\s*({months})",
        normalized,
    )

    if match:
        return (
            f"{match.group(2)} "
            f"{match.group(1)}"
        )

    return "قیمت روز"


def split_name_variant(
    value: str,
) -> tuple[
    str,
    str,
]:

    raw = sanitize_text(
        normalize_space(
            value
        ),
        max_chars=100,
    )

    for separator in (
        " | ",
        " / ",
        " - ",
        " – ",
        " — ",
    ):

        if separator in raw:

            left, right = raw.split(
                separator,
                1,
            )

            if (
                left.strip()
                and right.strip()
            ):
                return (
                    left.strip(),
                    right.strip(),
                )

    match = re.match(
        r"^(.*?)"
        r"(\s+(?:"
        r"تیپ\s+\S+|"
        r"دنده(?:ای|‌ای)|"
        r"اتوماتیک|"
        r"پلاس|"
        r"پرومکس|"
        r"پریمیوم|"
        r"V\d+|G|S|LX|EF7"
        r"))$",
        raw,
        flags=re.I,
    )

    if (
        match
        and match.group(
            1
        ).strip()
    ):
        return (
            match.group(
                1
            ).strip(),
            match.group(
                2
            ).strip(),
        )

    return (
        raw,
        "",
    )


def latest_zero_car_prices() -> dict[
    str,
    Any,
] | None:

    for text in reversed(
        telegram_messages()
    ):

        normalized = normalize_space(
            text
        )

        if (
            "قیمت"
            not in normalized
            or "خودرو"
            not in normalized
        ):
            continue

        items = []

        lines = [
            line.strip()
            for line
            in text.splitlines()
            if line.strip()
        ]

        for line in lines:

            match = re.search(
                r"(.{2,80}?)"
                r"[\s:：\-–—]+"
                r"([0-9۰-۹٠-٩]"
                r"[0-9۰-۹٠-٩,\.]{5,})",
                line,
            )

            if not match:
                continue

            price_digits = re.sub(
                r"\D",
                "",
                fa_to_en(
                    match.group(
                        2
                    )
                ),
            )

            if not price_digits:
                continue

            price = int(
                price_digits
            )

            if price < 100_000_000:
                continue

            label = (
                match.group(
                    1
                )
                .strip(
                    " -:|–—"
                )
            )

            name, variant = (
                split_name_variant(
                    label
                )
            )

            items.append(
                {
                    "name":
                        sanitize_text(
                            name,
                            max_chars=34,
                        ),

                    "variant":
                        sanitize_text(
                            variant,
                            max_chars=22,
                        ),

                    "price":
                        price,
                }
            )

            if (
                len(
                    items
                )
                >= MAX_ZERO_CARS
            ):
                break

        if (
            len(
                items
            )
            >= 4
        ):
            return {
                "date_label":
                    extract_date_label(
                        text
                    ),

                "items":
                    items,
            }

    return None


# ==========================================================
# TEMPLATE / OUTPUT HELPERS
# ==========================================================


def template(
    name: str,
) -> Image.Image:

    path = TEMPLATES[
        name
    ]

    if not path.is_file():
        raise RuntimeError(
            f"Template missing: {path}"
        )

    return (
        Image.open(
            path
        )
        .convert(
            "RGB"
        )
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


# ==========================================================
# RENDER: OPPORTUNITY STORY
# ==========================================================


def render_opportunity(
    deal: dict[
        str,
        Any,
    ],
) -> Path:

    img = template(
        "opportunity"
    )

    draw = ImageDraw.Draw(
        img
    )

    title = compact_vehicle_title(
        deal
    )

    brand = sanitize_text(
        deal.get(
            "brand"
        ),
        max_chars=40,
    )

    discount = format_discount(
        deal.get(
            "discount_percent"
        )
    )

    year = (
        sanitize_text(
            deal.get(
                "model_year"
            ),
            max_chars=8,
        )
        or "-"
    )

    if (
        deal.get(
            "mileage"
        )
        is None
    ):
        mileage = "نامشخص"

    else:
        mileage = (
            money(
                deal.get(
                    "mileage"
                )
            )
            + "\nکیلومتر"
        )

    technical = technical_label(
        deal
    )

    body = body_label(
        deal.get(
            "body_condition"
        )
    )

    percent_box = (
        678,
        151,
        784,
        211,
    )

    fill_box(
        draw,
        percent_box,
        "#E50914",
    )

    draw_center(
        draw,
        percent_box,
        discount,
        color="#FFFFFF",
        maximum=31,
        minimum=18,
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

    draw = ImageDraw.Draw(
        img
    )

    draw_center(
        draw,
        title_box,
        title,
        color="#0B0B0B",
        maximum=30,
        minimum=14,
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
            23,
            13,
        ),
        (
            (
                322,
                1128,
                484,
                1183,
            ),
            mileage,
            19,
            11,
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
            19,
            11,
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
            19,
            11,
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
            "#0B0B0B",
        )

        draw_center(
            draw,
            box,
            text,
            color="#F4EFE6",
            maximum=maximum,
            minimum=minimum,
            spacing=1,
        )

    price_fields = [
        (
            (
                155,
                1220,
                414,
                1261,
            ),
            deal.get(
                "candidate_price"
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
                "average_price"
            ),
        ),
    ]

    for box, value in price_fields:

        fill_box(
            draw,
            box,
            "#0B0B0B",
        )

        draw_center(
            draw,
            box,
            money(
                value
            ),
            color="#FFD56A",
            maximum=27,
            minimum=14,
        )

    path = output_path(
        "story_opportunity.jpeg"
    )

    img.save(
        path,
        "JPEG",
        quality=96,
        subsampling=0,
    )

    return path


# ==========================================================
# RENDER: MARKET POST
# ==========================================================


def render_market_post(
    values: dict[
        str,
        Any,
    ],
) -> Path:

    img = template(
        "market"
    )

    draw = ImageDraw.Draw(
        img
    )

    regions = {
        "gold18": (
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

        "usd": (
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

        "coin": (
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

        "ounce": (
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

    for (
        key,
        (
            region,
            number_box,
            unit_box,
        ),
    ) in regions.items():

        fill_box(
            draw,
            region,
            "#0B0B0B",
        )

        draw_center(
            draw,
            number_box,
            money(
                values.get(
                    key
                )
            ),
            color="#FFFFFF",
            maximum=30,
            minimum=15,
        )

        draw_center(
            draw,
            unit_box,
            "تومان",
            color="#F4EFE6",
            maximum=18,
            minimum=11,
            bold=False,
        )

    path = output_path(
        "post_market.jpeg"
    )

    img.save(
        path,
        "JPEG",
        quality=96,
        subsampling=0,
    )

    return path


# ==========================================================
# RENDER: ZERO CAR PRICE CAROUSEL
# ==========================================================


def chunks(
    items: list[Any],
    size: int,
) -> list[
    list[Any]
]:

    return [
        items[
            index:
            index + size
        ]
        for index
        in range(
            0,
            len(
                items
            ),
            size,
        )
    ]


def render_car_prices(
    data: dict[
        str,
        Any,
    ],
) -> list[Path]:

    items = list(
        data.get(
            "items"
        )
        or []
    )[
        :MAX_ZERO_CARS
    ]

    if not items:
        return []

    pages = chunks(
        items,
        ZERO_CARS_PER_SLIDE,
    )

    paths = []

    row_bands = [
        (
            505,
            599,
        ),
        (
            609,
            689,
        ),
        (
            699,
            778,
        ),
        (
            788,
            868,
        ),
        (
            878,
            958,
        ),
        (
            968,
            1048,
        ),
        (
            1058,
            1140,
        ),
        (
            1150,
            1242,
        ),
    ]

    for (
        page_number,
        page_items,
    ) in enumerate(
        pages,
        start=1,
    ):

        img = template(
            "cars"
        )

        draw = ImageDraw.Draw(
            img
        )

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
            "#0B0B0B",
        )

        img.paste(
            calendar_icon,
            (
                66,
                115,
            ),
        )

        draw = ImageDraw.Draw(
            img
        )

        draw_center(
            draw,
            (
                112,
                101,
                239,
                143,
            ),
            sanitize_text(
                data.get(
                    "date_label"
                )
                or "قیمت روز",
                max_chars=20,
            ),
            color="#FFFFFF",
            maximum=18,
            minimum=10,
        )

        draw_center(
            draw,
            (
                112,
                146,
                239,
                180,
            ),
            "قیمت ها به تومان",
            color="#F4EFE6",
            maximum=12,
            minimum=9,
            bold=False,
        )

        for (
            row_index,
            (
                y0,
                y1,
            ),
        ) in enumerate(
            row_bands
        ):

            for (
                side_index,
                side,
            ) in enumerate(
                (
                    "left",
                    "right",
                )
            ):

                slot = (
                    row_index
                    * 2
                    + side_index
                )

                item = (
                    page_items[
                        slot
                    ]
                    if slot
                    < len(
                        page_items
                    )
                    else None
                )

                global_index = (
                    (
                        page_number
                        - 1
                    )
                    * ZERO_CARS_PER_SLIDE
                    + slot
                    + 1
                )

                if side == "left":

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
                        y0 + 11,
                        318,
                        min(
                            y1 - 34,
                            y0 + 38,
                        ),
                    )

                    variant_box = (
                        151,
                        y0 + 40,
                        318,
                        y1 - 10,
                    )

                    separator_x = 330

                    price_box = (
                        351,
                        y0 + 12,
                        540,
                        min(
                            y1 - 31,
                            y0 + 42,
                        ),
                    )

                    toman_box = (
                        382,
                        y0 + 44,
                        516,
                        y1 - 9,
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
                        y0 + 11,
                        858,
                        min(
                            y1 - 34,
                            y0 + 38,
                        ),
                    )

                    variant_box = (
                        688,
                        y0 + 40,
                        858,
                        y1 - 10,
                    )

                    separator_x = 873

                    price_box = (
                        897,
                        y0 + 12,
                        1087,
                        min(
                            y1 - 31,
                            y0 + 42,
                        ),
                    )

                    toman_box = (
                        932,
                        y0 + 44,
                        1065,
                        y1 - 9,
                    )

                draw.rounded_rectangle(
                    panel,
                    radius=14,
                    fill="#0B0B0B",
                    outline="#C88A16",
                    width=2,
                )

                draw.line(
                    (
                        separator_x,
                        y0 + 10,
                        separator_x,
                        y1 - 10,
                    ),
                    fill="#C88A16",
                    width=2,
                )

                if item is None:
                    continue

                draw.rounded_rectangle(
                    index_box,
                    radius=7,
                    fill="#F5B942",
                )

                draw_center(
                    draw,
                    index_box,
                    f"{global_index:02d}",
                    color="#0B0B0B",
                    maximum=16,
                    minimum=10,
                )

                draw_center(
                    draw,
                    name_box,
                    sanitize_text(
                        item.get(
                            "name"
                        ),
                        max_chars=24,
                    ),
                    color="#F5B942",
                    maximum=19,
                    minimum=10,
                )

                draw_center(
                    draw,
                    variant_box,
                    sanitize_text(
                        item.get(
                            "variant"
                        ),
                        max_chars=18,
                    ),
                    color="#F4EFE6",
                    maximum=14,
                    minimum=9,
                    bold=False,
                )

                draw_center(
                    draw,
                    price_box,
                    money(
                        item.get(
                            "price"
                        )
                    ),
                    color="#FFFFFF",
                    maximum=18,
                    minimum=10,
                )

                draw_center(
                    draw,
                    toman_box,
                    "تومان",
                    color="#F4EFE6",
                    maximum=12,
                    minimum=9,
                    bold=False,
                )

        page_box = (
            966,
            1294,
            1048,
            1337,
        )

        fill_box(
            draw,
            page_box,
            "#0B0B0B",
        )

        draw_center(
            draw,
            page_box,
            (
                f"{page_number} "
                f"از {len(pages)}"
            ),
            color="#FFFFFF",
            maximum=18,
            minimum=10,
        )

        path = output_path(
            f"post_car_prices_{page_number:02d}.jpeg"
        )

        img.save(
            path,
            "JPEG",
            quality=96,
            subsampling=0,
        )

        paths.append(
            path
        )

    return paths


# ==========================================================
# BUILD
# ==========================================================


def build_content() -> dict[
    str,
    Any,
]:

    validate_templates()

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for old_file in OUTPUT_DIR.glob(
        "*.jpeg"
    ):
        old_file.unlink()

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if manifest_path.exists():
        manifest_path.unlink()

    result = {
        "version":
            STAGE4_VERSION,

        "generated_at":
            iso_now(),

        "stories":
            [],

        "market_post":
            None,

        "car_price_key":
            None,

        "car_price_slides":
            [],

        "buy_request_story":
            {
                "enabled":
                    False,

                "reason":
                    "no_explicit_public_consent_source",
            },
    }

    deal = latest_market_deal()

    if deal:

        result[
            "stories"
        ].append(
            {
                "kind":
                    "opportunity",

                "key":
                    "opportunity:"
                    + str(
                        deal.get(
                            "event_key"
                        )
                        or deal.get(
                            "source_key"
                        )
                        or ""
                    ),

                "path":
                    str(
                        render_opportunity(
                            deal
                        )
                    ),
            }
        )

    market = latest_market_prices()

    if market:

        market_key = (
            "market:"
            + hashlib.sha256(
                json.dumps(
                    market,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
        )

        result[
            "market_post"
        ] = {
            "key":
                market_key,

            "path":
                str(
                    render_market_post(
                        market
                    )
                ),
        }

    cars = latest_zero_car_prices()

    if cars:

        digest = hashlib.sha256(
            json.dumps(
                cars,
                sort_keys=True,
                ensure_ascii=False,
            ).encode(
                "utf-8"
            )
        ).hexdigest()

        car_key = (
            "cars:"
            + digest
        )

        car_paths = (
            render_car_prices(
                cars
            )
        )

        result[
            "car_price_key"
        ] = car_key

        result[
            "car_price_slides"
        ] = [
            {
                "key":
                    f"{car_key}:{index:02d}",

                "path":
                    str(
                        path
                    ),
            }
            for (
                index,
                path,
            )
            in enumerate(
                car_paths,
                start=1,
            )
        ]

    manifest_path.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status":
                    "built",

                "stage4_version":
                    STAGE4_VERSION,

                "stories":
                    len(
                        result[
                            "stories"
                        ]
                    ),

                "story_kinds":
                    [
                        item[
                            "kind"
                        ]
                        for item
                        in result[
                            "stories"
                        ]
                    ],

                "market_post":
                    bool(
                        result[
                            "market_post"
                        ]
                    ),

                "car_price_slides":
                    len(
                        result[
                            "car_price_slides"
                        ]
                    ),

                "buy_request_story_enabled":
                    False,

                "manifest":
                    str(
                        manifest_path
                    ),
            },
            ensure_ascii=False,
        )
    )

    return result


# ==========================================================
# PERSISTENT PUBLISH MEMORY
# ==========================================================


def load_publish_state() -> dict[
    str,
    Any,
]:

    if not PUBLISH_STATE_PATH.is_file():
        return {
            "version":
                1,

            "published":
                {},
        }

    try:

        data = json.loads(
            PUBLISH_STATE_PATH.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return {
            "version":
                1,

            "published":
                {},
        }

    published = data.get(
        "published"
    )

    if not isinstance(
        published,
        dict,
    ):
        published = {}

    return {
        "version":
            1,

        "published":
            published,
    }


def save_publish_state(
    state: dict[
        str,
        Any,
    ],
) -> None:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    published = state.get(
        "published"
    )

    if not isinstance(
        published,
        dict,
    ):
        published = {}

    if (
        len(
            published
        )
        > MAX_PUBLISH_STATE_ENTRIES
    ):

        ordered = sorted(
            published.items(),
            key=lambda item:
                str(
                    (
                        item[1]
                        or {}
                    ).get(
                        "published_at",
                        "",
                    )
                ),
            reverse=True,
        )

        published = dict(
            ordered[
                :MAX_PUBLISH_STATE_ENTRIES
            ]
        )

    payload = {
        "version":
            1,

        "published":
            published,
    }

    PUBLISH_STATE_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def publication_token(
    channel: str,
    key: str,
) -> str:

    return (
        channel
        + "|"
        + key
    )


def is_published(
    state: dict[
        str,
        Any,
    ],
    channel: str,
    key: str,
) -> bool:

    return (
        publication_token(
            channel,
            key,
        )
        in (
            state.get(
                "published"
            )
            or {}
        )
    )


def mark_published(
    state: dict[
        str,
        Any,
    ],
    channel: str,
    key: str,
    post_id: str,
) -> None:

    state.setdefault(
        "published",
        {},
    )[
        publication_token(
            channel,
            key,
        )
    ] = {
        "channel":
            channel,

        "content_key":
            key,

        "post_id":
            post_id,

        "published_at":
            iso_now(),
    }

    save_publish_state(
        state
    )


# ==========================================================
# MEDIA URL / BUFFER PUBLISH
# ==========================================================


def github_raw_url(
    name: str,
) -> str:

    repository = os.getenv(
        "GITHUB_REPOSITORY",
        "",
    ).strip()

    if not repository:
        raise RuntimeError(
            "GITHUB_REPOSITORY missing"
        )

    return (
        "https://raw.githubusercontent.com/"
        + repository
        + "/"
        + MEDIA_BRANCH
        + "/"
        + urllib.parse.quote(
            name
        )
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
    text: str = "",
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

    result = buffer_call(
        query
    ).get(
        "createPost"
    )

    if not result:
        raise RuntimeError(
            "Buffer createPost empty response"
        )

    if result.get(
        "message"
    ):
        raise RuntimeError(
            result[
                "message"
            ]
        )

    post = result.get(
        "post"
    )

    if not post:
        raise RuntimeError(
            "Buffer did not return post"
        )

    return str(
        post[
            "id"
        ]
    )


def publish_carousel(
    channel_id: str,
    urls: list[str],
    *,
    caption: str,
) -> str:

    if (
        len(
            urls
        )
        < 2
    ):
        raise ValueError(
            "Carousel requires at least two images"
        )

    assets = "\n".join(
        "{ image: { url: "
        + graphql_string(
            url
        )
        + " } }"
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
                carousel

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

    result = buffer_call(
        query
    ).get(
        "createPost"
    )

    if not result:
        raise RuntimeError(
            "Buffer carousel empty response"
        )

    if result.get(
        "message"
    ):
        raise RuntimeError(
            result[
                "message"
            ]
        )

    post = result.get(
        "post"
    )

    if not post:
        raise RuntimeError(
            "Buffer did not return carousel"
        )

    return str(
        post[
            "id"
        ]
    )


def publish_built() -> int:

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if not manifest_path.is_file():
        raise RuntimeError(
            "manifest.json missing"
        )

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    channels = buffer_channels()

    state = load_publish_state()

    for name in TARGET_CHANNELS:

        if name not in channels:
            raise RuntimeError(
                f"Missing Buffer channel: {name}"
            )

    published_now = 0
    skipped_duplicates = 0

    for story in (
        manifest.get(
            "stories"
        )
        or []
    ):

        key = str(
            story[
                "key"
            ]
        )

        url = github_raw_url(
            Path(
                story[
                    "path"
                ]
            ).name
        )

        for channel_name in TARGET_CHANNELS:

            if is_published(
                state,
                channel_name,
                key,
            ):

                skipped_duplicates += 1

                print(
                    json.dumps(
                        {
                            "status":
                                "skipped_duplicate",

                            "type":
                                "story",

                            "channel":
                                channel_name,

                            "source_key":
                                key,
                        },
                        ensure_ascii=False,
                    )
                )

                continue

            post_id = publish_image(
                str(
                    channels[
                        channel_name
                    ][
                        "id"
                    ]
                ),
                url,
                post_type="story",
            )

            mark_published(
                state,
                channel_name,
                key,
                post_id,
            )

            published_now += 1

            print(
                json.dumps(
                    {
                        "status":
                            "published",

                        "type":
                            "story",

                        "kind":
                            story.get(
                                "kind"
                            ),

                        "channel":
                            channel_name,

                        "post_id":
                            post_id,

                        "source_key":
                            key,
                    },
                    ensure_ascii=False,
                )
            )

    market = manifest.get(
        "market_post"
    )

    if market:

        key = str(
            market[
                "key"
            ]
        )

        url = github_raw_url(
            Path(
                market[
                    "path"
                ]
            ).name
        )

        caption = (
            "قیمت لحظه‌ای بازار\n"
            "@select_carr | @select_carrr"
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
                        "id"
                    ]
                ),
                url,
                post_type="post",
                text=caption,
            )

            mark_published(
                state,
                channel_name,
                key,
                post_id,
            )

            published_now += 1

    slides = (
        manifest.get(
            "car_price_slides"
        )
        or []
    )

    if slides:

        key = str(
            manifest.get(
                "car_price_key"
            )
            or slides[
                0
            ][
                "key"
            ]
        )

        urls = [
            github_raw_url(
                Path(
                    slide[
                        "path"
                    ]
                ).name
            )
            for slide
            in slides
        ]

        caption = (
            "قیمت روز خودروهای صفر\n"
            "@select_carr | @select_carrr"
        )

        for channel_name in TARGET_CHANNELS:

            if is_published(
                state,
                channel_name,
                key,
            ):

                skipped_duplicates += 1
                continue

            if (
                len(
                    urls
                )
                == 1
            ):

                post_id = publish_image(
                    str(
                        channels[
                            channel_name
                        ][
                            "id"
                        ]
                    ),
                    urls[
                        0
                    ],
                    post_type="post",
                    text=caption,
                )

            else:

                post_id = publish_carousel(
                    str(
                        channels[
                            channel_name
                        ][
                            "id"
                        ]
                    ),
                    urls,
                    caption=caption,
                )

            mark_published(
                state,
                channel_name,
                key,
                post_id,
            )

            published_now += 1

    print(
        json.dumps(
            {
                "status":
                    "publish_complete",

                "published_now":
                    published_now,

                "skipped_duplicates":
                    skipped_duplicates,

                "buy_request_story_enabled":
                    False,
            },
            ensure_ascii=False,
        )
    )

    return 0


# ==========================================================
# CLI
# ==========================================================


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "check_buffer",
            "validate_templates",
            "build",
            "publish",
        ],
    )

    args = parser.parse_args()

    if (
        args.action
        == "check_buffer"
    ):
        return check_buffer()

    if (
        args.action
        == "validate_templates"
    ):
        return validate_templates()

    if (
        args.action
        == "build"
    ):
        build_content()
        return 0

    if (
        args.action
        == "publish"
    ):
        return publish_built()

    return 2


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
