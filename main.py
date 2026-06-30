import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from statistics import median
from typing import Optional

import httpx
from telegram import Bot
from telegram.constants import ParseMode

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID: str = os.environ.get("TELEGRAM_CHANNEL_ID", "@chanelllvip")
DISCOUNT_THRESHOLD: float = float(os.environ.get("DISCOUNT_THRESHOLD_PERCENT", "10")) / 100
MIN_SAMPLE: int = int(os.environ.get("MIN_SAMPLE_FOR_MEDIAN", "5"))
PAGES_PER_CHANNEL: int = int(os.environ.get("TELEGRAM_PAGES_PER_CHANNEL", "1"))
MAX_MESSAGES: int = int(os.environ.get("MAX_MESSAGES_PER_RUN", "10"))
CHANNEL_DELAY: float = float(os.environ.get("CHANNEL_REQUEST_DELAY_SECONDS", "4"))
DB_PATH: str = os.environ.get("SELECTCAR_STATE_DB", "selectcar_state.sqlite3")

# ─── Channel lists ────────────────────────────────────────────────────────────
AD_CHANNELS = [
    ("zh_classic_car", "ZH Classic Car"),
    ("hmexpo", "HM Expo"),
    ("formulagallery", "Formula Gallery"),
    ("bamachintext", "Bamachin Text"),
    ("otugalericar", "Otu Galeri Car"),
    ("namayeshgahddarann", "Namayeshgah Daran"),
    ("Autoplack", "Autoplack"),
]

