"""
main.py — نسخه‌ی تک‌فایلی SelectCar Bot

همه‌ی منطق پروژه (دیتابیس، اسکریپ کانال‌های تلگرام، فیلتر آگهی اقساطی،
نرمال‌سازی، موتور قیمت‌گذاری) در همین یک فایل قرار داره تا نگه‌داری‌ش
روی موبایل ساده‌تر باشه.

منابع (کانال‌های تلگرام / سایت‌ها) پایین همین فایل، در متغیر SOURCES تعریف شدن.
برای اضافه کردن منبع جدید، فقط یه آیتم به لیست SOURCES اضافه کن — نیازی به
تغییر بقیه‌ی کد نیست.

اجرا: python main.py
"""

import re
import hashlib
import logging
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from statistics import mean

import httpx

# ============================================================================
# تنظیمات کلی
# ============================================================================

DB_PATH = Path(__file__).parent / "data" / "selectcar.db"
REQUEST_TIMEOUT = 15
MIN_SAMPLE = 2                 # حداقل تعداد آگهی مشابه برای معتبر بودن میانگین
DISCOUNT_THRESHOLD = 0.03      # حداقل ۳٪ زیر میانگین برای «دیل مناسب»

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

EXCLUDE_KEYWORDS = [
    "اقساط", "اقساطی", "لیزینگ", "لیزینگی",
    "پیش پرداخت", "پیش‌پرداخت", "پیش‌ پرداخت",
    "ثبت نام", "ثبت‌نام", "قرعه‌کشی", "قرعه کشی",
    "نقد و اقساط", "چک", "معاوضه با",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("selectcar")


# ============================================================================
# تعریف منابع — برای اضافه کردن منبع جدید فقط اینجا رو ویرایش کن
# ============================================================================

@dataclass
class Source:
    name: str
    source_type: str          # "telegram" | "website"
    patterns: dict
    channel_id: str | None = None   # برای تلگرام
    base_url: str | None = None     # برای وب‌سایت
    exclude_keywords: list = field(default_factory=list)


# الگوی regex مشترک برای پیام‌های کانال‌های تلگرام (بر اساس ساختار صفحه‌ی t.me/s)
TELEGRAM_PATTERNS = {
    "listing_block": r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|$)',
    "car_name": r'tgme_widget_message_text[^"]*"[^>]*>\s*([^\n<]{3,50})',
    "price": r'قیمت[^\d]{0,20}([\d,]{5,})\s*تومان',
    "mileage": r'کارکرد[^\d]{0,20}([\d,]+)\s*کیلومتر',
    "body_condition": r'وضعیت بدنه[^:\n]*[:：]\s*([^\n<]+)',
    "technical_health": r'سلامت فنی[^:\n]*[:：]\s*([^\n<]+)',
    "model_year": r'مدل[^\d]{0,10}(1[34]\d\d)',
    "ad_link": r'tgme_widget_message_date"\s+href="([^"]+)"',
}

SOURCES: list[Source] = [
    Source(
        name="hmexpo_telegram",
        source_type="telegram",
        channel_id="hmexpo",
        patterns=TELEGRAM_PATTERNS,
        exclude_keywords=EXCLUDE_KEYWORDS,
    ),
    Source(
        name="formulagallery_telegram",
        source_type="telegram",
        channel_id="formulagallery",
        patterns=TELEGRAM_PATTERNS,
        exclude_keywords=EXCLUDE_KEYWORDS,
    ),
    Source(
        name="zh_classic_car_telegram",
        source_type="telegram",
        channel_id="zh_classic_car",
        patterns=TELEGRAM_PATTERNS,
        exclude_keywords=EXCLUDE_KEYWORDS,
    ),
    # برای اضافه کردن یه کانال دیگه، همین‌جا یه Source جدید اضافه کن:
    # Source(
    #     name="yv_vk_telegram",
    #     source_type="telegram",
    #     channel_id="yv_vk",
    #     patterns=TELEGRAM_PATTERNS,
    #     exclude_keywords=EXCLUDE_KEYWORDS,
    # ),
]


# ============================================================================
# دیتابیس
# ============================================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    ad_link         TEXT NOT NULL,
    ad_hash         TEXT UNIQUE NOT NULL,
    car_name        TEXT NOT NULL,
    car_model_year  TEXT,
    normalized_key  TEXT NOT NULL,
    price_toman     INTEGER NOT NULL,
    mileage_km      INTEGER,
    body_condition  TEXT,
    technical_health TEXT,
    first_seen_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id  INTEGER NOT NULL REFERENCES listings(id),
    price_toman INTEGER NOT NULL,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zero_km_prices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_key TEXT UNIQUE NOT NULL,
    price_toman    INTEGER NOT NULL,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_listings_normalized_key ON listings(normalized_key);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ============================================================================
# اسکرپینگ (تلگرام از طریق t.me/s ، وب‌سایت از طریق request معمولی)
# ============================================================================

