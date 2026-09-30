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
# Sources:
# 1) Opportunity story: Select Carr Market Engine -> market_deals
# 2) Buy-request story: Bio/Telegram intake -> sc_intake_submissions
# 3) Market-price post: Select Carr content Telegram channel
# 4) Zero-car price carousel: Select Carr content Telegram channel
#
# Locked visual policy:
# - The four template images are immutable backgrounds.
# - Stage 4 only overwrites approved data fields.
# - No real vehicle photo is injected into either story.
# - Opportunity keeps the locked neon vehicle mark.
# - Buy request keeps the locked covered mystery vehicle.
# - Silence about paint/replacement/chassis is treated as healthy per project policy.
#
# Output:
# - 2 story images
# - 1 standalone market-price feed post
# - 1..5 zero-car price slides (16 cars per slide)
# - Buffer -> select_carr + select_carrr
#
# Story music and link sticker remain manual inside Instagram.
# ==========================================================


ROOT = Path(__file__).resolve().parent

TARGET_CHANNELS = (
    "select_carr",
    "select_carrr",
)

BUFFER_API = os.getenv("BUFFER_API_URL", "https://api.buffer.com").strip() or "https://api.buffer.com"
BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "").strip()

CONTENT_CHANNEL_URL = os.getenv(
    "SELECT_CARR_CONTENT_CHANNEL_URL",
    "https://t.me/s/select_carr",
).strip()

MARKET_DB = Path(
    os.getenv(
        "SELECT_CARR_MARKET_DB",
        ROOT / "select_carr_market_runtime" / "select_carr_market.sqlite3",
    )
)

ENC_BANK = Path(
    os.getenv(
        "SELECT_CARR_ENCRYPTED_DB",
        ROOT / "private_data" / "select_carr_banks.sqlite3.enc",
    )
)

DATA_SECRET = os.getenv("SELECT_CARR_DATA_SECRET", "").strip()

MEDIA_BRANCH = os.getenv("SELECT_CARR_STAGE4_MEDIA_BRANCH", "select-carr-stage4-media").strip()

TEMPLATE_DIR = Path(
    os.getenv(
        "STAGE4_TEMPLATE_DIR",
        ROOT / "assets" / "stage4_templates",
    )
)

BRAND_LOGO_DIR = Path(
    os.getenv(
        "STAGE4_BRAND_LOGO_DIR",
        ROOT / "assets" / "stage4_brand_logos",
    )
)

TEMPLATES = {
    "opportunity": TEMPLATE_DIR / "luxury_mercedes_showroom_poster.png",
    "buy_request": TEMPLATE_DIR / "luxury_car_showroom_mystery_offer.png",
    "market": TEMPLATE_DIR / "luxury_gold_market_showroom_board.png",
    "cars": TEMPLATE_DIR / "luxury_gold_car_showroom_price_list.png",
}

# Exact sizes of the four locked files supplied/approved on 2026-09-30.
EXPECTED_TEMPLATE_SIZES = {
    "opportunity": (1010, 1787),
    "buy_request": (916, 1695),
    "market": (1117, 1460),
    "cars": (1178, 1466),
}

OUTPUT_DIR = Path(
    os.getenv(
        "STAGE4_OUTPUT_DIR",
        ROOT / "stage4_runtime",
    )
)

STAGE4_VERSION = "2.0.0"
MAX_ZERO_CAR_SLIDES = 5
ZERO_CARS_PER_SLIDE = 16
MAX_ZERO_CARS = MAX_ZERO_CAR_SLIDES * ZERO_CARS_PER_SLIDE


# ==========================================================
# General helpers
# ==========================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def fa_to_en(value: object) -> str:
    return str(value or "").translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )


def normalize_space(value: object) -> str:
    return re.sub(r"\s+", " ", fa_to_en(value)).strip()


def money(value: object) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return str(value)


def compact_money(value: object) -> str:
    try:
        n = int(value)
    except Exception:
        return money(value)
    if n >= 1_000_000_000:
        x = n / 1_000_000_000
        s = f"{x:.1f}".rstrip("0").rstrip(".")
        return f"{s} میلیارد"
    if n >= 1_000_000:
        x = n / 1_000_000
        s = f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}".rstrip("0").rstrip(".")
        return f"{s} میلیون"
    return f"{n:,}"


