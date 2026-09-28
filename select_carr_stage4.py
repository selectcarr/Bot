#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display


# ==========================================================
# SELECT CARR - STAGE 4
# ==========================================================
#
# منابع:
#
# 1) فرصت خرید:
#    Select Carr Market Engine -> market_deals
#
# 2) درخواست خرید:
#    فرم Bio -> Lead Intake -> sc_intake_submissions
#
# 3) قیمت خودروهای صفر:
#    پیام‌های کانال Telegram محتوایی Select Carr
#
# 4) طلا / دلار / سکه / اونس:
#    پیام‌های کانال Telegram محتوایی Select Carr
#
# خروجی:
#
# 4 قالب قفل‌شده
# -> Buffer
# -> select_carr
# -> select_carrr
#
# موسیقی و Link Sticker استوری:
# دستی داخل Instagram
#
# ==========================================================


ROOT = Path(__file__).resolve().parent

TARGET_CHANNELS = (
    "select_carr",
    "select_carrr",
)

BUFFER_API = "https://api.buffer.com"

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

ENC_BANK = Path(
    os.getenv(
        "SELECT_CARR_ENCRYPTED_DB",
        ROOT
        / "private_data"
        / "select_carr_banks.sqlite3.enc",
    )
)

DATA_SECRET = os.getenv(
    "SELECT_CARR_DATA_SECRET",
    "",
).strip()

MEDIA_BRANCH = "select-carr-stage4-media"

TEMPLATES = {
    "opportunity":
        ROOT
        / "select_carr_template_opportunity_buy_story.jpeg",

    "buy_request":
        ROOT
        / "select_carr_template_buy_request_story.jpeg",

    "market":
        ROOT
        / "select_carr_template_market_prices_post.jpeg",

    "cars":
        ROOT
        / "select_carr_template_zero_car_prices_post.jpeg",
}

OUTPUT_DIR = ROOT / "stage4_runtime"

STAGE4_VERSION = "1.0.0"


# ==========================================================
# ابزار عمومی
# ==========================================================


def utc_now() -> datetime:
    return datetime.now(
        timezone.utc
    )


def iso_now() -> str:
    return utc_now().isoformat()


def fa_to_en(
    value: object,
) -> str:

    text = str(
        value
        or ""
    )

    return text.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )


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
        return (
            f"{int(value):,}"
        )
    except Exception:
        return str(
            value
        )


def parse_iso(
    value: object,
) -> datetime | None:

    raw = str(
        value
        or ""
    ).strip()

    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(
            raw.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None

    if (
        dt.tzinfo
        is None
    ):
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


# ==========================================================
# فونت
# ==========================================================


def find_font(
    bold: bool = False,
) -> str:

    candidates = []

    if bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansArabic-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )

    else:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        )

    for path in candidates:

        if Path(
            path
        ).exists():

            return path

    raise RuntimeError(
        "Persian font not found"
    )


def font(
    size: int,
    *,
    bold: bool = False,
):
    return ImageFont.truetype(
        find_font(
            bold
        ),
        size,
    )


