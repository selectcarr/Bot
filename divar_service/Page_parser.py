from __future__ import annotations

import re

from divar_service.models import VehicleAd


# -----------------------------
# regex patterns
# -----------------------------
YEAR_PATTERN = re.compile(r"(13\d{2}|14\d{2})")
MILEAGE_PATTERN = re.compile(
    r"(\d{1,3}(?:[,\s]\d{3})+|\d+)\s*(?:km|کیلومتر)?",
    re.IGNORECASE,
)


# -----------------------------
# تمیزسازی متن
# -----------------------------
def normalize(text: str) -> str:
    return (
        text.replace("ي", "ی")
        .replace("ك", "ک")
        .lower()
        .strip()
    )


# -----------------------------
# استخراج سال
# -----------------------------
def extract_year(title: str) -> int | None:
    match = YEAR_PATTERN.search(title)
    if match:
        return int(match.group(1))
    return None


# -----------------------------
# استخراج کارکرد
# -----------------------------
def extract_mileage(title: str) -> int | None:
    match = MILEAGE_PATTERN.search(title)
    if not match:
        return None

    raw = match.group(1)
    raw = raw.replace(",", "").replace(" ", "")

    try:
        value = int(raw)
    except:
        return None

    # جلوگیری از اشتباه گرفتن قیمت با کارکرد
    if value > 1_000_000:
        return None

    return value


# -----------------------------
# استخراج برند و مدل
# -----------------------------
def extract_brand_model(title: str) -> tuple[str, str]:
    t = normalize(title)

    known = [
        ("benz", "cls"),
        ("bmw", "x3"),
        ("peugeot", "206"),
        ("peugeot", "207"),
        ("pride", "131"),
        ("pride", "111"),
        ("tiba", "2"),
        ("dignity", "پرایم"),
    ]

    for brand, model in known:
        if brand in t and model in t:
            return brand, model

    parts = t.split()
    if len(parts) >= 2:
        return parts[0], parts[1]

    return "unknown", "unknown"


# -----------------------------
# استخراج trim (تیپ)
# -----------------------------
def extract_trim(title: str) -> str | None:
    t = normalize(title)

    patterns = [
        r"تیپ\s*(\d+)",
        r"type\s*(\d+)",
        r"tip\s*(\d+)",
    ]

    for p in patterns:
        m = re.search(p, t)
        if m:
            return m.group(1)

    return None


# -----------------------------
# ساخت VehicleAd از داده خام
# -----------------------------
def build_ad(
    ad_id: str,
    title: str,
    price: int,
    url: str,
) -> VehicleAd:

    year = extract_year(title)
    mileage = extract_mileage(title)
    brand, model = extract_brand_model(title)
    trim = extract_trim(title)

    # ❗ اگر سال پیدا نشد → این آگهی رو رد کن
    if year is None:
        raise ValueError(f"Year not found in title: {title}")

    return VehicleAd(
        ad_id=ad_id,
        brand=brand,
        model=model,
        year=year,
        price=price,
        url=url,
        title=title,
        mileage=mileage,
        trim=trim,
    )
