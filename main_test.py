"""
SelectCar Bot - TEST MODE
این نسخه از داده‌ی نمونه‌ی ثابت (نه اسکرپ زنده) استفاده می‌کند تا مسیر تلگرام
(توکن، دسترسی ادمین کانال، فرمت پیام) را قطعی و سریع تست کنیم.
بعد از تأیید موفق، می‌رویم سراغ داده‌ی واقعی دیوار.
"""

import asyncio
import os
from telegram import Bot
from telegram.error import TelegramError

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# --- داده‌ی نمونه‌ی ثابت، فقط برای تست ---
SAMPLE_DEALS = [
    {
        "title": "پژو ۲۰۶ تیپ ۲ مدل ۱۴۰۰",
        "price": 450_000_000,
        "market_price": 530_000_000,
        "discount_pct": 15.1,
        "url": "https://divar.ir/v/test-sample-1",
    },
    {
        "title": "پراید ۱۱۱ مدل ۱۳۹۹",
        "price": 280_000_000,
        "market_price": 320_000_000,
        "discount_pct": 12.5,
        "url": "https://divar.ir/v/test-sample-2",
    },
]


def format_message(item: dict) -> str:
    price_m = item["price"] // 1_000_000
    market_m = item["market_price"] // 1_000_000
    return (
        f"🧪 [TEST] 🚗 {item['title']}\n"
        f"💰 قیمت: {price_m:,} میلیون تومان\n"
        f"📊 میانگین بازار: {market_m:,} میلیون تومان\n"
        f"🔥 تخفیف: {item['discount_pct']}٪\n"
        f"🔗 {item['url']}\n\n"
        f"⚡️ @SelectCar_ir"
    )


async def main():
    print("[main] === TEST RUN started (sample data, no scraping) ===", flush=True)

    if not BOT_TOKEN:
        print("[telegram] FATAL: TELEGRAM_BOT_TOKEN is missing", flush=True)
        return
    if not CHANNEL_ID:
        print("[telegram] FATAL: TELEGRAM_CHANNEL_ID is missing", flush=True)
        return

    print(f"[telegram] token_len={len(BOT_TOKEN)} channel_id={CHANNEL_ID}", flush=True)

    bot = Bot(token=BOT_TOKEN)

    try:
        me = await bot.get_me()
        print(f"[telegram] auth OK -> bot username = @{me.username}", flush=True)
    except TelegramError as e:
        print(f"[telegram] FATAL auth error: {type(e).__name__}: {e}", flush=True)
        return

    sent = 0
    for item in SAMPLE_DEALS:
        msg = format_message(item)
        try:
            result = await bot.send_message(chat_id=CHANNEL_ID, text=msg)
            print(f"[telegram] SENT OK message_id={result.message_id} title={item['title']}", flush=True)
            sent += 1
        except TelegramError as e:
            print(f"[telegram] SEND FAILED for '{item['title']}': {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(2)

    print(f"[main] === TEST RUN finished, sent {sent}/{len(SAMPLE_DEALS)} messages ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
