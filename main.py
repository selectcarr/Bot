#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تحلیل قیمت خودرو از کانال‌های تلگرام
------------------------------------------
رفع‌شده در این نسخه:
1. اعداد شبیه شماره موبایل (مثل 913.110.7054) دیگر به‌عنوان قیمت خوانده نمی‌شوند.
2. فرمت کوتاه‌نویسی با "/" مثل "قیمت 2/970" یا "کارکرد 4/500" پشتیبانی می‌شود.
3. سال میلادی (مثل "مدل 2024") علاوه بر سال شمسی تشخیص داده می‌شود.
4. کد تیپ بعد از نام خودرو (مثل "جک E50A") به نام اضافه می‌شود تا گروه‌بندی دقیق‌تر شود.
5. آگهی‌های ثبت‌نامی/پیش‌فروش با نوشتارهای مختلف حذف می‌شوند.
6. آگهی‌های صفرکیلومتر فقط با صفرهای هم‌نام+هم‌مدل مقایسه می‌شوند، نه با کارکرده‌ها.
"""

import os
import re
import json
import time
import sys
from statistics import mean

import requests

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
SENT_IDS_PATH = os.path.join(APP_DIR, "sent_ids.json")
MAX_SENT_IDS = 2000

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ENGLISH_DIGITS = "0123456789"
DIGIT_MAP = str.maketrans(PERSIAN_DIGITS, ENGLISH_DIGITS)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sent_ids():
    if not os.path.exists(SENT_IDS_PATH):
        return set()
    try:
        with open(SENT_IDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def save_sent_ids(sent_ids):
    trimmed = list(sent_ids)[-MAX_SENT_IDS:]
    with open(SENT_IDS_PATH, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def normalize_number(raw):
    if not raw:
        return None
    cleaned = raw.translate(DIGIT_MAP)
    cleaned = re.sub(r"[,،.\s]", "", cleaned)
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def fetch_channel_html(channel, before=None, timeout=15):
    url = f"https://t.me/s/{channel}"
    if before:
        url += f"?before={before}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


POST_MARKER_RE = re.compile(r'data-post="([^"/]+)/(\d+)"')
MESSAGE_TEXT_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL
)

TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def clean_html_text(raw_html):
    if not raw_html:
        return ""
    text = BR_RE.sub("\n", raw_html)
    text = TAG_RE.sub("", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", '"')
                .replace("&#39;", "'").replace("&nbsp;", " "))
    return text.strip()


def parse_channel_messages(html, channel):
    markers = []
    seen_ids = set()
    for m in POST_MARKER_RE.finditer(html):
        ch, post_id = m.group(1), m.group(2)
        key = (ch, post_id)
        if key in seen_ids:
            continue
        seen_ids.add(key)
        markers.append((m.start(), ch, post_id))

    messages = []
    for idx, (start, ch, post_id) in enumerate(markers):
        end = markers[idx + 1][0] if idx + 1 < len(markers) else len(html)
        block = html[start:end]
        text_match = MESSAGE_TEXT_RE.search(block)
        if not text_match:
            continue
        text = clean_html_text(text_match.group(1))
        if not text:
            continue
        messages.append({
            "channel": ch,
            "id": post_id,
            "link": f"https://t.me/{ch}/{post_id}",
            "text": text
        })
    return messages


def contains_excluded_keyword(text, exclude_keywords):
    for kw in exclude_keywords:
        if kw in text:
            return kw
    return None


def extract_car_name(text, car_models):
    for model in car_models:
        idx = text.find(model)
        if idx == -1:
            continue
        remainder = text[idx + len(model):]
        trim_match = re.match(r"\s+([A-Za-z][A-Za-z0-9\-]{1,8})\b", remainder)
        if trim_match:
            return f"{model} {trim_match.group(1)}"
        return model

    first_line = text.split("\n")[0].strip()
    fallback = first_line[:60].strip()
    return fallback if fallback else "خودروی نامشخص"


def looks_like_phone_number(raw):
    cleaned = re.sub(r"\s", "", raw)
    if re.fullmatch(r"0?9\d{2}[.\-]?\d{3}[.\-]?\d{4}", cleaned):
        return True
    return False


NUM_PATTERN = r"([۰-۹0-9][۰-۹0-9,،.٬\s]{0,15}[۰-۹0-9]|[۰-۹0-9])"
BILLION_WORDS = ("میلیارد", "ملیارد")
MILLION_WORDS = ("میلیون", "ملیون", "میليون", "میلیو")


def parse_price_from_text(text):
    m = re.search(r"قیمت[^۰-۹0-9]{0,10}([۰-۹0-9]{1,3})\s*/\s*([۰-۹0-9]{3})(?!\d)", text)
    if m:
        whole = normalize_number(m.group(1))
        frac = normalize_number(m.group(2))
        if whole is not None and frac is not None:
            return (whole * 1000 + frac) * 1_000_000

    m = re.search(NUM_PATTERN + r"\s*(?:" + "|".join(BILLION_WORDS) + ")", text)
    if m:
        n = normalize_number(m.group(1))
        if n:
            return n * 1_000_000_000

    m = re.search(NUM_PATTERN + r"\s*(?:" + "|".join(MILLION_WORDS) + ")", text)
    if m:
        n = normalize_number(m.group(1))
        if n:
            return n * 1_000_000

    m = re.search(r"([۰-۹0-9][۰-۹0-9,،.٬\s]{2,20}[۰-۹0-9])\s*تومان", text)
    if m and not looks_like_phone_number(m.group(1)):
        n = normalize_number(m.group(1))
        if n and n >= 1_000_000:
            return n

    m = re.search(r"([۰-۹0-9][۰-۹0-9,،.٬]{0,10})\s*م(?:[^ا-ی]|$)", text)
    if m and not looks_like_phone_number(m.group(1)):
        n = normalize_number(m.group(1))
        if n and n < 100_000:
            return n * 1_000_000

    m = re.search(r"(?:قیمت|فروش|مبلغ)[^۰-۹0-9]{0,10}([۰-۹0-9][۰-۹0-9,،.٬]{0,10})", text)
    if m and not looks_like_phone_number(m.group(1)):
        n = normalize_number(m.group(1))
        if n:
            return n if n >= 1_000_000 else n * 1_000_000

    return None


def parse_mileage_from_text(text):
    if re.search(r"کارکرد\s*[:：]?\s*صفر(?!\S)", text):
        return 0

    m = re.search(r"کارکرد[^۰-۹0-9]{0,10}([۰-۹0-9]{1,3})\s*/\s*([۰-۹0-9]{3})(?!\d)", text)
    if m:
        whole = normalize_number(m.group(1))
        frac = normalize_number(m.group(2))
        if whole is not None and frac is not None:
            return whole * 1000 + frac

    m = re.search(r"کارکرد[^۰-۹0-9]{0,10}([۰-۹0-9][۰-۹0-9,،.٬\s]{0,15})\s*(?:کیلومتر|کیلو|km)?", text, re.IGNORECASE)
    if m:
        n = normalize_number(m.group(1))
        if n is not None and 0 <= n <= 2_000_000:
            return n

    return None


def parse_model_year(text):
    # اعداد فارسی را قبل از تطبیق به انگلیسی تبدیل می‌کنیم، چون بعضی آگهی‌ها
    # سال را با ارقام فارسی می‌نویسند (مثل «مدل ۲۰۱۵») و بدون این تبدیل، الگوی
    # ارقام لاتین آن را پیدا نمی‌کرد.
    ascii_text = text.translate(DIGIT_MAP)
    m = re.search(r"(?:مدل|سال)\s*[:：]?\s*((?:1[34]\d{2})|(?:20\d{2}))", ascii_text)
    if m:
        return m.group(1)
    return None



ZERO_KM_PATTERNS = (
    r"صفر\s*کیلومتر",
    r"صفرکیلومتر",
    r"کیلومتر\s*صفر",
    r"کارکرد\s*[:：]?\s*صفر(?!\S)",
    r"(?<!\d)0\s*km\b",
    r"(?<!\d)0\s*کیلومتر",
)
ZERO_KM_RE = re.compile("|".join(ZERO_KM_PATTERNS), re.IGNORECASE)


def is_zero_km_ad(text):
    return bool(ZERO_KM_RE.search(text))


def parse_listing(text, cfg):
    excluded_kw = contains_excluded_keyword(text, cfg["exclude_keywords"])
    if excluded_kw:
        return None, excluded_kw

    car_name = extract_car_name(text, cfg["car_models"])

    price = parse_price_from_text(text)
    if price is None:
        return None, "قیمت پیدا نشد (هیچ فرمت شناخته‌شده‌ای نداشت)"

    mileage = parse_mileage_from_text(text)
    year = parse_model_year(text)

    return {
        "car_name": car_name,
        "model_year": year,
        "price": price,
        "mileage": mileage,
        "is_zero_km": is_zero_km_ad(text),
        "raw_text": text
    }, None


def group_key(listing):
    year = listing["model_year"] or "نامشخص"
    return f"{listing['car_name']}|{year}"


def build_zero_km_price_table(listings):
    by_name_year = {}
    by_name_only = {}

    for l in listings:
        if not l["is_zero_km"]:
            continue
        by_name_only.setdefault(l["car_name"], []).append(l["price"])
        if l["model_year"]:
            key = f"{l['car_name']}|{l['model_year']}"
            by_name_year.setdefault(key, []).append(l["price"])

    table_year = {k: round(mean(v)) for k, v in by_name_year.items()}
    table_name = {k: round(mean(v)) for k, v in by_name_only.items()}
    return table_year, table_name


def lookup_zero_km_price(car_name, model_year, table_year, table_name):
    if model_year:
        key = f"{car_name}|{model_year}"
        if key in table_year:
            return table_year[key]
    if car_name in table_name:
        return table_name[car_name]
    return None


def analyze_listings(listings, threshold_percent):
    deals = []

    for zero_flag in (False, True):
        subset = [l for l in listings if l["is_zero_km"] == zero_flag]
        groups = {}
        for l in subset:
            groups.setdefault(group_key(l), []).append(l)

        for key, items in groups.items():
            prices = [i["price"] for i in items]
            avg_price = mean(prices)
            for i in items:
                diff_percent = (avg_price - i["price"]) / avg_price * 100
                i["avg_price_group"] = round(avg_price)
                i["diff_percent_vs_avg"] = round(diff_percent, 1)
                i["group_size"] = len(items)
                if diff_percent >= threshold_percent:
                    deals.append(i)

    deals.sort(key=lambda x: x["diff_percent_vs_avg"], reverse=True)
    return deals


def format_toman(value):
    if value is None:
        return "پیدا نشد"
    return f"{value:,}".replace(",", "٬") + " تومان"


def format_deal_message(listing, link, table_year, table_name):
    zero_price = lookup_zero_km_price(
        listing["car_name"], listing["model_year"], table_year, table_name
    )

    lines = [
        f"نام خودرو : {listing['car_name']}",
        f"مدل خودرو : {listing['model_year'] or 'نامشخص'}",
        f"قیمت صفر : {format_toman(zero_price)}",
        f"قیمت میانگین این مدل : {format_toman(listing['avg_price_group'])}",
        f"قیمت این خودرو : {format_toman(listing['price'])}",
        f"درصد قیمت زیر میانگین : {listing['diff_percent_vs_avg']}٪",
        f"لینک آگهی : {link}",
    ]
    return "\n".join(lines)


def send_telegram_message(bot_token, chat_id, text):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(url, data={
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False
    }, timeout=15)
    if resp.status_code != 200:
        print(f"[warn] ارسال پیام تلگرام ناموفق: {resp.status_code} {resp.text}", file=sys.stderr)
    return resp.ok


def collect_all_listings(cfg):
    all_listings = []
    for channel in cfg["telegram_channels"]:
        try:
            html = fetch_channel_html(channel)
        except Exception as e:
            print(f"[warn] خطا در گرفتن کانال {channel}: {e}", file=sys.stderr)
            continue
        messages = parse_channel_messages(html, channel)
        print(f"[info] کانال {channel}: {len(messages)} پست پیدا شد")
        for msg in messages:
            listing, reason = parse_listing(msg["text"], cfg)
            if listing is None:
                continue
            listing["link"] = msg["link"]
            all_listings.append(listing)
        time.sleep(1)
    return all_listings


def main():
    cfg = load_config()

    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not bot_token or not chat_id:
        print("[error] TELEGRAM_BOT_TOKEN یا TELEGRAM_CHAT_ID تنظیم نشده است.", file=sys.stderr)
        sys.exit(1)

    sent_ids = load_sent_ids()

    listings = collect_all_listings(cfg)
    print(f"[info] مجموع آگهی‌های معتبر (بعد از فیلتر): {len(listings)}")

    zero_count = sum(1 for l in listings if l["is_zero_km"])
    print(f"[info] تعداد آگهی‌های صفرکیلومتر شناسایی‌شده: {zero_count}")

    if not listings:
        send_telegram_message(bot_token, chat_id,
                               "ℹ️ در این اجرا هیچ آگهی نقدی معتبری پیدا نشد.")
        return

    table_year, table_name = build_zero_km_price_table(listings)

    deals = analyze_listings(listings, cfg.get("deal_threshold_percent", 4))

    new_deals = [d for d in deals if d["link"] not in sent_ids]

    print(f"[info] تعداد کل دیل‌های واجد شرایط: {len(deals)}")
    print(f"[info] تعداد دیل‌های جدید (قبلاً فرستاده نشده): {len(new_deals)}")

    if not new_deals:
        send_telegram_message(
            bot_token, chat_id,
            f"ℹ️ از بین {len(listings)} آگهی، {len(deals)} دیل واجد شرایط بود "
            f"ولی همه قبلاً فرستاده شده بودند."
        )
        return

    for deal in new_deals[:20]:
        msg = format_deal_message(deal, deal["link"], table_year, table_name)
        if send_telegram_message(bot_token, chat_id, msg):
            sent_ids.add(deal["link"])
        time.sleep(0.5)

    save_sent_ids(sent_ids)


if __name__ == "__main__":
    main()