def rtl(
    text: object,
) -> str:

    raw = str(
        text
        or ""
    )

    if not raw:
        return ""

    shaped = (
        arabic_reshaper
        .reshape(
            raw
        )
    )

    return get_display(
        shaped
    )


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    *,
    maximum: int,
    minimum: int,
    bold: bool = True,
):

    shaped = rtl(
        text
    )

    for size in range(
        maximum,
        minimum - 1,
        -1,
    ):

        fnt = font(
            size,
            bold=bold,
        )

        bbox = (
            draw.textbbox(
                (0, 0),
                shaped,
                font=fnt,
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

        if (
            width
            <= box[2]
            - box[0]
            and height
            <= box[3]
            - box[1]
        ):
            return shaped, fnt

    return (
        shaped,
        font(
            minimum,
            bold=bold,
        ),
    )


def draw_center(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: object,
    *,
    color: str,
    maximum: int,
    minimum: int = 12,
    bold: bool = True,
):

    shaped, fnt = fit_text(
        draw,
        str(
            text
            or ""
        ),
        box,
        maximum=maximum,
        minimum=minimum,
        bold=bold,
    )

    bbox = draw.textbbox(
        (0, 0),
        shaped,
        font=fnt,
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
    )

    y = (
        box[1]
        + (
            box[3]
            - box[1]
            - height
        )
        / 2
    )

    draw.text(
        (x, y),
        shaped,
        font=fnt,
        fill=color,
    )


# ==========================================================
# رمزگشایی بانک Select Carr
# ==========================================================


def fernet() -> Fernet:

    if not DATA_SECRET:
        raise RuntimeError(
            "SELECT_CARR_DATA_SECRET is missing"
        )

    key = (
        base64
        .urlsafe_b64encode(
            hashlib.sha256(
                (
                    "select-carr-bank-v1:"
                    + DATA_SECRET
                ).encode(
                    "utf-8"
                )
            ).digest()
        )
    )

    return Fernet(
        key
    )


def decrypt_bank(
    target: Path,
):

    if not ENC_BANK.exists():
        raise RuntimeError(
            "Encrypted Select Carr bank not found"
        )

    try:

        target.write_bytes(
            fernet().decrypt(
                ENC_BANK.read_bytes()
            )
        )

    except InvalidToken as exc:

        raise RuntimeError(
            "Select Carr bank decrypt failed"
        ) from exc


# ==========================================================
# Buffer
# ==========================================================


def buffer_call(
    query: str,
) -> dict[str, Any]:

    if not BUFFER_API_KEY:
        raise RuntimeError(
            "BUFFER_API_KEY is missing"
        )

    req = urllib.request.Request(
        BUFFER_API,
        data=json.dumps(
            {
                "query":
                    query
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
        req,
        timeout=30,
    ) as response:

        payload = json.loads(
            response.read()
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


def buffer_channels()
    -> dict[str, dict[str, Any]]:

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

    for org in organizations:

        org_id = str(
            org["id"]
        )

        channels = buffer_call(
            f"""
            query {{
              channels(
                input: {{
                  organizationId:
                    "{org_id}"
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
            []
        )

        for channel in channels:

            name = str(
                channel.get(
                    "name",
                    "",
                )
            ).lower()

            if (
                name
                in TARGET_CHANNELS
            ):
                found[
                    name
                ] = channel

    return found


def check_buffer() -> int:

    channels = (
        buffer_channels()
    )

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
                "status":
                    "ok",

                "stage4_version":
                    STAGE4_VERSION,

                "select_carr":
                    True,

                "select_carrr":
                    True,
            },
            ensure_ascii=False,
        )
    )

    return 0


# ==========================================================
# دریافت Deal
# ==========================================================


def latest_market_deal()
    -> dict[str, Any] | None:

    if not MARKET_DB.exists():
        return None

    con = sqlite3.connect(
        MARKET_DB
    )

    con.row_factory = (
        sqlite3.Row
    )

    try:

        row = con.execute(
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
        con.close()


# ==========================================================
# دریافت درخواست خرید
# ==========================================================


def latest_buy_request()
    -> dict[str, Any] | None:

    with tempfile.TemporaryDirectory() as td:

        db_path = (
            Path(
                td
            )
            / "select_carr.sqlite3"
        )

        decrypt_bank(
            db_path
        )

        con = sqlite3.connect(
            db_path
        )

        con.row_factory = (
            sqlite3.Row
        )

        try:

            row = con.execute(
                """
                SELECT *
                FROM sc_intake_submissions
                WHERE
                    request_type='buy'
                    AND status='active'
                ORDER BY
                    created_at DESC
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
            con.close()


# ==========================================================
# Telegram channel data
# ==========================================================


def telegram_messages()
    -> list[str]:

    req = urllib.request.Request(
        CONTENT_CHANNEL_URL,
        headers={
            "User-Agent":
                "Mozilla/5.0",
        },
    )

    with urllib.request.urlopen(
        req,
        timeout=30,
    ) as response:

        raw = (
            response.read()
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

    messages = []

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
        )

        clean = clean.strip()

        if clean:
            messages.append(
                clean
            )

    return messages


def extract_price(
    text: str,
    labels: list[str],
) -> int | None:

    normalized = (
        fa_to_en(
            text
        )
    )

    for label in labels:

        pattern = (
            re.escape(
                label
            )
            + r".{0,30}?"
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


def latest_market_prices()
    -> dict[str, Any] | None:

    for text in reversed(
        telegram_messages()
    ):

        normalized = (
            normalize_space(
                text
            )
        )

        if not (
            "دلار"
            in normalized
            and "طلا"
            in normalized
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
            result["usd"]
            and result["gold18"]
        ):
            return result

    return None


def latest_zero_car_prices()
    -> list[dict[str, Any]]:

    messages = (
        telegram_messages()
    )

    for text in reversed(
        messages
    ):

        normalized = (
            normalize_space(
                text
            )
        )

        if not (
            "قیمت"
            in normalized
            and "خودرو"
            in normalized
        ):
            continue

        lines = [
            line.strip()
            for line
            in text.splitlines()
            if line.strip()
        ]

        items = []

        for line in lines:

            match = re.search(
                r"(.{2,50}?)"
                r"[\s:：\-]+"
                r"([0-9۰-۹٠-٩][0-9۰-۹٠-٩,\.]{5,})",
                line,
            )

            if not match:
                continue

            name = (
                match.group(
                    1
                )
                .strip(
                    " -:|"
                )
            )

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

            if price < 100000000:
                continue

            items.append(
                {
                    "name":
                        name,

                    "variant":
                        "",

                    "price":
                        price,
                }
            )

        if len(
            items
        ) >= 4:
            return items[
                :32
            ]

    return []


# ==========================================================
# Render templates
# ==========================================================


def template(
    name: str,
) -> Image.Image:

    path = (
        TEMPLATES[
            name
        ]
    )

    if not path.exists():

        raise RuntimeError(
            f"Template missing: {path.name}"
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


def scaled_box(
    image: Image.Image,
    reference_size:
        tuple[int, int],
    box:
        tuple[int, int, int, int],
):

    sx = (
        image.width
        / reference_size[
            0
        ]
    )

    sy = (
        image.height
        / reference_size[
            1
        ]
    )

    return (
        int(
            box[0]
            * sx
        ),
        int(
            box[1]
            * sy
        ),
        int(
            box[2]
            * sx
        ),
        int(
            box[3]
            * sy
        ),
    )


def fill_box(
    draw: ImageDraw.ImageDraw,
    box:
        tuple[
            int,
            int,
            int,
            int,
        ],
    color:
        str,
):

    draw.rectangle(
        box,
        fill=color,
    )


def render_opportunity(
    deal: dict[str, Any],
) -> Path:

    img = template(
        "opportunity"
    )

    draw = ImageDraw.Draw(
        img
    )

    ref = (
        817,
        1449,
    )

    title = (
        " ".join(
            [
                str(
                    deal.get(
                        "brand"
                    )
                    or ""
                ),
                str(
                    deal.get(
                        "model"
                    )
                    or ""
                ),
                str(
                    deal.get(
                        "trim"
                    )
                    or ""
                ),
                str(
                    deal.get(
                        "model_year"
                    )
                    or ""
                ),
            ]
        )
        .strip()
    )

    discount = (
        f"{float(deal.get('discount_percent') or 0):.1f}%"
    )

    year = str(
        deal.get(
            "model_year"
        )
        or "-"
    )

    mileage = (
        money(
            deal.get(
                "mileage"
            )
        )
        + " کیلومتر"
    )

    body = str(
        deal.get(
            "body_condition"
        )
        or "تمیز"
    )

    price = money(
        deal.get(
            "candidate_price"
        )
    )

    average = money(
        deal.get(
            "average_price"
        )
    )

    dynamic = [
        (
            (
                543,
                137,
                756,
                203,
            ),
            discount,
            "#ffffff",
            "#e50914",
            34,
        ),
        (
            (
                186,
                910,
                628,
                969,
            ),
            title,
            "#0b0b0b",
            "#ffd56a",
            30,
        ),
        (
            (
                74,
                996,
                199,
                1054,
            ),
            year,
            "#ffffff",
            "#0b0b0b",
            23,
        ),
        (
            (
                215,
                996,
                400,
                1054,
            ),
            mileage,
            "#f4efe6",
            "#0b0b0b",
            21,
        ),
        (
            (
                606,
                996,
                754,
                1054,
            ),
            body,
            "#f4efe6",
            "#0b0b0b",
            21,
        ),
        (
            (
                82,
                1076,
                380,
                1141,
            ),
            price
            + "\nتومان",
            "#ffd56a",
            "#0b0b0b",
            24,
        ),
        (
            (
                438,
                1076,
                738,
                1141,
            ),
            average
            + "\nتومان",
            "#ffd56a",
            "#0b0b0b",
            24,
        ),
    ]

    for (
        raw_box,
        text,
        color,
        bg,
        size,
    ) in dynamic:

        box = scaled_box(
            img,
            ref,
            raw_box,
        )

        fill_box(
            draw,
            box,
            bg,
        )

        draw_center(
            draw,
            box,
            text,
            color=color,
            maximum=size,
            minimum=12,
        )

    path = output_path(
        "story_opportunity.jpeg"
    )

    img.save(
        path,
        quality=95,
    )

    return path


def render_buy_request(
    request_row:
        dict[str, Any],
) -> Path:

    img = template(
        "buy_request"
    )

    draw = ImageDraw.Draw(
        img
    )

    ref = (
        823,
        1443,
    )

    title = str(
        request_row.get(
            "model"
        )
        or "خودرو"
    )

    budget_value = (
        request_row.get(
            "budget"
        )
    )

    budget = (
        "قدرت خرید تا "
        + money(
            budget_value
        )
        + " تومان"
        if budget_value
        else "درخواست خرید فعال"
    )

    vehicle_condition = str(
        request_row.get(
            "vehicle_condition"
        )
        or "سالم"
    )

    urgency = str(
        request_row.get(
            "urgency"
        )
        or "قیمت مناسب"
    )

    dynamic = [
        (
            (
                240,
                867,
                588,
                918,
            ),
            budget,
            "#ffd56a",
            "#0b0b0b",
            24,
        ),
        (
            (
                203,
                947,
                594,
                1000,
            ),
            title,
            "#0b0b0b",
            "#ffd56a",
            29,
        ),
        (
            (
                421,
                1080,
                607,
                1142,
            ),
            vehicle_condition,
            "#f4efe6",
            "#0b0b0b",
            20,
        ),
        (
            (
                617,
                1080,
                772,
                1142,
            ),
            urgency,
            "#f4efe6",
            "#0b0b0b",
            20,
        ),
    ]

    for (
        raw_box,
        text,
        color,
        bg,
        size,
    ) in dynamic:

        box = scaled_box(
            img,
            ref,
            raw_box,
        )

        fill_box(
            draw,
            box,
            bg,
        )

        draw_center(
            draw,
            box,
            text,
            color=color,
            maximum=size,
            minimum=12,
        )

    path = output_path(
        "story_buy_request.jpeg"
    )

    img.save(
        path,
        quality=95,
    )

    return path


def render_market_post(
    values: dict[str, Any],
) -> Path:

    img = template(
        "market"
    )

    draw = ImageDraw.Draw(
        img
    )

    ref = (
        1117,
        1460,
    )

    rows = [
        (
            (
                164,
                895,
                520,
                1016,
            ),
            money(
                values.get(
                    "gold18"
                )
            ),
        ),
        (
            (
                590,
                895,
                948,
                1016,
            ),
            money(
                values.get(
                    "usd"
                )
            ),
        ),
        (
            (
                164,
                1110,
                520,
                1235,
            ),
            money(
                values.get(
                    "coin"
                )
            ),
        ),
        (
            (
                590,
                1110,
                948,
                1235,
            ),
            money(
                values.get(
                    "ounce"
                )
            ),
        ),
    ]

    for raw_box, text in rows:

        box = scaled_box(
            img,
            ref,
            raw_box,
        )

        fill_box(
            draw,
            box,
            "#0b0b0b",
        )

        draw_center(
            draw,
            box,
            text
            + "\nتومان",
            color="#ffffff",
            maximum=34,
            minimum=16,
        )

    path = output_path(
        "post_market.jpeg"
    )

    img.save(
        path,
        quality=95,
    )

    return path


def render_car_prices(
    items:
        list[
            dict[str, Any]
        ],
) -> Path:

    img = template(
        "cars"
    )

    draw = ImageDraw.Draw(
        img
    )

    ref = (
        1178,
        1466,
    )

    slots = []

    left_y = [
        503,
        593,
        683,
        773,
        863,
        953,
        1043,
        1133,
    ]

    right_y = list(
        left_y
    )

    for idx, y in enumerate(
        left_y
    ):

        slots.append(
            (
                idx,
                "left",
                y,
            )
        )

    for idx, y in enumerate(
        right_y
    ):

        slots.append(
            (
                idx + 8,
                "right",
                y,
            )
        )

    for (
        slot_index,
        side,
        y,
    ) in slots:

        if (
            slot_index
            >= len(
                items
            )
        ):
            continue

        item = items[
            slot_index
        ]

        if side == "left":

            name_raw = (
                150,
                y,
                322,
                y + 50,
            )

            price_raw = (
                354,
                y,
                541,
                y + 50,
            )

        else:

            name_raw = (
                687,
                y,
                887,
                y + 50,
            )

            price_raw = (
                906,
                y,
                1095,
                y + 50,
            )

        name_box = scaled_box(
            img,
            ref,
            name_raw,
        )

        price_box = scaled_box(
            img,
            ref,
            price_raw,
        )

        fill_box(
            draw,
            name_box,
            "#0b0b0b",
        )

        fill_box(
            draw,
            price_box,
            "#0b0b0b",
        )

        draw_center(
            draw,
            name_box,
            item.get(
                "name",
                "",
            ),
            color="#ffd56a",
            maximum=22,
            minimum=12,
        )

        draw_center(
            draw,
            price_box,
            money(
                item.get(
                    "price"
                )
            )
            + "\nتومان",
            color="#ffffff",
            maximum=21,
            minimum=11,
        )

    path = output_path(
        "post_car_prices.jpeg"
    )

    img.save(
        path,
        quality=95,
    )

    return path


# ==========================================================
# Media URL
# ==========================================================


def github_raw_url(
    relative_path: str,
) -> str:

    repo = os.getenv(
        "GITHUB_REPOSITORY",
        "",
    ).strip()

    if not repo:
        raise RuntimeError(
            "GITHUB_REPOSITORY missing"
        )

    return (
        "https://raw.githubusercontent.com/"
        + repo
        + "/"
        + MEDIA_BRANCH
        + "/"
        + urllib.parse.quote(
            relative_path
        )
    )


# ==========================================================
# Buffer publish
# ==========================================================


def graphql_string(
    value: str,
) -> str:

    return json.dumps(
        value,
        ensure_ascii=False,
    )


def publish_image(
    channel_id: str,
    image_url: str,
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
                  {graphql_string(image_url)}
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

    assets = "\n".join(
        (
            "{ image: { url: "
            + graphql_string(
                url
            )
            + " } }"
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


# ==========================================================
# Main sync
# ==========================================================


def build_content() -> dict[str, Any]:

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    result = {
        "stories":
            [],

        "post_slides":
            [],
    }

    deal = (
        latest_market_deal()
    )

    if deal:

        result[
            "stories"
        ].append(
            {
                "key":
                    "opportunity:"
                    + str(
                        deal.get(
                            "event_key"
                        )
                        or deal.get(
                            "source_key"
                        )
                    ),

                "path":
                    str(
                        render_opportunity(
                            deal
                        )
                    ),
            }
        )

    buy_request = (
        latest_buy_request()
    )

    if buy_request:

        result[
            "stories"
        ].append(
            {
                "key":
                    "buy:"
                    + str(
                        buy_request.get(
                            "submission_id"
                        )
                    ),

                "path":
                    str(
                        render_buy_request(
                            buy_request
                        )
                    ),
            }
        )

    market = (
        latest_market_prices()
    )

    if market:

        result[
            "post_slides"
        ].append(
            {
                "key":
                    "market:"
                    + hashlib.sha256(
                        json.dumps(
                            market,
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),

                "path":
                    str(
                        render_market_post(
                            market
                        )
                    ),
            }
        )

    cars = (
        latest_zero_car_prices()
    )

    if cars:

        result[
            "post_slides"
        ].append(
            {
                "key":
                    "cars:"
                    + hashlib.sha256(
                        json.dumps(
                            cars,
                            sort_keys=True,
                            ensure_ascii=False,
                        ).encode(
                            "utf-8"
                        )
                    ).hexdigest(),

                "path":
                    str(
                        render_car_prices(
                            cars
                        )
                    ),
            }
        )

    manifest = (
        OUTPUT_DIR
        / "manifest.json"
    )

    manifest.write_text(
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

                "stories":
                    len(
                        result[
                            "stories"
                        ]
                    ),

                "post_slides":
                    len(
                        result[
                            "post_slides"
                        ]
                    ),
            },
            ensure_ascii=False,
        )
    )

    return result


def publish_built() -> int:

    manifest_path = (
        OUTPUT_DIR
        / "manifest.json"
    )

    if not manifest_path.exists():
        raise RuntimeError(
            "manifest.json missing"
        )

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    channels = (
        buffer_channels()
    )

    for name in TARGET_CHANNELS:

        if name not in channels:
            raise RuntimeError(
                f"Missing Buffer channel: {name}"
            )

    for story in manifest[
        "stories"
    ]:

        file_name = (
            Path(
                story[
                    "path"
                ]
            ).name
        )

        url = github_raw_url(
            file_name
        )

        for name in TARGET_CHANNELS:

            post_id = publish_image(
                channels[
                    name
                ][
                    "id"
                ],
                url,
                post_type="story",
            )

            print(
                json.dumps(
                    {
                        "type":
                            "story",

                        "channel":
                            name,

                        "post_id":
                            post_id,

                        "source_key":
                            story[
                                "key"
                            ],
                    },
                    ensure_ascii=False,
                )
            )

    slides = manifest[
        "post_slides"
    ]

    if slides:

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
            "قیمت روز بازار و خودرو\n"
            "@select_carr | @select_carrr"
        )

        for name in TARGET_CHANNELS:

            post_id = publish_carousel(
                channels[
                    name
                ][
                    "id"
                ],
                urls,
                caption=caption,
            )

            print(
                json.dumps(
                    {
                        "type":
                            "carousel",

                        "channel":
                            name,

                        "post_id":
                            post_id,
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
