import asyncio
import httpx
import re
import os
from telegram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

REJECT_WORDS = [
    "اقساط","قسط","وام","لیزینگ","معاوضه",
    "تهاتر","نمایشگاه","توافقی","پیش پرداخت"
]

def is_valid(title: str, desc: str) -> bool:
    text = title + " " + desc
    for word in REJECT_WORDS:
        if word in text:
            return False
    return True

def parse_price(text: str) -> int:
    text = text.replace("٬","").replace(",","")
    nums = re.findall(r'\d+', text)
    if nums:
        p = int("".join(nums[:2]))
        if 80_000_000 <= p <= 80_000_000_000:
            return p
    return 0

async def scrape_divar() -> list:
    url = "https://api.divar.ir/v8/web-search/Tehran/car"
    headers = {"User-Agent": "Mozilla/5.0"}
    results = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json={}, headers=headers)
            data = r.json()
            posts = data.get("web_widgets", {}).get("post_list", [])
            for p in posts:
                d = p.get("data", {})
                title = d.get("title", "")
                desc = d.get("description", "")
                price_text = d.get("bottom_description", {}).get("text", "")
                price = parse_price(price_text)
                token = d.get("token", "")
                if price and is_valid(title, desc):
                    results.append({
                        "title": title,
                        "price": price,
                        "url": f"https://divar.ir/v/{token}"
                    })
    except Exception as e:
        print(f"Scrape error: {e}")
    return results

async def send_deals():
    bot = Bot(token=BOT_TOKEN)
    listings = await scrape_divar()
    count = 0
    for item in listings[:3]:
        price_m = item["price"] // 1_000_000
        msg = (
            f"🚗 {item['title']}\n"
            f"💰 {price_m:,} میلیون تومان\n"
            f"🔗 {item['url']}\n\n"
            f"⚡️ @SelectCar_ir"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=msg)
        count += 1
        await asyncio.sleep(2)
    print(f"Sent {count} deals")

async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_deals, "interval", minutes=30)
    scheduler.start()
    await send_deals()
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
