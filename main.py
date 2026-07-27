#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from statistics import mean

import requests

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
SENT_IDS_PATH = os.path.join(APP_DIR, "sent_ids.json")
DAILY_STATE_PATH = os.path.join(APP_DIR, "daily_state.json")
MAX_SENT_IDS = 2000

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))
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


def load_daily_state():
    if not os.path.exists(DAILY_STATE_PATH):
        return {"last_daily_post_date": None}
    try:
        with open(DAILY_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"last_daily_post_date": None}


def save_daily_state(state):
    with open(DAILY_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def today_iran_date_str():
    return datetime.now(IRAN_TZ).date().isoformat()


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
    best_idx, best_model = None, None
    for model in car_models:
        m = re.search(r"\b" + re.escape(model) + r"\b", text)
        if not m:
            continue
        if (best_idx is None or m.start() < best_idx or
                (m.start() == best_idx and len(model) > len(best_model))):
            best_idx, best_model = m.start(), model

    if best_model:
        remainder = text[best_idx + len(best_model):]
        trim_match = re.match(r"\s+([A-Za-z][A-Za-z0-9\-]{1,8})\b", remainder)
        if trim_match:
            return f"{best_model} {trim_match.group(1)}"
        return best_model

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
    ascii_text = text.translate(DIGIT_MAP)
    m = re.search(r"(?:مدل|سال)\s*[:：]?\s*(\d{2,4})", ascii_text)
    if not m:
        return None
    raw = m.group(1)
    n = int(raw)
    if len(raw) == 4:
        if 1300 <= n <= 1499 or 2000 <= n <= 2099:
            return raw
        return None
    if len(raw) == 2:
        if 0 <= n <= 30:
            return str(1400 + n)
        if 70 <= n <= 99:
            return str(1300 + n)
        return None
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
        return None, "قیمت پیدا نشد"

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


def lookup_zero_km_price(car_name, yv_price_table):
    if not yv_price_table:
        return None
    for key, price in yv_price_table.items():
        if car_name.startswith(key) or key in car_name:
            return price
    return None


def analyze_listings(listings, threshold_percent, max_discount_percent=12):
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
                if threshold_percent <= diff_percent <= max_discount_percent:
                    deals.append(i)

    deals.sort(key=lambda x: x["diff_percent_vs_avg"], reverse=True)
    return deals


def format_toman(value):
    if value is None:
        return "پیدا نشد"
    return f"{value:,}".replace(",", "٬") + " تومان"


def format_deal_message(listing, link, yv_price_table, support_admin):
    zero_price = lookup_zero_km_price(listing["car_name"], yv_price_table)

    lines = [
        f"نام خودرو : {listing['car_name']}",
        f"مدل خودرو : {listing['model_year'] or 'نامشخص'}",
        f"قیمت صفر : {format_toman(zero_price)}",
        f"قیمت میانگین این مدل : {format_toman(listing['avg_price_group'])}",
        f"قیمت این خودرو : {format_toman(listing['price'])}",
        f"درصد قیمت زیر میانگین : {listing['diff_percent_vs_avg']}٪",
        f"<b>ادمین پشتیبانی : {support_admin}</b>",
        f"لینک آگهی : {link}",
    ]
    return "\n".join(lines)


def send_telegram_message(bot_token, chat_id, text, parse_mode=None):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False
    }
    if parse_mode:
        data["parse_mode"] = parse_mode
    resp = requests.post(url, data=data, timeout=15)
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


PRICE_LIST_KEYWORDS = ("پرفروش",)
CURRENCY_KEYWORDS = ("دلار", "طلا")


def find_latest_message_with_keywords(messages, keywords):
    match = None
    for msg in messages:
        if all(kw in msg["text"] for kw in keywords):
            match = msg
    return match


def build_yv_price_table(price_list_text, car_models):
    table = {}
    for line in price_list_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # فقط خطوطی که — یا – دارن معتبرن
        separator = None
        if "—" in line:
            separator = "—"
        elif "–" in line:
            separator = "–"
        else:
            continue

        parts = line.split(separator)
        if len(parts) < 2:
            continue

        price_part = parts[-1].strip()
        name_part = parts[0].strip()

        # پیدا کردن مدل خودرو در قسمت نام
        matched_model = None
        for model in car_models:
            if model in name_part:
                matched_model = model
                break
        if not matched_model:
            continue

        # استخراج قیمت فقط از قسمت بعد از —
        num_match = re.search(
            r"([۰-۹0-9][۰-۹0-9,،.٬\s]{0,10}[۰-۹0-9]|[۰-۹0-9]{3,6})",
            price_part
        )
        if not num_match:
            continue

        n = normalize_number(num_match.group(1))
        if n and matched_model not in table:
            table[matched_model] = n * 1_000_000 if n < 10_000 else n

    return table


SOURCE_SIGNATURE_RE = re.compile(r"\n?@[A-Za-z0-9_]+\s*$")


def strip_source_signature(text):
    return SOURCE_SIGNATURE_RE.sub("", text).rstrip()


