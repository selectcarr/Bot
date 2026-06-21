"""
SelectCar Bot - یک‌فایلی، ساده، قابل دیباگ سریع.
هر بار اجرا: اسکرپ دیوار -> فیلتر نویز -> تشخیص قیمت زیر بازار -> ارسال به کانال تلگرام -> خروج.
بدون حلقه بی‌پایان، بدون scheduler داخلی. زمان‌بندی را GitHub Actions cron انجام می‌دهد.
"""

import asyncio
import httpx
import re
import os
import statistics
from telegram import Bot
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# --- تنظیمات قابل تغییر ---
MIN_PRICE = 80_000_000
MAX_PRICE = 80_000_000_000
DISCOUNT_THRESHOLD = 0.90   # یعنی قیمت باید حداکثر ۹۰٪ میانگین بازار باشد (۱۰٪ یا بیشتر تخفیف)
MAX_MESSAGES_PER_RUN = 5
MIN_SAMPLE_FOR_MEDIAN = 5    # حداقل تعداد آگهی مشابه برای محاسبه قیمت بازار معتبر

REJECT_WORDS = [
    "اقساط", "قسط", "تسهیلات", "وام", "لیزینگ",
    "معاوضه", "تهاتر", "عوض",
    "نمایشگاه", "خریدار حرفه", "واسطه", "کارگزار",
]


PERSIAN_ARABIC_DIGITS = "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩"
LATIN_DIGITS = "01234567890123456789"
DIGIT_MAP = str.maketrans(PERSIAN_ARABIC_DIGITS, LATIN_DIGITS)


def normalize_text(text: str) -> str:
    """نرمال‌سازی ساده فارسی: ک/ی عربی به فارسی، اعداد به لاتین، حذف کاراکترهای کنترلی."""
    if not text:
        return ""
    text = text.replace("ك", "ک").replace("ي", "ی")
    text = text.translate(DIGIT_MAP)
    text = text.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return text.strip()


def is_valid(title: str, desc: str) -> bool:
    text = normalize_text(title) + " " + normalize_text(desc)
    for word in REJECT_WORDS:
        if word in text:
            return False
    return True


def parse_price(text: str) -> int:
    if not text:
        return 0
    text = normalize_text(text)
    text = text.replace("٬", "").replace(",", "")
    nums = re.findall(r"\d+", text)
    if not nums:
        return 0
    p = int("".join(nums[:2]))
    if MIN_PRICE <= p <= MAX_PRICE:
        return p
    return 0


def extract_model_key(title: str) -> str:
    """کلید ساده برای گروه‌بندی آگهی‌های مشابه جهت محاسبه میانگین بازار.
    فعلاً بر اساس کلمه اول و دوم عنوان (برند + مدل تقریبی)."""
    norm = normalize_text(title)
    words = norm.split()
    if len(words) >= 2:
        return f"{words[0]}_{words[1]}"
    elif len(words) == 1:
        return words[0]
    return "unknown"


async def scrape_divar() -> list:
  url = "https://api.divar.ir/v8/web-search/tehran/car"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://divar.ir",
    "Referer": "https://divar.ir/s/tehran/car",
}

    results = []

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json={}, headers=headers)
            print(f"[scrape] status_code={r.status_code}", flush=True)

            if r.status_code != 200:
                print(f"[scrape] bad response: {r.text[:300]}", flush=True)
                return results

            data = r.json()
            posts = data.get("web_widgets", {}).get("post_list", [])
            print(f"[scrape] total_posts={len(posts)}", flush=True)

            no_price = 0
            rejected = 0

            for p in posts:
                d = p.get("data", {})
                title = d.get("title", "")
                desc = d.get("description", "")
                price_text = d.get("bottom_description", {}).get("text", "")
                price = parse_price(price_text)
                token = d.get("token", "")
                has_image = bool(d.get("image_url") or d.get("image"))

                if not price:
                    no_price += 1
                    continue

                if not is_valid(title, desc):
                    rejected += 1
                    continue

                results.append({
                    "title": normalize_text(title),
                    "price": price,
                    "url": f"https://divar.ir/v/{token}",
                    "model_key": extract_model_key(title),
                    "has_image": has_image,
                })

            print(f"[scrape] no_price={no_price} rejected_by_keyword={rejected} passed_filter={len(results)}", flush=True)

    except httpx.RequestError as e:
        print(f"[scrape] network error: {e}", flush=True)
    except Exception as e:
        print(f"[scrape] unexpected error: {type(e).__name__}: {e}", flush=True)

    return results


def find_deals(listings: list) -> list:
    """گروه‌بندی بر اساس model_key، محاسبه میانگین (median)، و انتخاب آگهی‌هایی که
    حداقل DISCOUNT_THRESHOLD درصد زیر میانگین گروه خودشان هستند."""
    by_model = {}
    for item in listings:
        by_model.setdefault(item["model_key"], []).append(item)

    deals = []
    for model_key, items in by_model.items():
        if len(items) < MIN_SAMPLE_FOR_MEDIAN:
            continue

        prices = [i["price"] for i in items]
        median_price = statistics.median(prices)

        for item in items:
            if item["price"] <= median_price * DISCOUNT_THRESHOLD:
                discount_pct = round((1 - item["price"] / median_price) * 100, 1)
                deals.append({
                    **item,
                    "market_price": int(median_price),
                    "discount_pct": discount_pct,
                })

    deals.sort(key=lambda x: x["discount_pct"], reverse=True)
    print(f"[deals] groups_with_enough_samples={sum(1 for v in by_model.values() if len(v) >= MIN_SAMPLE_FOR_MEDIAN)} deals_found={len(deals)}", flush=True)
    return deals


def format_message(item: dict) -> str:
    price_m = item["price"] // 1_000_000
    market_m = item["market_price"] // 1_000_000
    return (
        f"🚗 {item['title']}\n"
        f"💰 قیمت: {price_m:,} میلیون تومان\n"
        f"📊 میانگین بازار: {market_m:,} میلیون تومان\n"
        f"🔥 تخفیف: {item['discount_pct']}٪\n"
        f"🔗 {item['url']}\n\n"
        f"⚡️ @SelectCar_ir"
    )


async def send_to_channel(deals: list) -> int:
    if not BOT_TOKEN or not CHANNEL_ID:
        print("[telegram] FATAL: BOT_TOKEN or CHANNEL_ID missing from environment", flush=True)
        return 0

    bot = Bot(token=BOT_TOKEN)

    try:
        me = await bot.get_me()
        print(f"[telegram] authenticated as @{me.username}", flush=True)
    except TelegramError as e:
        print(f"[telegram] FATAL auth error: {e}", flush=True)
        return 0

    sent = 0
    for item in deals[:MAX_MESSAGES_PER_RUN]:
        try:
            msg = format_message(item)
            result = await bot.send_message(chat_id=CHANNEL_ID, text=msg)
            print(f"[telegram] SENT id={result.message_id} -> {item['title'][:40]}", flush=True)
            sent += 1
        except TelegramError as e:
            print(f"[telegram] SEND FAILED for '{item['title'][:40]}': {e}", flush=True)
        except Exception as e:
            print(f"[telegram] UNEXPECTED ERROR: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(2)

    return sent


async def main():
    print("[main] === run started ===", flush=True)
    listings = await scrape_divar()

    if not listings:
        print("[main] no listings passed scraping/filter stage, exiting", flush=True)
        return

    deals = find_deals(listings)

    if not deals:
        print("[main] no deals met the discount threshold this run, exiting", flush=True)
        return

    sent = await send_to_channel(deals)
    print(f"[main] === run finished, {sent} message(s) sent ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