def fetch_html(url: str) -> str | None:
    """گرفتن HTML یک صفحه با مدیریت کامل خطا (شکست یک منبع کل برنامه رو متوقف نمی‌کنه)."""
    clean_url = url.strip()
    try:
        response = httpx.get(clean_url, headers=HEADERS, timeout=REQUEST_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except httpx.TimeoutException:
        logger.warning(f"تایم‌اوت در دریافت: {clean_url}")
    except httpx.HTTPStatusError as e:
        logger.warning(f"خطای HTTP {e.response.status_code} در: {clean_url}")
    except httpx.RequestError as e:
        logger.warning(f"خطای اتصال به {clean_url}: {e}")
    return None


def _extract_first(pattern: str, text: str) -> str | None:
    if not pattern:
        return None
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def parse_block(block_html: str, patterns: dict, source_name: str) -> dict | None:
    """استخراج یک آگهی از یک بلوک HTML بر اساس الگوهای regex."""
    car_name = _extract_first(patterns.get("car_name", ""), block_html)
    price_raw = _extract_first(patterns.get("price", ""), block_html)
    ad_link = _extract_first(patterns.get("ad_link", ""), block_html)

    if not car_name or not price_raw or not ad_link:
        return None

    try:
        price_toman = int(price_raw.replace(",", ""))
    except ValueError:
        logger.warning(f"[{source_name}] قیمت غیرقابل تبدیل: {price_raw}")
        return None

    mileage_raw = _extract_first(patterns.get("mileage", ""), block_html)
    mileage_km = int(mileage_raw.replace(",", "")) if mileage_raw else None

    return {
        "car_name": car_name,
        "price_toman": price_toman,
        "ad_link": ad_link,
        "mileage_km": mileage_km,
        "body_condition": _extract_first(patterns.get("body_condition", ""), block_html),
        "technical_health": _extract_first(patterns.get("technical_health", ""), block_html),
        "model_year": _extract_first(patterns.get("model_year", ""), block_html),
        "raw_description": block_html,  # برای بررسی کلمات ممنوعه در کل متن
    }


def scrape_source(source: Source) -> list[dict]:
    """اسکرپ یک منبع (تلگرام یا وب‌سایت) و برگرداندن لیست آگهی‌های خام."""
    if source.source_type == "telegram":
        channel = (source.channel_id or "").strip().lstrip("@")
        url = f"https://t.me/s/{channel}"
    elif source.source_type == "website":
        url = source.base_url
    else:
        logger.warning(f"[{source.name}] نوع منبع ناشناخته: {source.source_type}")
        return []

    html = fetch_html(url)
    if html is None:
        logger.error(f"[{source.name}] دریافت صفحه ناموفق بود")
        return []

    block_pattern = source.patterns.get("listing_block")
    if not block_pattern:
        logger.error(f"[{source.name}] الگوی listing_block تعریف نشده")
        return []

    blocks = re.findall(block_pattern, html, flags=re.DOTALL)
    listings = []
    for block in blocks:
        parsed = parse_block(block, source.patterns, source.name)
        if parsed:
            listings.append(parsed)

    logger.info(f"[{source.name}] {len(listings)} آگهی خام از {len(blocks)} پیام/بلوک استخراج شد")
    return listings


# ============================================================================
# فیلتر آگهی‌های اقساطی/لیزینگ/غیرنقدی
# ============================================================================

def is_excluded(listing: dict, extra_keywords: list[str]) -> tuple[bool, str | None]:
    keywords = EXCLUDE_KEYWORDS + extra_keywords
    searchable_text = " ".join(
        str(listing.get(field, "") or "")
        for field in ("car_name", "body_condition", "technical_health", "raw_description")
    )
    for keyword in keywords:
        if keyword in searchable_text:
            return True, keyword
    return False, None


def filter_listings(listings: list[dict], extra_keywords: list[str]) -> tuple[list[dict], list[dict]]:
    valid, excluded = [], []
    for listing in listings:
        excluded_flag, reason = is_excluded(listing, extra_keywords)
        if excluded_flag:
            listing["exclude_reason"] = reason
            excluded.append(listing)
        else:
            valid.append(listing)
    return valid, excluded


# ============================================================================
# نرمال‌سازی اسم خودرو (برای گروه‌بندی آگهی‌های مشابه)
# ============================================================================

BRAND_ALIASES = {
    "peugeot": "پژو", "پژو": "پژو",
    "samand": "سمند", "سمند": "سمند",
    "pride": "پراید", "پراید": "پراید",
    "tiba": "تیبا", "تیبا": "تیبا",
    "dena": "دنا", "دنا": "دنا",
    "quick": "کوییک", "کوییک": "کوییک",
}


def normalize_car_name(raw_name: str) -> str:
    text = raw_name.strip().lower()
    text = re.sub(r"[\u200c\s]+", " ", text)
    text = re.sub(r"[^\w\u0600-\u06FF\s]", "", text)
    for alias, standard in BRAND_ALIASES.items():
        if alias in text:
            text = text.replace(alias, standard)
            break
    return "-".join(text.split())


def build_normalized_key(car_name: str, model_year: str | None) -> str:
    base_key = normalize_car_name(car_name)
    return f"{base_key}-{model_year.strip()}" if model_year else base_key


# ============================================================================
# ذخیره‌سازی در دیتابیس
# ============================================================================

def make_ad_hash(ad_link: str) -> str:
    return hashlib.sha256(ad_link.strip().encode("utf-8")).hexdigest()


def save_listing(source_name: str, listing: dict) -> None:
    ad_link = listing["ad_link"].strip()
    ad_hash = make_ad_hash(ad_link)
    normalized_key = build_normalized_key(listing["car_name"], listing.get("model_year"))

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO listings (
                source_name, ad_link, ad_hash, car_name, car_model_year,
                normalized_key, price_toman, mileage_km,
                body_condition, technical_health, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(ad_hash) DO UPDATE SET
                price_toman = excluded.price_toman,
                mileage_km = excluded.mileage_km,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (
                source_name, ad_link, ad_hash,
                listing["car_name"], listing.get("model_year"),
                normalized_key, listing["price_toman"], listing.get("mileage_km"),
                listing.get("body_condition"), listing.get("technical_health"),
            ),
        )
        conn.commit()

        listing_id = conn.execute(
            "SELECT id FROM listings WHERE ad_hash = ?", (ad_hash,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO price_history (listing_id, price_toman) VALUES (?, ?)",
            (listing_id, listing["price_toman"]),
        )
        conn.commit()
    except Exception as e:
        logger.error(f"خطا در ذخیره‌ی آگهی ({ad_link}): {e}")
    finally:
        conn.close()


# ============================================================================
# موتور قیمت‌گذاری: میانگین بازار، مقایسه با صفر، تشخیص دیل مناسب
# ============================================================================

@dataclass
class DealResult:
    car_name: str
    price_toman: int
    market_avg_price: float | None
    zero_km_price: int | None
    diff_from_avg_pct: float | None
    diff_from_zero_pct: float | None
    is_good_deal: bool
    ad_link: str
    source_name: str


def compute_group_average(normalized_key: str) -> tuple[float | None, int]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT price_toman FROM listings WHERE normalized_key = ?", (normalized_key,)
        ).fetchall()
    finally:
        conn.close()
    prices = [row["price_toman"] for row in rows]
    if len(prices) < MIN_SAMPLE:
        return None, len(prices)
    return mean(prices), len(prices)


def get_zero_km_price(normalized_key: str) -> int | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT price_toman FROM zero_km_prices WHERE normalized_key = ?", (normalized_key,)
        ).fetchone()
    finally:
        conn.close()
    return row["price_toman"] if row else None