def maybe_send_daily_price_list(cfg, bot_token, chat_id):
    daily_channel = cfg.get("daily_price_channel")
    zero_channels = cfg.get("zero_price_channels", [daily_channel] if daily_channel else [])
    support_admin = cfg.get("support_admin", "@chanelll_vip")
    if not daily_channel:
        return {}

    state = load_daily_state()
    today = today_iran_date_str()

    yv_price_table = {}
    combined_price_table = {}

    all_messages = []
    for ch in zero_channels:
        try:
            html = fetch_channel_html(ch)
            messages = parse_channel_messages(html, ch)
            all_messages.extend(messages)
        except Exception as e:
            print(f"[warn] خطا در گرفتن کانال {ch}: {e}", file=sys.stderr)

    yv_messages = [m for m in all_messages if m["channel"] == daily_channel]
    price_msg = find_latest_message_with_keywords(yv_messages, PRICE_LIST_KEYWORDS)
    currency_msg = find_latest_message_with_keywords(yv_messages, CURRENCY_KEYWORDS)

    if price_msg:
        yv_price_table = build_yv_price_table(price_msg["text"], cfg["car_models"])
        combined_price_table.update(yv_price_table)

    akhbar_messages = [m for m in all_messages if m["channel"] != daily_channel]
    akhbar_price_msg = find_latest_message_with_keywords(
        akhbar_messages, ("پرفروش",)
    ) or (akhbar_messages[0] if akhbar_messages else None)

    if akhbar_price_msg:
        akhbar_table = build_yv_price_table(akhbar_price_msg["text"], cfg["car_models"])
        for model, price in akhbar_table.items():
            if model not in combined_price_table:
                combined_price_table[model] = price

    if state.get("last_daily_post_date") == today:
        print("[info] لیست روزانه قبلاً امروز فرستاده شده؛ دوباره پست نمیشود.")
        return combined_price_table

    admin_line = f"\n\n<b>ادمین پشتیبانی : {support_admin}</b>"

    sent_something = False
    if price_msg:
        clean_text = strip_source_signature(price_msg["text"]) + admin_line
        if send_telegram_message(bot_token, chat_id, clean_text, parse_mode="HTML"):
            sent_something = True
        time.sleep(0.5)
    if currency_msg:
        clean_text = strip_source_signature(currency_msg["text"]) + admin_line
        if send_telegram_message(bot_token, chat_id, clean_text, parse_mode="HTML"):
            sent_something = True
        time.sleep(0.5)

    if akhbar_price_msg and combined_price_table:
        extra_models = {k: v for k, v in combined_price_table.items() if k not in yv_price_table}
        if extra_models:
            extra_text = "📋 قیمت خودروهای تکمیلی\n━━━━━━━━━━━━━━━━\n"
            for model, price in extra_models.items():
                extra_text += f"🚗 {model} — {price // 1_000_000:,} میلیون\n"
            extra_text += admin_line
            if send_telegram_message(bot_token, chat_id, extra_text, parse_mode="HTML"):
                sent_something = True

    if sent_something:
        state["last_daily_post_date"] = today
        save_daily_state(state)
        print("[info] لیست روزانه با موفقیت فرستاده شد.")
    else:
        print("[warn] هیچ پست مناسبی پیدا نشد.")

    return combined_price_table


def main():
    cfg = load_config()

    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not bot_token or not chat_id:
        print("[error] TELEGRAM_BOT_TOKEN یا TELEGRAM_CHAT_ID تنظیم نشده است.", file=sys.stderr)
        sys.exit(1)

    support_admin = cfg.get("support_admin", "@chanelll_vip")
    sent_ids = load_sent_ids()

    yv_price_table = maybe_send_daily_price_list(cfg, bot_token, chat_id)

    listings = collect_all_listings(cfg)
    print(f"[info] مجموع آگهیهای معتبر (بعد از فیلتر): {len(listings)}")

    zero_count = sum(1 for l in listings if l["is_zero_km"])
    print(f"[info] تعداد آگهی‌های صفرکیلومتر شناساییشده: {zero_count}")

    if not listings:
        send_telegram_message(bot_token, chat_id,
                              "ℹ️ در این اجرا هیچ آگهی نقدی معتبری پیدا نشد.")
        return

    max_discount = cfg.get("deal_max_discount_percent", 12)
    deals = analyze_listings(
        listings,
        cfg.get("deal_threshold_percent", 4),
        max_discount
    )

    new_deals = [d for d in deals if d["link"] not in sent_ids]

    print(f"[info] تعداد کل دیل‌های واجد شرایط: {len(deals)}")
    print(f"[info] تعداد دیل‌های جدید: {len(new_deals)}")

    if not new_deals:
        send_telegram_message(
            bot_token, chat_id,
            f"ℹ️ از بین {len(listings)} آگهی، {len(deals)} دیل واجد شرایط بود "
            f"ولی همه قبلاً فرستاده شده بودند."
        )
        return

    for deal in new_deals[:20]:
        msg = format_deal_message(deal, deal["link"], yv_price_table, support_admin)
        if send_telegram_message(bot_token, chat_id, msg, parse_mode="HTML"):
            sent_ids.add(deal["link"])
        time.sleep(0.5)

    save_sent_ids(sent_ids)


if __name__ == "__main__":
    main()