def parse_iso(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def safe_filename_token(value: object) -> str:
    s = normalize_space(value).casefold()
    s = s.replace(" ", "_").replace("-", "_")
    return re.sub(r"[^\w\u0600-\u06ff]+", "", s, flags=re.UNICODE).strip("_")


def format_discount(value: object) -> str:
    try:
        n = float(value)
    except Exception:
        return "-"
    if abs(n - round(n)) < 0.05:
        return f"{int(round(n))}%"
    return f"{n:.1f}%"


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


def body_label(value: object) -> str:
    raw = str(value or "").strip()
    return BODY_LABELS.get(raw, raw or "سالم")


def technical_label(row: dict[str, Any]) -> str:
    explicit = str(row.get("mechanical_status") or "").strip()
    if explicit:
        return explicit
    # Project policy: silence about condition axes is healthy.
    return "فنی سالم"


def extract_year_range(*values: object) -> str:
    text = " ".join(fa_to_en(v) for v in values if str(v or "").strip())
    years = []
    for token in re.findall(r"(?<!\d)(?:13\d{2}|14\d{2}|19\d{2}|20\d{2})(?!\d)", text):
        if token not in years:
            years.append(token)
    if len(years) >= 2:
        return f"{years[0]} تا {years[1]}"
    if len(years) == 1:
        return years[0]
    return "ذکر نشده"


def infer_brand(text: object) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    try:
        from accurate_average_collectors import extract_vehicle_identity
        brand, _model, _trim = extract_vehicle_identity(raw)
        if brand:
            return str(brand)
    except Exception:
        pass

    # Safe fallback for display only; no pricing or matching decision depends on it.
    known = [
        "Mercedes-Benz", "Mercedes Benz", "BMW", "Porsche", "Toyota", "Lexus",
        "Hyundai", "Kia", "Peugeot", "Renault", "Saipa", "Iran Khodro",
        "Chery", "KMC", "Lamari", "Mazda", "Nissan", "Mitsubishi", "Honda",
        "مرسدس بنز", "بی ام و", "پورشه", "تویوتا", "لکسوس", "هیوندای", "کیا",
        "پژو", "رنو", "سایپا", "ایران خودرو", "چری", "کی ام سی", "لاماری",
        "مزدا", "نیسان", "میتسوبیشی", "هوندا",
    ]
    low = raw.casefold()
    for item in known:
        if item.casefold() in low:
            return item
    return raw.split()[0] if raw.split() else ""


# ==========================================================
# Font + RTL drawing
# ==========================================================


def find_font(bold: bool = False) -> str:
    env_key = "STAGE4_FONT_BOLD" if bold else "STAGE4_FONT_REGULAR"
    candidates = [
        os.getenv(env_key, "").strip(),
    ]
    if bold:
        candidates.extend([
            str(ROOT / "assets" / "fonts" / "Vazirmatn-Bold.ttf"),
            str(ROOT / "assets" / "fonts" / "Vazirmatn-SemiBold.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansArabic-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ])
    else:
        candidates.extend([
            str(ROOT / "assets" / "fonts" / "Vazirmatn-Regular.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Persian font not found")


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(find_font(bold=bold), max(8, int(size)))


def rtl(text: object) -> str:
    raw = str(text or "")
    if not raw:
        return ""
    return "\n".join(
        get_display(arabic_reshaper.reshape(line)) if line.strip() else ""
        for line in raw.splitlines()
    )


def fit_text(
    draw: ImageDraw.ImageDraw,
    text: object,
    box: tuple[int, int, int, int],
    *,
    maximum: int,
    minimum: int = 10,
    bold: bool = True,
    spacing: int = 4,
) -> tuple[str, ImageFont.FreeTypeFont]:
    shaped = rtl(text)
    for size in range(maximum, minimum - 1, -1):
        fnt = font(size, bold=bold)
        bbox = draw.multiline_textbbox((0, 0), shaped, font=fnt, spacing=spacing, align="center")
        if bbox[2] - bbox[0] <= box[2] - box[0] and bbox[3] - bbox[1] <= box[3] - box[1]:
            return shaped, fnt
    return shaped, font(minimum, bold=bold)


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
    shaped, fnt = fit_text(
        draw,
        text,
        box,
        maximum=maximum,
        minimum=minimum,
        bold=bold,
        spacing=spacing,
    )
    bbox = draw.multiline_textbbox((0, 0), shaped, font=fnt, spacing=spacing, align="center")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = box[0] + ((box[2] - box[0]) - width) / 2 - bbox[0]
    y = box[1] + ((box[3] - box[1]) - height) / 2 - bbox[1]
    draw.multiline_text(
        (x, y),
        shaped,
        font=fnt,
        fill=color,
        spacing=spacing,
        align="center",
    )


def fill_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], color: str) -> None:
    draw.rectangle(box, fill=color)


def erase_region(
    img: Image.Image,
    box: tuple[int, int, int, int],
    *,
    edge_width: int = 4,
) -> None:
    """Erase baked sample text while approximating the local horizontal background."""
    x0, y0, x1, y1 = box
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(img.width, x1)
    y1 = min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    px = img.load()
    for y in range(y0, y1):
        left_samples = [
            px[x, y]
            for x in range(max(0, x0 - edge_width), x0)
        ]
        right_samples = [
            px[x, y]
            for x in range(x1, min(img.width, x1 + edge_width))
        ]
        if not left_samples:
            left_samples = [px[x0, y]]
        if not right_samples:
            right_samples = [px[x1 - 1, y]]
        left = tuple(sum(c[i] for c in left_samples) // len(left_samples) for i in range(3))
        right = tuple(sum(c[i] for c in right_samples) // len(right_samples) for i in range(3))
        span = max(1, x1 - x0 - 1)
        for x in range(x0, x1):
            t = (x - x0) / span
            px[x, y] = tuple(int(left[i] * (1 - t) + right[i] * t) for i in range(3))


def scaled_box(
    image: Image.Image,
    reference_size: tuple[int, int],
    box: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    sx = image.width / reference_size[0]
    sy = image.height / reference_size[1]
    return (
        int(round(box[0] * sx)),
        int(round(box[1] * sy)),
        int(round(box[2] * sx)),
        int(round(box[3] * sy)),
    )


def paste_contain(
    base: Image.Image,
    source: Path,
    box: tuple[int, int, int, int],
) -> None:
    logo = Image.open(source).convert("RGBA")
    bw = max(1, box[2] - box[0])
    bh = max(1, box[3] - box[1])
    ratio = min(bw / logo.width, bh / logo.height)
    size = (max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio)))
    logo = logo.resize(size, Image.LANCZOS)
    x = box[0] + (bw - logo.width) // 2
    y = box[1] + (bh - logo.height) // 2
    if base.mode != "RGBA":
        rgba = base.convert("RGBA")
        rgba.alpha_composite(logo, (x, y))
        base.paste(rgba.convert(base.mode))
    else:
        base.alpha_composite(logo, (x, y))


def find_brand_logo(brand: object) -> Path | None:
    if not BRAND_LOGO_DIR.is_dir():
        return None
    raw = str(brand or "").strip()
    if not raw:
        return None
    names = [
        raw,
        safe_filename_token(raw),
        raw.replace(" ", "_"),
        raw.replace(" ", "-"),
    ]
    extensions = (".png", ".webp", ".jpg", ".jpeg")
    for name in names:
        for ext in extensions:
            p = BRAND_LOGO_DIR / (name + ext)
            if p.is_file():
                return p
    return None


def paint_brand_badge(
    img: Image.Image,
    box: tuple[int, int, int, int],
    brand: object,
    *,
    background: str = "#F5B942",
) -> None:
    draw = ImageDraw.Draw(img)
    erase_region(img, box)
    draw = ImageDraw.Draw(img)
    logo = find_brand_logo(brand)
    if logo:
        inset = 6
        paste_contain(img, logo, (box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset))
        return

    # Safe fallback: never leave the sample Mercedes emblem on another brand.
    # A simple circular text badge preserves the locked geometry without inventing a logo.
    pad = 5
    draw.ellipse(
        (box[0] + pad, box[1] + pad, box[2] - pad, box[3] - pad),
        outline="#0B0B0B",
        width=3,
    )
    raw = str(brand or "SC").strip()
    token = "".join(part[:1] for part in re.split(r"[\s_-]+", raw) if part)[:3] or "SC"
    draw_center(
        draw,
        box,
        token.upper(),
        color="#0B0B0B",
        maximum=20,
        minimum=10,
        bold=True,
    )


# ==========================================================
# Select Carr encrypted bank
# ==========================================================


def fernet() -> Fernet:
    if not DATA_SECRET:
        raise RuntimeError("SELECT_CARR_DATA_SECRET is missing")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(("select-carr-bank-v1:" + DATA_SECRET).encode("utf-8")).digest()
    )
    return Fernet(key)


def decrypt_bank(target: Path) -> None:
    if not ENC_BANK.exists():
        raise RuntimeError("Encrypted Select Carr bank not found")
    try:
        target.write_bytes(fernet().decrypt(ENC_BANK.read_bytes()))
    except InvalidToken as exc:
        raise RuntimeError("Select Carr bank decrypt failed") from exc


# ==========================================================
# Buffer
# ==========================================================


def buffer_call(query: str) -> dict[str, Any]:
    if not BUFFER_API_KEY:
        raise RuntimeError("BUFFER_API_KEY is missing")
    req = urllib.request.Request(
        BUFFER_API,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BUFFER_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], ensure_ascii=False))
    return payload.get("data") or {}


def buffer_channels() -> dict[str, dict[str, Any]]:
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
    organizations = (data.get("account") or {}).get("organizations") or []
    found: dict[str, dict[str, Any]] = {}
    for org in organizations:
        org_id = str(org["id"])
        channels = buffer_call(
            f"""
            query {{
              channels(input: {{ organizationId: "{org_id}" }}) {{
                id
                name
                displayName
                service
                isDisconnected
                isLocked
              }}
            }}
            """
        ).get("channels") or []
        for channel in channels:
            name = str(channel.get("name") or "").strip().lower()
            if name in TARGET_CHANNELS:
                found[name] = dict(channel)
    return found


def check_buffer() -> int:
    channels = buffer_channels()
    missing = [name for name in TARGET_CHANNELS if name not in channels]
    if missing:
        raise RuntimeError("Missing Buffer channels: " + ", ".join(missing))
    for name in TARGET_CHANNELS:
        row = channels[name]
        if str(row.get("service") or "").lower() != "instagram":
            raise RuntimeError(f"{name} is not Instagram")
        if row.get("isDisconnected") is True:
            raise RuntimeError(f"{name} is disconnected")
        if row.get("isLocked") is True:
            raise RuntimeError(f"{name} is locked")
    print(
        json.dumps(
            {
                "status": "ok",
                "stage4_version": STAGE4_VERSION,
                "select_carr": True,
                "select_carrr": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


# ==========================================================
# Template validation
# ==========================================================


def validate_templates() -> int:
    report = []
    for key, path in TEMPLATES.items():
        if not path.is_file():
            raise RuntimeError(f"Template missing: {path}")
        try:
            with Image.open(path) as im:
                size = tuple(im.size)
                fmt = im.format
                im.verify()
        except Exception as exc:
            raise RuntimeError(f"Template unreadable: {path}") from exc
        expected = EXPECTED_TEMPLATE_SIZES[key]
        if size != expected:
            raise RuntimeError(
                f"Locked template size mismatch for {key}: got={size}, expected={expected}"
            )
        report.append({
            "template": key,
            "file": str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path),
            "size": list(size),
            "format": fmt,
        })
    print(json.dumps({"status": "ok", "templates": 4, "details": report}, ensure_ascii=False, indent=2))
    return 0


# ==========================================================
# Data: latest deal
# ==========================================================


def latest_market_deal() -> dict[str, Any] | None:
    if not MARKET_DB.exists():
        return None
    con = sqlite3.connect(MARKET_DB)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT *
            FROM market_deals
            WHERE status IN ('sent', 'suppressed_system_b')
            ORDER BY
                COALESCE(sent_at, queued_at, candidate_seen_at) DESC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


# ==========================================================
# Data: latest buy request
# ==========================================================


def latest_buy_request() -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "select_carr.sqlite3"
        decrypt_bank(db_path)
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                """
                SELECT *
                FROM sc_intake_submissions
                WHERE request_type='buy'
                  AND status='active'
                  AND (expires_at IS NULL OR julianday(expires_at) > julianday(?))
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (iso_now(),),
            ).fetchone()
            return dict(row) if row else None
        finally:
            con.close()


# ==========================================================
# Data: Telegram content channel
# ==========================================================


def telegram_messages() -> list[str]:
    req = urllib.request.Request(
        CONTENT_CHANNEL_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="ignore")

    blocks = re.findall(
        r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
        raw,
        flags=re.I | re.S,
    )
    messages = []
    for block in blocks:
        clean = re.sub(r"<br\s*/?>", "\n", block, flags=re.I)
        clean = re.sub(r"<[^>]+>", "", clean)
        clean = html.unescape(clean).strip()
        if clean:
            messages.append(clean)
    return messages


def extract_price(text: str, labels: list[str]) -> int | None:
    normalized = fa_to_en(text)
    for label in labels:
        pattern = re.escape(label) + r".{0,40}?([0-9][0-9,\.]{2,})"
        match = re.search(pattern, normalized, flags=re.I | re.S)
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)
    return None


def latest_market_prices() -> dict[str, Any] | None:
    for text in reversed(telegram_messages()):
        normalized = normalize_space(text)
        if "دلار" not in normalized or "طلا" not in normalized:
            continue
        result = {
            "usd": extract_price(text, ["دلار آزاد", "دلار"]),
            "gold18": extract_price(text, ["طلای 18", "طلای ۱۸", "طلا 18", "طلا ۱۸"]),
            "coin": extract_price(text, ["سکه امامی", "سکه"]),
            "ounce": extract_price(text, ["اونس", "انس"]),
        }
        if result["usd"] and result["gold18"]:
            return result
    return None


PERSIAN_MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)


def extract_date_label(text: str) -> str:
    normalized = fa_to_en(text)
    months = "|".join(map(re.escape, PERSIAN_MONTHS))
    m = re.search(rf"({months})\s*(1[34]\d{{2}})", normalized)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m = re.search(rf"(1[34]\d{{2}})\s*({months})", normalized)
    if m:
        return f"{m.group(2)} {m.group(1)}"
    return "قیمت روز"


def split_name_variant(value: str) -> tuple[str, str]:
    raw = normalize_space(value)
    for sep in (" | ", " / ", " - ", " – ", " — "):
        if sep in raw:
            left, right = raw.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    m = re.match(
        r"^(.*?)(\s+(?:تیپ\s+\S+|دنده(?:ای|‌ای)|اتوماتیک|پلاس|پرومکس|پریمیوم|V\d+|G|S|LX|EF7))$",
        raw,
        flags=re.I,
    )
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).strip()
    return raw, ""


def latest_zero_car_prices() -> dict[str, Any] | None:
    for text in reversed(telegram_messages()):
        normalized = normalize_space(text)
        if "قیمت" not in normalized or "خودرو" not in normalized:
            continue

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        items: list[dict[str, Any]] = []
        for line in lines:
            match = re.search(
                r"(.{2,80}?)[\s:：\-–—]+([0-9۰-۹٠-٩][0-9۰-۹٠-٩,\.]{5,})",
                line,
            )
            if not match:
                continue
            label = match.group(1).strip(" -:|–—")
            price_digits = re.sub(r"\D", "", fa_to_en(match.group(2)))
            if not price_digits:
                continue
            price = int(price_digits)
            if price < 100_000_000:
                continue
            name, variant = split_name_variant(label)
            items.append({
                "name": name,
                "variant": variant,
                "price": price,
            })
            if len(items) >= MAX_ZERO_CARS:
                break

        if len(items) >= 4:
            return {
                "date_label": extract_date_label(text),
                "items": items,
            }

    return None


# ==========================================================
# Templates + output
# ==========================================================


def template(name: str) -> Image.Image:
    path = TEMPLATES[name]
    if not path.exists():
        raise RuntimeError(f"Template missing: {path}")
    return Image.open(path).convert("RGB")


def output_path(name: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / name


# ==========================================================
# Render: opportunity story
# ==========================================================


def render_opportunity(deal: dict[str, Any]) -> Path:
    img = template("opportunity")
    draw = ImageDraw.Draw(img)

    title = " ".join(
        str(x or "").strip()
        for x in (
            deal.get("brand"),
            deal.get("model"),
            deal.get("trim"),
            deal.get("model_year"),
        )
        if str(x or "").strip()
    )

    brand = str(deal.get("brand") or "").strip()
    discount = format_discount(deal.get("discount_percent"))
    year = str(deal.get("model_year") or "-")
    mileage = "نامشخص" if deal.get("mileage") is None else f"{money(deal.get('mileage'))}\nکیلومتر"
    technical = technical_label(deal)
    body = body_label(deal.get("body_condition"))
    price = money(deal.get("candidate_price"))
    average = money(deal.get("average_price"))

    # Locked 1010x1787 coordinates. Only baked sample data is erased.
    percent_box = (680, 151, 782, 211)
    fill_box(draw, percent_box, "#E50914")
    draw_center(draw, percent_box, discount, color="#FFFFFF", maximum=31, minimum=18, bold=True)

    paint_brand_badge(img, (71, 1020, 164, 1091), brand)
    draw = ImageDraw.Draw(img)

    # Gold name bar: reconstruct local gradient. Black cards: use their native flat black.
    title_box = (180, 1028, 686, 1086)
    erase_region(img, title_box)
    draw = ImageDraw.Draw(img)
    draw_center(draw, title_box, title, color="#0B0B0B", maximum=31, minimum=15, bold=True)

    card_fields = [
        ((132, 1134, 245, 1181), year, "#FFFFFF", 24, 13),
        ((322, 1128, 484, 1183), mileage, "#F4EFE6", 20, 11),
        ((546, 1133, 706, 1182), technical, "#F4EFE6", 20, 11),
        ((785, 1133, 932, 1182), body, "#F4EFE6", 20, 11),
    ]
    for box, text, color, maximum, minimum in card_fields:
        fill_box(draw, box, "#0B0B0B")
        draw_center(draw, box, text, color=color, maximum=maximum, minimum=minimum, bold=True)

    # Replace numbers only; keep the locked titles and "تومان" labels.
    price_fields = [
        ((155, 1220, 414, 1261), price),
        ((594, 1220, 855, 1261), average),
    ]
    for box, text in price_fields:
        fill_box(draw, box, "#0B0B0B")
        draw_center(draw, box, text, color="#FFD56A", maximum=27, minimum=14, bold=True)

    path = output_path("story_opportunity.jpeg")
    img.save(path, "JPEG", quality=95, subsampling=0)
    return path


# ==========================================================
# Render: buy-request story
# ==========================================================


def request_condition_text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("vehicle_condition") or "").strip(),
        str(row.get("body_condition") or "").strip(),
        str(row.get("mechanical_status") or "").strip(),
    ]
    parts = [p for p in parts if p]
    if not parts:
        return "سالم"
    return " / ".join(dict.fromkeys(parts))


def request_priority_text(row: dict[str, Any]) -> str:
    return (
        str(row.get("urgency") or "").strip()
        or str(row.get("purpose") or "").strip()
        or "قیمت مناسب"
    )


def render_buy_request(request_row: dict[str, Any]) -> Path:
    img = template("buy_request")
    draw = ImageDraw.Draw(img)

    title = str(request_row.get("model") or "خودرو").strip()
    brand = infer_brand(title)

    budget_value = request_row.get("budget")
    budget = (
        f"قدرت خرید تا {compact_money(budget_value)}"
        if budget_value
        else "درخواست خرید فعال"
    )

    year_pref = extract_year_range(
        request_row.get("model"),
        request_row.get("description"),
    )

    mileage_value = request_row.get("mileage")
    mileage_pref = (
        f"تا {money(mileage_value)} کیلومتر"
        if mileage_value not in (None, "")
        else "ذکر نشده"
    )

    condition_pref = request_condition_text(request_row)
    priority_pref = request_priority_text(request_row)

    budget_box = (260, 989, 657, 1042)
    fill_box(draw, budget_box, "#0B0B0B")
    draw_center(draw, budget_box, budget, color="#FFD56A", maximum=24, minimum=13, bold=True)

    paint_brand_badge(img, (76, 1066, 202, 1193), brand)
    draw = ImageDraw.Draw(img)

    # Gold bar text areas are reconstructed from their local background.
    for box, text, maximum in [
        ((224, 1083, 620, 1144), title, 29),
        ((270, 1144, 593, 1190), year_pref, 23),
    ]:
        erase_region(img, box)
        draw = ImageDraw.Draw(img)
        draw_center(draw, box, text, color="#0B0B0B", maximum=maximum, minimum=12, bold=True)

    card_fields = [
        ((126, 1244, 226, 1290), year_pref),
        ((321, 1242, 442, 1292), mileage_pref),
        ((538, 1242, 649, 1292), condition_pref),
        ((744, 1242, 842, 1292), priority_pref),
    ]
    for box, text in card_fields:
        fill_box(draw, box, "#0B0B0B")
        draw_center(draw, box, text, color="#F4EFE6", maximum=17, minimum=9, bold=True)

    path = output_path("story_buy_request.jpeg")
    img.save(path, "JPEG", quality=95, subsampling=0)
    return path


# ==========================================================
# Render: market-price post
# ==========================================================


def render_market_post(values: dict[str, Any]) -> Path:
    img = template("market")
    draw = ImageDraw.Draw(img)

    # Replace the complete value+unit zone so no baked sample digits remain.
    value_boxes = {
        "gold18": (180, 998, 500, 1070),
        "usd": (620, 998, 947, 1070),
        "coin": (180, 1228, 500, 1290),
        "ounce": (620, 1228, 947, 1290),
    }

    for key, box in value_boxes.items():
        fill_box(draw, box, "#0B0B0B")
        draw_center(
            draw,
            box,
            money(values.get(key)) + "\nتومان",
            color="#FFFFFF",
            maximum=31,
            minimum=15,
            bold=True,
            spacing=1,
        )

    path = output_path("post_market.jpeg")
    img.save(path, "JPEG", quality=95, subsampling=0)
    return path


# ==========================================================
# Render: zero-car price carousel
# ==========================================================


def chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def render_car_prices(data: dict[str, Any]) -> list[Path]:
    all_items = list(data.get("items") or [])[:MAX_ZERO_CARS]
    if not all_items:
        return []

    pages = chunks(all_items, ZERO_CARS_PER_SLIDE)
    paths: list[Path] = []

    # Exact row bands detected from the approved 1178x1466 locked template.
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

    for page_number, page_items in enumerate(pages, start=1):
        img = template("cars")
        draw = ImageDraw.Draw(img)

        # Repaint the date text area (icon remains untouched).
        date_area = (108, 101, 238, 181)
        fill_box(draw, date_area, "#0B0B0B")
        draw_center(
            draw,
            (111, 105, 235, 145),
            str(data.get("date_label") or "قیمت روز"),
            color="#FFFFFF",
            maximum=19,
            minimum=10,
            bold=True,
        )
        draw_center(
            draw,
            (111, 145, 235, 177),
            "قیمت ها به تومان",
            color="#F4EFE6",
            maximum=13,
            minimum=9,
            bold=False,
        )

        # Repaint all 16 row cards on the exact locked grid.
        for slot in range(ZERO_CARS_PER_SLIDE):
            side = "left" if slot < 8 else "right"
            band = row_bands[slot if slot < 8 else slot - 8]
            y0, y1 = band
            item = page_items[slot] if slot < len(page_items) else None
            global_index = (page_number - 1) * ZERO_CARS_PER_SLIDE + slot + 1

            if side == "left":
                panel = (69, y0, 573, y1)
                index_box = (83, y0 + 17, 132, min(y1 - 12, y0 + 60))
                name_box = (151, y0 + 11, 318, min(y1 - 34, y0 + 38))
                variant_box = (151, y0 + 40, 318, y1 - 10)
                separator_x = 330
                price_box = (351, y0 + 12, 540, min(y1 - 31, y0 + 42))
                toman_box = (382, y0 + 44, 516, y1 - 9)
            else:
                panel = (604, y0, 1107, y1)
                index_box = (618, y0 + 17, 667, min(y1 - 12, y0 + 60))
                name_box = (688, y0 + 11, 858, min(y1 - 34, y0 + 38))
                variant_box = (688, y0 + 40, 858, y1 - 10)
                separator_x = 873
                price_box = (897, y0 + 12, 1087, min(y1 - 31, y0 + 42))
                toman_box = (932, y0 + 44, 1065, y1 - 9)

            draw.rounded_rectangle(
                panel,
                radius=14,
                fill="#0B0B0B",
                outline="#C88A16",
                width=2,
            )
            draw.line(
                (separator_x, y0 + 10, separator_x, y1 - 10),
                fill="#C88A16",
                width=2,
            )

            if item is None:
                continue

            draw.rounded_rectangle(index_box, radius=7, fill="#F5B942")
            draw_center(
                draw,
                index_box,
                f"{global_index:02d}",
                color="#0B0B0B",
                maximum=17,
                minimum=10,
                bold=True,
            )
            draw_center(
                draw,
                name_box,
                item.get("name", ""),
                color="#F5B942",
                maximum=20,
                minimum=10,
                bold=True,
            )
            draw_center(
                draw,
                variant_box,
                item.get("variant", ""),
                color="#F4EFE6",
                maximum=15,
                minimum=9,
                bold=False,
            )
            draw_center(
                draw,
                price_box,
                money(item.get("price")),
                color="#FFFFFF",
                maximum=19,
                minimum=10,
                bold=True,
            )
            draw_center(
                draw,
                toman_box,
                "تومان",
                color="#F4EFE6",
                maximum=13,
                minimum=9,
                bold=False,
            )

        # Page indicator text area; arrows remain from the locked template.
        page_area = (914, 1287, 1042, 1341)
        fill_box(draw, page_area, "#0B0B0B")
        draw_center(
            draw,
            page_area,
            f"{page_number} از {len(pages)}",
            color="#FFFFFF",
            maximum=19,
            minimum=10,
            bold=True,
        )

        path = output_path(f"post_car_prices_{page_number:02d}.jpeg")
        img.save(path, "JPEG", quality=95, subsampling=0)
        paths.append(path)

    return paths


# ==========================================================
# Build manifest
# ==========================================================


def build_content() -> dict[str, Any]:
    validate_templates()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Remove stale Stage 4 images so a shorter new carousel cannot inherit old slides.
    for old in OUTPUT_DIR.glob("*.jpeg"):
        old.unlink()

    result: dict[str, Any] = {
        "version": STAGE4_VERSION,
        "generated_at": iso_now(),
        "stories": [],
        "market_post": None,
        "car_price_slides": [],
    }

    deal = latest_market_deal()
    if deal:
        result["stories"].append({
            "key": "opportunity:" + str(deal.get("event_key") or deal.get("source_key") or ""),
            "path": str(render_opportunity(deal)),
        })

    buy_request = latest_buy_request()
    if buy_request:
        result["stories"].append({
            "key": "buy:" + str(buy_request.get("submission_id") or ""),
            "path": str(render_buy_request(buy_request)),
        })

    market = latest_market_prices()
    if market:
        result["market_post"] = {
            "key": "market:" + hashlib.sha256(
                json.dumps(market, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "path": str(render_market_post(market)),
        }

    cars = latest_zero_car_prices()
    if cars:
        car_paths = render_car_prices(cars)
        source_hash = hashlib.sha256(
            json.dumps(cars, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        result["car_price_slides"] = [
            {
                "key": f"cars:{source_hash}:{index:02d}",
                "path": str(path),
            }
            for index, path in enumerate(car_paths, start=1)
        ]

    manifest = OUTPUT_DIR / "manifest.json"
    manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": "built",
                "stage4_version": STAGE4_VERSION,
                "stories": len(result["stories"]),
                "market_post": bool(result["market_post"]),
                "car_price_slides": len(result["car_price_slides"]),
                "manifest": str(manifest),
            },
            ensure_ascii=False,
        )
    )

    return result


# ==========================================================
# Public media URL
# ==========================================================


def github_raw_url(relative_path: str) -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY missing")
    return (
        "https://raw.githubusercontent.com/"
        + repo
        + "/"
        + MEDIA_BRANCH
        + "/"
        + urllib.parse.quote(relative_path)
    )


# ==========================================================
# Buffer publish
# ==========================================================


def graphql_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


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
          text: {graphql_string(text)}
          channelId: {graphql_string(channel_id)}
          schedulingType: automatic
          mode: shareNow
          assets: [
            {{
              image: {{
                url: {graphql_string(image_url)}
              }}
            }}
          ]
          metadata: {{
            instagram: {{
              type: {post_type}
              shouldShareToFeed: {"false" if post_type == "story" else "true"}
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
    result = buffer_call(query).get("createPost")
    if not result:
        raise RuntimeError("Buffer createPost empty response")
    if result.get("message"):
        raise RuntimeError(result["message"])
    post = result.get("post")
    if not post:
        raise RuntimeError("Buffer did not return post")
    return str(post["id"])


def publish_carousel(
    channel_id: str,
    urls: list[str],
    *,
    caption: str,
) -> str:
    if len(urls) < 2:
        raise ValueError("Carousel requires at least two images")
    assets = "\n".join(
        "{ image: { url: " + graphql_string(url) + " } }"
        for url in urls
    )
    query = f"""
    mutation {{
      createPost(
        input: {{
          text: {graphql_string(caption)}
          channelId: {graphql_string(channel_id)}
          schedulingType: automatic
          mode: shareNow
          assets: [
            {assets}
          ]
          metadata: {{
            instagram: {{
              type: carousel
              shouldShareToFeed: true
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
    result = buffer_call(query).get("createPost")
    if not result:
        raise RuntimeError("Buffer carousel empty response")
    if result.get("message"):
        raise RuntimeError(result["message"])
    post = result.get("post")
    if not post:
        raise RuntimeError("Buffer did not return carousel")
    return str(post["id"])


def publish_built() -> int:
    manifest_path = OUTPUT_DIR / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("manifest.json missing")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    channels = buffer_channels()

    for name in TARGET_CHANNELS:
        if name not in channels:
            raise RuntimeError(f"Missing Buffer channel: {name}")

    # 1) Stories are published independently.
    for story in manifest.get("stories") or []:
        file_name = Path(story["path"]).name
        url = github_raw_url(file_name)
        for name in TARGET_CHANNELS:
            post_id = publish_image(
                str(channels[name]["id"]),
                url,
                post_type="story",
            )
            print(json.dumps({
                "type": "story",
                "channel": name,
                "post_id": post_id,
                "source_key": story["key"],
            }, ensure_ascii=False))

    # 2) Market prices are one independent feed post.
    market_post = manifest.get("market_post")
    if market_post:
        url = github_raw_url(Path(market_post["path"]).name)
        caption = "قیمت لحظه‌ای بازار\n@select_carr | @select_carrr"
        for name in TARGET_CHANNELS:
            post_id = publish_image(
                str(channels[name]["id"]),
                url,
                post_type="post",
                text=caption,
            )
            print(json.dumps({
                "type": "market_post",
                "channel": name,
                "post_id": post_id,
                "source_key": market_post["key"],
            }, ensure_ascii=False))

    # 3) Zero-car prices are one independent post/carousel, 1..5 slides.
    car_slides = manifest.get("car_price_slides") or []
    if car_slides:
        urls = [
            github_raw_url(Path(slide["path"]).name)
            for slide in car_slides
        ]
        caption = "قیمت روز خودروهای صفر\n@select_carr | @select_carrr"
        for name in TARGET_CHANNELS:
            if len(urls) == 1:
                post_id = publish_image(
                    str(channels[name]["id"]),
                    urls[0],
                    post_type="post",
                    text=caption,
                )
            else:
                post_id = publish_carousel(
                    str(channels[name]["id"]),
                    urls,
                    caption=caption,
                )
            print(json.dumps({
                "type": "zero_car_prices",
                "slides": len(urls),
                "channel": name,
                "post_id": post_id,
            }, ensure_ascii=False))

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

    if args.action == "check_buffer":
        return check_buffer()
    if args.action == "validate_templates":
        return validate_templates()
    if args.action == "build":
        build_content()
        return 0
    if args.action == "publish":
        return publish_built()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