def find_good_deals() -> list[DealResult]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM listings").fetchall()
    finally:
        conn.close()

    results = []
    for row in rows:
        normalized_key = row["normalized_key"]
        avg_price, _ = compute_group_average(normalized_key)
        zero_price = get_zero_km_price(normalized_key)
        price = row["price_toman"]

        diff_from_avg_pct = None
        is_good_deal = False
        if avg_price:
            diff_from_avg_pct = (price - avg_price) / avg_price * 100
            if price <= avg_price * (1 - DISCOUNT_THRESHOLD):
                is_good_deal = True

        diff_from_zero_pct = (price - zero_price) / zero_price * 100 if zero_price else None

        results.append(DealResult(
            car_name=row["car_name"],
            price_toman=price,
            market_avg_price=avg_price,
            zero_km_price=zero_price,
            diff_from_avg_pct=diff_from_avg_pct,
            diff_from_zero_pct=diff_from_zero_pct,
            is_good_deal=is_good_deal,
            ad_link=row["ad_link"],
            source_name=row["source_name"],
        ))

    good_deals = [r for r in results if r.is_good_deal]
    good_deals.sort(key=lambda r: r.diff_from_avg_pct or 0)
    return good_deals


# ============================================================================
# اجرای اصلی
# ============================================================================

def main() -> None:
    logger.info("=== شروع اجرای SelectCar Bot ===")
    init_db()

    for source in SOURCES:
        try:
            raw_listings = scrape_source(source)
            valid, excluded = filter_listings(raw_listings, source.exclude_keywords)
            logger.info(
                f"[{source.name}] {len(valid)} آگهی معتبر، "
                f"{len(excluded)} آگهی حذف‌شده (اقساطی/لیزینگ/...)"
            )
            for listing in valid:
                save_listing(source.name, listing)
        except Exception as e:
            # خطای یک منبع نباید کل اجرا رو متوقف کنه
            logger.error(f"[{source.name}] خطای غیرمنتظره: {e}")

    deals = find_good_deals()
    logger.info(f"=== {len(deals)} دیل مناسب پیدا شد ===")
    for deal in deals[:10]:
        logger.info(
            f"{deal.car_name} | {deal.price_toman:,} تومان | "
            f"{deal.diff_from_avg_pct:.1f}% زیر میانگین | {deal.ad_link}"
        )

    logger.info("=== پایان اجرا ===")


if __name__ == "__main__":
    main()
