#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات تحلیل قیمت خودرو از کانال‌های تلگرام
------------------------------------------
نحوه کار:
1. برای هر کانال در config.json صفحه‌ی عمومی https://t.me/s/<channel> با requests گرفته می‌شود.
2. با regex پست‌ها (متن + لینک مستقیم) استخراج می‌شوند.
3. هر پستی که کلمات ممنوعه (اقساط/لیزینگ/...) داشته باشد کامل حذف می‌شود.
4. از متن باقی‌مانده با regex قیمت، کارکرد و نام خودرو استخراج می‌شود.
5. آگهی‌ها بر اساس نام خودرو گروه‌بندی و میانگین قیمت هر گروه محاسبه می‌شود.
6. آگهی‌هایی که X درصد (deal_threshold_percent) زیر میانگین گروه خودشان هستند
   به عنوان "دیل مناسب" علامت‌گذاری و به تلگرام کاربر فرستاده می‌شوند.
"""

import os
import re
import json
import time
import sys
from statistics import mean

import requests

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ENGLISH_DIGITS = "0123456789"
DIGIT_MAP = str.maketrans(PERSIAN_DIGITS, ENGLISH_DIGITS)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
        if model in text:
            return model
    return None


def extract_first_match(pattern, text):
    m = re.search(pattern, text)
    if not m:
        return None
    return m.group(1)


def parse_listing(text, cfg):
    excluded_kw = contains_excluded_keyword(text, cfg["exclude_keywords"])
    if excluded_kw:
        return None, excluded_kw

    car_name = extract_car_name(text, cfg["car_models"])
    if not car_name:
        return None, "بدون نام خودرو شناسایی‌شده"

    price_raw = extract_first_match(cfg["regex_patterns"]["price"], text)
    price = normalize_number(price_raw)
    if price is None:
        return None, "قیمت پیدا نشد"

    unit_match = re.search(cfg["regex_patterns"]["price"], text)
    unit = unit_match.group(2) if unit_match else ""
    if "میلیون" in unit:
        price = price * 1_000_000
    elif "میلیارد" in unit:
        price = price * 1_000_000_000

    mileage_raw = extract_first_match(cfg["regex_patterns"]["mileage"], text)
    mileage = normalize_number(mileage_raw)

    year_raw = extract_first_match(cfg["regex_patterns"]["year_model"], text)

    return {
        "car_name": car_name,
        "model_year": year_raw,
        "price": price,
        "mileage": mileage,
        "raw_text": text
    }, None


def analyze_listings(listings, threshold_percent):
    groups = {}
    for l in listings:
        groups.setdefault(l["car_name"], []).append(l)

    deals = []
    for car_name, items in groups.items():
        prices = [i["price"] for i in items]
        avg_price = mean(prices)
        for i in items:
            diff_percent = (avg_price - i["price"]) / avg_price * 100
            i["avg_price_group"] = round(avg_price)
            i["diff_percent_vs_avg"] = round(diff_percent, 1)
            if diff_percent >= threshold_percent:
                deals.append(i)
    deals.sort(key=lambda x: x["diff_percent_vs_avg"], reverse=True)
    return deals


def format_deal_message(listing, link):
    price_toman = f"{listing['price']:,}"
    avg_toman = f"{listing['avg_price_group']:,}"
    lines = [
        f"🚗 {listing['car_name']}"
        + (f" - مدل {listing['model_year']}" if listing['model_year'] else ""),
        f"💰 قیمت آگهی: {price_toman} تومان",
        f"📊 میانگین بازار (همین گروه): {avg_toman} تومان",
        f"📉 اختلاف با میانگین: {listing['diff_percent_vs_avg']}٪ ارزان‌تر",
    ]
    if listing["mileage"] is not None:
        lines.append(f"🛣 کارکرد: {listing['mileage']:,} کیلومتر")
    lines.append(f"🔗 {link}")
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

    # .strip() برای حذف خودکار فاصله یا خط جدید اضافه که ممکنه
    # موقع کپی/پیست کردن مقدار Secret توی گیتهاب اضافه شده باشه.
    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not bot_token or not chat_id:
        print("[error] TELEGRAM_BOT_TOKEN یا TELEGRAM_CHAT_ID تنظیم نشده است.", file=sys.stderr)
        sys.exit(1)

    listings = collect_all_listings(cfg)
    print(f"[info] مجموع آگهی‌های معتبر (بعد از فیلتر): {len(listings)}")

    if not listings:
        send_telegram_message(bot_token, chat_id,
                               "ℹ️ در این اجرا هیچ آگهی نقدی معتبری پیدا نشد.")
        return

    deals = analyze_listings(listings, cfg.get("deal_threshold_percent", 10))
    print(f"[info] تعداد دیل‌های مناسب: {len(deals)}")

    if not deals:
        send_telegram_message(bot_token, chat_id,
                               f"ℹ️ از بین {len(listings)} آگهی، هیچ‌کدام {cfg.get('deal_threshold_percent',10)}٪ "
                               f"یا بیشتر زیر میانگین گروه خودشان نبودند.")
        return

    for deal in deals[:20]:
        msg = format_deal_message(deal, deal["link"])
        send_telegram_message(bot_token, chat_id, msg)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