ZERO_PRICE_CHANNELS = [
    ("officialhamrahmechanic", "Hamrah Mechanic"),
    ("karnameh_com", "Karnameh"),
    ("khodro45", "Khodro45"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "fa,en;q=0.9",
}

# ─── DB setup ────────────────────────────────────────────────────────────────

def init_db(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS processed_messages (
            channel_username TEXT NOT NULL,
            message_id       TEXT NOT NULL,
            url              TEXT,
            status           TEXT,
            processed_at     TEXT,
            PRIMARY KEY (channel_username, message_id)
        );

        CREATE TABLE IF NOT EXISTS listings (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username     TEXT,
            channel_display_name TEXT,
            message_id           TEXT,
            url                  TEXT,
            title                TEXT,
            model_key            TEXT,
            price_toman          REAL,
            mileage_km           REAL,
            contact_phone        TEXT,
            posted_at            TEXT,
            raw_text             TEXT,
            created_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS rejected_listings (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username     TEXT,
            channel_display_name TEXT,
            message_id           TEXT,
            url                  TEXT,
            reason               TEXT,
            raw_text             TEXT,
            posted_at            TEXT,
            created_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS sent_deals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username    TEXT,
            message_id          TEXT,
            url                 TEXT,
            title               TEXT,
            price_toman         REAL,
            median_price_toman  REAL,
            discount_percent    REAL,
            sent_at             TEXT
        );

        CREATE TABLE IF NOT EXISTS zero_price_index (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name     TEXT,
            source_type     TEXT,
            source_url      TEXT,
            message_id      TEXT,
            title           TEXT,
            model_key       TEXT,
            zero_price_toman REAL,
            raw_text        TEXT,
            observed_at     TEXT,
            created_at      TEXT
        );
    """)
    con.commit()
    return con


# ─── Text helpers ─────────────────────────────────────────────────────────────

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
ZWC = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

def normalize_text(text: str) -> str:
    text = ZWC.sub("", text)
    text = text.translate(FA_DIGITS)
    text = text.replace("ك", "ک").replace("ي", "ی")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_title(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[0] if lines else ""


def make_model_key(title: str) -> str:
    key = title.lower()
    key = re.sub(r"[^a-z0-9\u0600-\u06ff]+", "_", key)
    return key.strip("_")


# ─── Price extraction ─────────────────────────────────────────────────────────

REJECT_PRICE_PATTERNS = re.compile(
    r"(توافق|تماس\s*بگیر|قیمت\s*توافق|اعلام\s*نشده|بدون\s*قیمت|تماس\s*بگیرید)",
    re.IGNORECASE,
)

def extract_price(text: str) -> Optional[float]:
    if REJECT_PRICE_PATTERNS.search(text):
        return None

    # میلیارد و میلیون توأم: مثل ۱۰ میلیارد و ۵۰۰ میلیون
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*میلیارد\s*(?:و\s*)?(\d+(?:\.\d+)?)\s*میلیون",
        text,
    )
    if m:
        return float(m.group(1)) * 1_000_000_000 + float(m.group(2)) * 1_000_000

    m = re.search(r"(\d+(?:\.\d+)?)\s*میلیارد", text)
    if m:
        return float(m.group(1)) * 1_000_000_000

    m = re.search(r"(\d+(?:\.\d+)?)\s*میلیون", text)
    if m:
        return float(m.group(1)) * 1_000_000

    # فرمت کوتاه‌شده: 10/500 → ۱۰ میلیارد و ۵۰۰ میلیون
    m = re.search(r"\b(\d{1,3})/(\d{3})\b", text)
    if m:
        billions = int(m.group(1))
        millions = int(m.group(2))
        value = billions * 1_000_000_000 + millions * 1_000_000
        if value > 100_000_000:
            return float(value)
    m = re.search(r"\b(\d{1,3}(?:,\d{3}){2,})\b", text)
    if m:
        value = float(m.group(1).replace(",", ""))
        if value >= 100_000_000:
            return value


    # عدد خام بزرگ (بیش از ۱۰۰ میلیون تومان)
    m = re.search(r"\b(\d{9,})\b", text)
    if m:
        return float(m.group(1))

    return None


# ─── Mileage extraction ───────────────────────────────────────────────────────

def extract_mileage(text: str) -> Optional[float]:
    m = re.search(
        r"(?:کارکرد|كاركرد|کار\s*کرد|کیلومتر|km)[^\d]*(\d[\d,\.]*)",
        text,
        re.IGNORECASE,
    )
    if m:
        raw = m.group(1).replace(",", "").replace(".", "")
        return float(raw)
    return None


# ─── Phone extraction ─────────────────────────────────────────────────────────

def extract_phone(text: str) -> Optional[str]:
    m = re.search(r"(?<!\d)(0?9\d{9})(?!\d)", text)
    if m:
        digits = m.group(1)
        if not digits.startswith("0"):
            digits = "0" + digits
        return digits
    return None


# ─── Rejection check ──────────────────────────────────────────────────────────

REJECT_CONTENT_PATTERNS = re.compile(
    r"(پیش.?فروش|ثبت.?نام|لیست\s*قیمت|قرعه.?کشی|فروش\s*فوری\s*کارخانه"
    r"|اعلام\s*موجودی|تحلیل\s*بازار|اخبار\s*خودرو|تبلیغ)",
    re.IGNORECASE,
)

def is_ad_content_rejected(text: str) -> Optional[str]:
    if REJECT_CONTENT_PATTERNS.search(text):
        return "محتوای نامعتبر (خبر/تبلیغ/پیش‌فروش)"
    return None


# ─── HTML scraper ─────────────────────────────────────────────────────────────

MSG_BLOCK = re.compile(
    r'<div class="tgme_widget_message_wrap[^>]*>.*?</div>\s*</div>\s*</div>',
    re.DOTALL,
)
MSG_ID = re.compile(r'data-post="[^/]+/(\d+)"')
MSG_TEXT = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
HTML_TAG = re.compile(r"<[^>]+>")
MSG_DATE = re.compile(r'<time[^>]+datetime="([^"]+)"')


def parse_html_messages(html: str, channel_username: str) -> list[dict]:
    results = []
    for block in MSG_BLOCK.finditer(html):
        raw_block = block.group(0)

        mid_m = MSG_ID.search(raw_block)
        if not mid_m:
            continue
        message_id = mid_m.group(1)

        text_m = MSG_TEXT.search(raw_block)
        if not text_m:
            continue
        raw_html_text = text_m.group(1)
        text = HTML_TAG.sub("", raw_html_text)
        text = text.replace("&#33;", "!").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = normalize_text(text)

        date_m = MSG_DATE.search(raw_block)
        posted_at = date_m.group(1) if date_m else datetime.now(timezone.utc).isoformat()

        url = f"https://t.me/{channel_username}/{message_id}"
        results.append({
            "message_id": message_id,
            "text": text,
            "url": url,
            "posted_at": posted_at,
        })
    return results


async def fetch_channel_page(
    client: httpx.AsyncClient,
    channel_username: str,
    before_id: Optional[str] = None,
) -> str:
    url = f"https://t.me/s/{channel_username}"
    if before_id:
        url += f"?before={before_id}"
    resp = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


# ─── Process one ad channel ──────────────────────────────────────────────────

def already_processed(con: sqlite3.Connection, channel: str, msg_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM processed_messages WHERE channel_username=? AND message_id=?",
        (channel, msg_id),
    ).fetchone()
    return row is not None


def mark_processed(con: sqlite3.Connection, channel: str, msg_id: str, url: str, status: str):
    con.execute(
        """INSERT OR REPLACE INTO processed_messages
           (channel_username, message_id, url, status, processed_at)
           VALUES (?,?,?,?,?)""",
        (channel, msg_id, url, status, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()


def save_listing(con: sqlite3.Connection, data: dict):
    con.execute(
        """INSERT INTO listings
           (channel_username, channel_display_name, message_id, url, title,
            model_key, price_toman, mileage_km, contact_phone, posted_at, raw_text, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["channel_username"],
            data["channel_display_name"],
            data["message_id"],
            data["url"],
            data["title"],
            data["model_key"],
            data["price_toman"],
            data["mileage_km"],
            data.get("contact_phone"),
            data["posted_at"],
            data["raw_text"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    con.commit()


def save_rejected(con: sqlite3.Connection, data: dict):
    con.execute(
        """INSERT INTO rejected_listings
           (channel_username, channel_display_name, message_id, url,
            reason, raw_text, posted_at, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            data["channel_username"],
            data["channel_display_name"],
            data["message_id"],
            data["url"],
            data["reason"],
            data["raw_text"],
            data["posted_at"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    con.commit()


async def process_ad_channel(
    client: httpx.AsyncClient,
    con: sqlite3.Connection,
    username: str,
    display_name: str,
) -> tuple[int, int]:
    accepted = 0
    rejected = 0
    before_id: Optional[str] = None

    for _ in range(PAGES_PER_CHANNEL):
        try:
            html = await fetch_channel_page(client, username, before_id)
        except Exception as e:
            log.info(f"[scrape] channel=@{username} fetch_error={e}")
            break

        messages = parse_html_messages(html, username)
        if not messages:
            break

        messages = messages[:MAX_MESSAGES]
        before_id = messages[0]["message_id"]

        for msg in messages:
            mid = msg["message_id"]
            url = msg["url"]
            text = msg["text"]

            if already_processed(con, username, mid):
                continue

            reject_reason: Optional[str] = None

            # محتوای نامعتبر
            reject_reason = is_ad_content_rejected(text)

            if not reject_reason:
                price = extract_price(text)
                if price is None:
                    reject_reason = "قیمت مشخص نیست یا توافقی است"


                mileage = extract_mileage(text)
            log.info(f"[debug] reason={reject_reason} text={text[:200]}")

            if reject_reason:
                save_rejected(con, {
                    "channel_username": username,
                    "channel_display_name": display_name,
                    "message_id": mid,
                    "url": url,
                    "reason": reject_reason,
                    "raw_text": text,
                    "posted_at": msg["posted_at"],
                })
                mark_processed(con, username, mid, url, "rejected")
                rejected += 1
                continue

            title = extract_title(text)
            model_key = make_model_key(title)
            phone = extract_phone(text)

            save_listing(con, {
                "channel_username": username,
                "channel_display_name": display_name,
                "message_id": mid,
                "url": url,
                "title": title,
                "model_key": model_key,
                "price_toman": price,
                "mileage_km": mileage,
                "contact_phone": phone,
                "posted_at": msg["posted_at"],
                "raw_text": text,
            })
            mark_processed(con, username, mid, url, "accepted")
            accepted += 1

        await asyncio.sleep(CHANNEL_DELAY)

    log.info(f"[scrape] channel=@{username} accepted={accepted} rejected={rejected}")
    return accepted, rejected


# ─── Process zero price channels ─────────────────────────────────────────────

def already_zero_processed(con: sqlite3.Connection, source: str, msg_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM zero_price_index WHERE source_name=? AND message_id=?",
        (source, msg_id),
    ).fetchone()
    return row is not None


def save_zero_price(con: sqlite3.Connection, data: dict):
    con.execute(
        """INSERT INTO zero_price_index
           (source_name, source_type, source_url, message_id, title,
            model_key, zero_price_toman, raw_text, observed_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            data["source_name"],
            "telegram",
            data["source_url"],
            data["message_id"],
            data["title"],
            data["model_key"],
            data["zero_price_toman"],
            data["raw_text"],
            data["observed_at"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    con.commit()


async def process_zero_channel(
    client: httpx.AsyncClient,
    con: sqlite3.Connection,
    username: str,
    display_name: str,
) -> int:
    saved = 0
    try:
        html = await fetch_channel_page(client, username)
    except Exception as e:
        log.info(f"[zero] channel=@{username} fetch_error={e}")
        return 0

    messages = parse_html_messages(html, username)
    for msg in messages[:MAX_MESSAGES]:
        mid = msg["message_id"]
        text = msg["text"]
        url = msg["url"]

        if already_zero_processed(con, username, mid):
            continue

        price = extract_price(text)
        if price is None:
            continue

        title = extract_title(text)
        model_key = make_model_key(title)

        save_zero_price(con, {
            "source_name": username,
            "source_url": url,
            "message_id": mid,
            "title": title,
            "model_key": model_key,
            "zero_price_toman": price,
            "raw_text": text,
            "observed_at": msg["posted_at"],
        })
        log.info(f"[zero] updated model={model_key} price={price:,.0f}")
        saved += 1

    await asyncio.sleep(CHANNEL_DELAY)
    return saved


# ─── Deal detection ───────────────────────────────────────────────────────────

def already_sent(con: sqlite3.Connection, channel: str, msg_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sent_deals WHERE channel_username=? AND message_id=?",
        (channel, msg_id),
    ).fetchone()
    return row is not None


def get_global_prices(con: sqlite3.Connection, model_key: str, exclude_id: Optional[int] = None) -> list[float]:
    query = "SELECT price_toman FROM listings WHERE model_key=? AND price_toman > 0"
    params: list = [model_key]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    rows = con.execute(query, params).fetchall()
    return [r["price_toman"] for r in rows]


def get_zero_price(con: sqlite3.Connection, model_key: str) -> Optional[float]:
    row = con.execute(
        """SELECT zero_price_toman FROM zero_price_index
           WHERE model_key=? ORDER BY observed_at DESC LIMIT 1""",
        (model_key,),
    ).fetchone()
    return row["zero_price_toman"] if row else None


def format_toman(value: float) -> str:
    if value >= 1_000_000_000:
        b = value / 1_000_000_000
        return f"{b:.2f} میلیارد تومان"
    m = value / 1_000_000
    return f"{m:.0f} میلیون تومان"


def build_deal_message(
    listing: sqlite3.Row,
    median_price: float,
    discount_pct: float,
    zero_price: Optional[float],
) -> str:
    title = listing["title"] or "نامشخص"
    mileage = f"{int(listing['mileage_km']):,} km" if listing["mileage_km"] is not None else "نامشخص"
    price_str = format_toman(listing["price_toman"])
    median_str = format_toman(median_price)
    discount_str = f"{discount_pct:.1f}٪ پایین‌تر"

    if zero_price is not None:
        zero_str = format_toman(zero_price)
        diff_zero = ((listing["price_toman"] - zero_price) / zero_price) * 100
        if diff_zero < 0:
            zero_diff_str = f"{abs(diff_zero):.1f}٪ پایین‌تر از قیمت صفر"
        else:
            zero_diff_str = f"{abs(diff_zero):.1f}٪ بالاتر از قیمت صفر"
    else:
        zero_str = "پیدا نشد"
        zero_diff_str = "نامشخص"

    phone = listing["contact_phone"] or "موجود نیست"

    return (
        f"🚘 دیل مناسب خودرو\n\n"
        f"نام خودرو: {title}\n"
        f"کارکرد: {mileage}\n"
        f"قیمت آگهی: {price_str}\n"
        f"میانگین قیمت کارکرده بین همه کانال‌ها: {median_str}\n"
        f"قیمت صفر همان خودرو: {zero_str}\n"
        f"اختلاف آگهی با میانگین کارکرده: {discount_str}\n"
        f"اختلاف آگهی با قیمت صفر: {zero_diff_str}\n"
        f"منبع آگهی: {listing['channel_display_name']}\n"
        f"لینک آگهی اصلی:\n{listing['url']}\n"
        f"شماره تماس فروشنده: {phone}"
    )


async def detect_and_send_deals(con: sqlite3.Connection, bot: Bot):
    rows = con.execute(
        "SELECT * FROM listings ORDER BY created_at DESC"
    ).fetchall()

    sent_count = 0
    for row in rows:
        channel = row["channel_username"]
        mid = row["message_id"]
        model_key = row["model_key"]
        price = row["price_toman"]

        if already_sent(con, channel, mid):
            continue

        prices = get_global_prices(con, model_key, exclude_id=row["id"])
        if len(prices) < MIN_SAMPLE:
            continue

        med = median(prices)
        if med == 0:
            continue

        discount = (med - price) / med
        if discount < DISCOUNT_THRESHOLD:
            continue

        zero_price = get_zero_price(con, model_key)
        message_text = build_deal_message(row, med, discount * 100, zero_price)

        try:
            sent = await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_text,
            )
            log.info(f"[telegram] SENT message_id={sent.message_id} model={model_key} discount={discount*100:.1f}%")
        except Exception as e:
            log.info(f"[telegram] SEND_FAILED model={model_key} error={e}")
            continue

        con.execute(
            """INSERT INTO sent_deals
               (channel_username, message_id, url, title,
                price_toman, median_price_toman, discount_percent, sent_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                channel, mid, row["url"], row["title"],
                price, med, discount * 100,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        con.commit()
        sent_count += 1
        log.info(f"[deals] FOUND model={model_key} discount={discount*100:.1f}%")

    return sent_count


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    log.info("[main] started")

    con = init_db(DB_PATH)
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async with httpx.AsyncClient() as client:

        # گروه A — کانال‌های آگهی
        total_accepted = 0
        total_rejected = 0
        for username, display_name in AD_CHANNELS:
            a, r = await process_ad_channel(client, con, username, display_name)
            total_accepted += a
            total_rejected += r

        log.info(f"[scrape] total_accepted={total_accepted} total_rejected={total_rejected}")

        # گروه B — منابع قیمت صفر
        for username, display_name in ZERO_PRICE_CHANNELS:
            await process_zero_channel(client, con, username, display_name)
    rows = con.execute("SELECT model_key, COUNT(*) c FROM listings GROUP BY model_key HAVING c > 1").fetchall()
    for r in rows:
        log.info(f"[debug-model] key={r['model_key']} count={r['c']}")

    # تشخیص و ارسال دیل
    sent = await detect_and_send_deals(con, bot)
    log.info(f"[deals] sent_deals={sent}")

    con.close()
    log.info("[main] finished")


if __name__ == "__main__":
    asyncio.run(main())