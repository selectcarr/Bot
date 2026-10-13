#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import select_carr_stage4 as stage4


VERSION = "1.0.1"
ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.getenv("SELECT_CARR_CONTENT_RUNTIME", ROOT / "content_runtime"))
STATE_PATH = RUNTIME_DIR / "state.json"
MANIFEST_PATH = RUNTIME_DIR / "manifest.json"
STAGE4_STATE_PATH = RUNTIME_DIR / "stage4_publish_state.json"

TEHRAN = ZoneInfo("Asia/Tehran")

TELEGRAM_BOT_TOKEN = os.getenv(
    "CONTENT_TELEGRAM_BOT_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = (
    os.getenv(
        "CONTENT_TELEGRAM_CHAT_ID",
        "-1004444202669",
    ).strip()
    or "-1004444202669"
)

EXPECTED_BOT_USERNAME = (
    os.getenv("CONTENT_EXPECTED_BOT_USERNAME", "Selectcaranalyzbot_bot")
    .strip()
    .lstrip("@")
)


def now_tehran() -> datetime:
    return datetime.now(TEHRAN)


def today_key() -> str:
    return now_tehran().date().isoformat()


def clean(value: object, max_chars: int = 180) -> str:
    text = str(value or "").replace("\u200c", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars].strip()


def fa_num(value: object) -> str:
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def money_toman(value: object) -> str:
    try:
        number = int(float(str(value or "0").replace(",", "")))
    except Exception:
        return "نامشخص"

    if number >= 1_000_000_000:
        x = number / 1_000_000_000
        shown = f"{x:.1f}".rstrip("0").rstrip(".")
        return f"{fa_num(shown)} میلیارد تومان"

    if number >= 1_000_000:
        x = number / 1_000_000
        shown = f"{x:.0f}" if x.is_integer() else f"{x:.1f}".rstrip("0").rstrip(".")
        return f"{fa_num(shown)} میلیون تومان"

    return f"{fa_num(f'{number:,}')} تومان"


def display_rate(row: dict[str, Any] | None) -> str:
    if not row:
        return "نامشخص"
    display = clean(row.get("display"))
    unit = clean(row.get("unit"))
    return f"{fa_num(display)} {unit}".strip()


def load_state() -> dict[str, Any]:
    default = {
        "version": 1,
        "sent": {},
        "last_reel_family": "",
        "last_post_family": "",
        "instagram_notification_initialized": False,
        "instagram_notified_tokens": {},
    }
    if not STATE_PATH.is_file():
        return default

    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default

    if not isinstance(payload, dict):
        return default

    result = dict(default)
    result.update(payload)

    if not isinstance(result.get("sent"), dict):
        result["sent"] = {}

    if not isinstance(result.get("instagram_notified_tokens"), dict):
        result["instagram_notified_tokens"] = {}

    return result


def save_state(state: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state["version"] = 1
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def collect_context() -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "market": None,
        "cars": None,
        "deal": None,
        "buyer": None,
        "source_posts_scanned": 0,
        "warnings": [],
    }

    posts = None

    try:
        posts = stage4.telegram_posts()
        ctx["source_posts_scanned"] = len(posts)
    except Exception as exc:
        ctx["warnings"].append(f"telegram_source:{type(exc).__name__}:{exc}")

    if posts is not None:
        try:
            ctx["market"] = stage4.latest_market_prices(
                posts,
                allow_previous_day=True,
            )
        except Exception as exc:
            ctx["warnings"].append(f"market:{type(exc).__name__}:{exc}")

        try:
            ctx["cars"] = stage4.latest_zero_car_prices(
                posts,
                allow_previous_day=True,
            )
        except Exception as exc:
            ctx["warnings"].append(f"cars:{type(exc).__name__}:{exc}")

    try:
        ctx["deal"] = stage4.latest_market_deal()
    except Exception as exc:
        ctx["warnings"].append(f"deal:{type(exc).__name__}:{exc}")

    try:
        ctx["buyer"] = stage4.latest_buy_request()
    except Exception as exc:
        ctx["warnings"].append(f"buyer:{type(exc).__name__}:{exc}")

    return ctx


def deal_title(deal: dict[str, Any]) -> str:
    try:
        title = stage4.compact_vehicle_title(deal)
    except Exception:
        title = " ".join(
            x for x in (
                clean(deal.get("brand"), 30),
                clean(deal.get("model"), 40),
                clean(deal.get("trim"), 30),
                clean(deal.get("model_year"), 8),
            )
            if x
        )
    return clean(title or "خودرو", 70)


def discount_text(deal: dict[str, Any]) -> str:
    value = deal.get("discount_percent")
    try:
        number = float(value)
        shown = f"{number:.1f}".rstrip("0").rstrip(".")
        return f"{fa_num(shown)}٪"
    except Exception:
        return "چند درصد"


def choose_family(
    kind: str,
    ctx: dict[str, Any],
    state: dict[str, Any],
) -> str:
    available: list[str] = []

    if ctx.get("deal"):
        available.append("deal")
    if ctx.get("buyer"):
        available.append("buyer")
    if ctx.get("market"):
        available.append("market")
    if ctx.get("cars"):
        available.append("cars")

    if not available:
        return "evergreen"

    state_key = "last_reel_family" if kind == "reel" else "last_post_family"
    previous = str(state.get(state_key) or "")

    if len(available) > 1 and previous in available:
        start = (available.index(previous) + 1) % len(available)
        return available[start]

    return available[0]


def reel_from_deal(deal: dict[str, Any]) -> str:
    title = deal_title(deal)
    price = money_toman(deal.get("candidate_price"))
    average = money_toman(deal.get("average_price"))
    discount = discount_text(deal)
    sample_count = int(deal.get("sample_count") or 0)

    proof = (
        f"این آگهی با {fa_num(sample_count)} نمونه مشابه مقایسه شده."
        if sample_count > 0
        else "این آگهی با نمونه‌های مشابه بازار مقایسه شده."
    )

    return f"""🎬 بسته ریلز آماده اجرا

هدف: جذب خریدار جدی + اثبات توان تحلیل Select Carr

عنوان کاور:
«این خودرو واقعاً زیر قیمت بازاره؟»

هوک ۰ تا ۳ ثانیه:
«هر آگهی ارزون، فرصت خرید نیست؛ ولی این یکی چرا توسط سیستم ما علامت خورد؟»

پلان ۱ — ۳ تا ۸ ثانیه:
روی تصویر بنویس:
{title}
قیمت آگهی: {price}

گویندگی:
«این {title} با قیمت {price} وارد سیستم ما شد.»

پلان ۲ — ۸ تا ۱۵ ثانیه:
روی تصویر:
میانگین مقایسه بازار: {average}
اختلاف: {discount}

گویندگی:
«سیستم، قیمتش را با بازار مقایسه کرد؛ میانگین نمونه‌های مشابه حدود {average} بود و اختلاف به {discount} رسید.»

پلان ۳ — ۱۵ تا ۲۲ ثانیه:
روی تصویر:
«قیمت پایین کافی نیست»

گویندگی:
«اما فقط پایین بودن قیمت کافی نیست. وضعیت خودرو، سال، کارکرد و کیفیت نمونه‌های مقایسه هم باید قابل قبول باشد.»

پلان ۴ — ۲۲ تا ۳۰ ثانیه:
روی تصویر:
«فرصت واقعی = قیمت + داده + بررسی»

گویندگی:
«{proof} نتیجه این بررسی، همان فرصت‌هایی است که در Select Carr می‌بینید.»

دعوت به اقدام:
«اگر خریدار هستی کلمه «خرید» و اگر فروشنده‌ای کلمه «فروش» را بفرست.»

کپشن:
هر خودروی ارزان‌قیمت، فرصت خرید نیست.
ما آگهی را با نمونه‌های واقعی بازار مقایسه می‌کنیم تا اختلاف قیمت قابل دفاع باشد.

خودرو: {title}
قیمت آگهی: {price}
میانگین مقایسه: {average}
اختلاف: {discount}

برای درخواست خرید یا فروش، پیام بده.
@select_carr
@select_carrr

منبع محتوا: Deal واقعی سیستم Select Carr
"""


def reel_from_buyer(buyer: dict[str, Any]) -> str:
    model = clean(buyer.get("model"), 70) or "خودروی مشخص"
    budget = money_toman(buyer.get("budget"))
    urgency = clean(buyer.get("urgency"), 30) or "فعال"

    return f"""🎬 بسته ریلز آماده اجرا

هدف: جذب فروشنده واقعی از دل تقاضای موجود

عنوان کاور:
«برای این خودرو خریدار واقعی داریم»

هوک ۰ تا ۳ ثانیه:
«اگر {model} داری، این ویدئو ممکنه برای خودت باشه.»

پلان ۱ — ۳ تا ۹ ثانیه:
روی تصویر:
درخواست خرید فعال
{model}

گویندگی:
«داخل سیستم Select Carr یک درخواست خرید فعال برای {model} ثبت شده.»

پلان ۲ — ۹ تا ۱۵ ثانیه:
روی تصویر:
بودجه تا {budget}

گویندگی:
«قدرت خرید ثبت‌شده تا {budget} است و وضعیت درخواست: {urgency}.»

پلان ۳ — ۱۵ تا ۲۲ ثانیه:
روی تصویر:
«آگهی بیشتر نمی‌خواهیم؛ تطبیق واقعی می‌خواهیم»

گویندگی:
«کار ما فقط انتشار آگهی نیست؛ فروشنده مناسب را به خریدار متناسب وصل می‌کنیم.»

پلان ۴ — ۲۲ تا ۳۰ ثانیه:
روی تصویر:
«{model} داری؟»

گویندگی:
«اگر چنین خودرویی داری و قصد فروش واقعی داری، مشخصاتش را برای ما بفرست.»

دعوت به اقدام:
«کلمه «فروش» را دایرکت کن یا فرم فروش را تکمیل کن.»

کپشن:
خریدار واقعی وقتی ارزش دارد که به فروشنده مناسب برسد.

درخواست فعال:
{model}
بودجه: {budget}
اولویت: {urgency}

اگر خودروی متناسب داری، مشخصات را ارسال کن.
@select_carr
@select_carrr

منبع محتوا: درخواست خرید واقعی ثبت‌شده در سیستم
"""


def reel_from_market(market: dict[str, Any]) -> str:
    usd = display_rate(market.get("usd"))
    gold = display_rate(market.get("gold18"))
    coin = display_rate(market.get("coin"))

    return f"""🎬 بسته ریلز آماده اجرا

هدف: محتوای روز + افزایش ذخیره و اشتراک‌گذاری

عنوان کاور:
«دلار بالا رفت؛ خودرو هم باید فوراً گرون بشه؟»

هوک ۰ تا ۳ ثانیه:
«یک اشتباه رایج: هر حرکت دلار را مستقیم روی قیمت ماشین ضرب نکن.»

پلان ۱ — ۳ تا ۸ ثانیه:
روی تصویر:
دلار: {usd}
طلای ۱۸ عیار: {gold}

گویندگی:
«این‌ها نرخ‌های امروز بازارند؛ اما بازار خودرو همیشه با همان سرعت واکنش نشان نمی‌دهد.»

پلان ۲ — ۸ تا ۱۵ ثانیه:
روی تصویر:
«قیمت آگهی ≠ قیمت معامله»

گویندگی:
«فروشنده ممکن است قیمت را سریع عوض کند، ولی چیزی که مهم است قیمت معامله و تعداد خریدار واقعی است.»

پلان ۳ — ۱۵ تا ۲۲ ثانیه:
روی تصویر:
سکه: {coin}

گویندگی:
«برای تصمیم خرید خودرو، کنار ارز و طلا باید آگهی‌های مشابه و رفتار واقعی بازار خودرو را هم ببینی.»

پلان ۴ — ۲۲ تا ۳۰ ثانیه:
روی تصویر:
«قبل از خرید، مقایسه کن»

گویندگی:
«Select Carr همین مقایسه را روی چند منبع انجام می‌دهد تا فرصت واقعی از قیمت‌سازی جدا شود.»

دعوت به اقدام:
«این ویدئو را ذخیره کن؛ برای قیمت و فرصت‌های واقعی بازار صفحه را دنبال کن.»

کپشن:
حرکت دلار مهم است، اما تنها متغیر بازار خودرو نیست.

دلار: {usd}
طلای ۱۸ عیار: {gold}
سکه: {coin}

ما قیمت آگهی را با نمونه‌های واقعی بازار مقایسه می‌کنیم.
@select_carr
@select_carrr

منبع محتوا: آخرین نرخ معتبر منبع بازار
"""


def reel_from_cars(cars: dict[str, Any]) -> str:
    items = list(cars.get("items") or [])[:3]
    rows = []
    for item in items:
        rows.append(
            f"{clean(item.get('name'), 45)} — {fa_num(item.get('display_price') or '')} میلیون تومان"
        )
    prices = "\n".join(rows) or "لیست قیمت معتبر خودرو"

    return f"""🎬 بسته ریلز آماده اجرا

هدف: محتوای قیمت‌محور با قابلیت ذخیره و ارسال

عنوان کاور:
«۳ قیمت خودرو که امروز باید ببینی»

هوک ۰ تا ۳ ثانیه:
«اگر این روزها قصد خرید داری، قبل از تماس با فروشنده این سه قیمت را ببین.»

پلان ۱ — ۳ تا ۱۴ ثانیه:
روی تصویر:
{prices}

گویندگی:
«این سه مورد از آخرین لیست معتبر قیمت خودرو استخراج شده‌اند.»

پلان ۲ — ۱۴ تا ۲۱ ثانیه:
روی تصویر:
«یک عدد به‌تنهایی کافی نیست»

گویندگی:
«قیمت روز نقطه شروع است؛ برای خرید خوب باید همان مدل، سال و شرایط واقعی آگهی‌ها هم مقایسه شود.»

پلان ۳ — ۲۱ تا ۳۰ ثانیه:
روی تصویر:
«قیمت روز + فرصت خرید واقعی»

گویندگی:
«در Select Carr هم قیمت روز را می‌بینی و هم آگهی‌هایی که نسبت به بازار فرصت محسوب می‌شوند.»

دعوت به اقدام:
«برای اینکه قیمت‌های بعدی را از دست ندهی، صفحه را ذخیره و دنبال کن.»

کپشن:
قیمت روز فقط نقطه شروع تصمیم است.

{prices}

بعد از قیمت، مهم‌ترین مرحله مقایسه با نمونه‌های واقعی بازار است.
@select_carr
@select_carrr

منبع محتوا: آخرین لیست معتبر قیمت خودرو
"""


def reel_evergreen() -> str:
    return """🎬 بسته ریلز آماده اجرا

هدف: آموزش + جذب خریدار و فروشنده

عنوان کاور:
«۳ نشانه که یک آگهی ارزان، لزوماً فرصت خرید نیست»

هوک ۰ تا ۳ ثانیه:
«قیمت پایین دیدی؟ هنوز برای تماس عجله نکن.»

پلان ۱:
«اول: با مدل و سال دقیق خودش مقایسه کن، نه فقط اسم خودرو.»

پلان ۲:
«دوم: رنگ، بدنه، کارکرد و وضعیت فنی می‌تواند کل اختلاف قیمت را توضیح بدهد.»

پلان ۳:
«سوم: یک آگهی ملاک بازار نیست؛ چند نمونه معتبر لازم داری.»

جمع‌بندی:
«فرصت خرید واقعی یعنی قیمت مناسب + مقایسه درست + وضعیت قابل قبول.»

دعوت به اقدام:
«اگر می‌خواهی بازار را با داده دنبال کنی، Select Carr را دنبال کن.»

کپشن:
ارزان بودن با فرصت خرید بودن فرق دارد.
برای تصمیم درست، مقایسه باید روی نمونه‌های واقعی و هم‌گروه انجام شود.

@select_carr
@select_carrr
"""


def build_reel(ctx: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
    family = choose_family("reel", ctx, state)

    if family == "deal":
        text = reel_from_deal(ctx["deal"])
    elif family == "buyer":
        text = reel_from_buyer(ctx["buyer"])
    elif family == "market":
        text = reel_from_market(ctx["market"])
    elif family == "cars":
        text = reel_from_cars(ctx["cars"])
    else:
        text = reel_evergreen()

    return family, text


def post_from_market(market: dict[str, Any]) -> str:
    usd = display_rate(market.get("usd"))
    gold = display_rate(market.get("gold18"))
    coin = display_rate(market.get("coin"))
    ounce = display_rate(market.get("ounce"))

    return f"""🖼 بسته پست کاروسل آماده طراحی

هدف: ذخیره‌سازی بالا + مرجع روزانه

تیتر کاور:
«امروز بازار چه می‌گوید؟»

اسلاید ۱:
امروز بازار چه می‌گوید؟
۴ عددی که قبل از تصمیم مالی باید ببینی

اسلاید ۲:
دلار
{usd}

متن کوتاه:
حرکت دلار مهم است، اما اثرش روی خودرو همیشه فوری و یک‌به‌یک نیست.

اسلاید ۳:
طلای ۱۸ عیار
{gold}

اسلاید ۴:
سکه
{coin}

اسلاید ۵:
اونس
{ounce}

اسلاید ۶:
برای بازار خودرو فقط این اعداد کافی نیست.
قیمت آگهی، تعداد خریدار و نمونه‌های مشابه باید کنار هم دیده شوند.

اسلاید آخر:
Select Carr
قیمت روز + مقایسه بازار + فرصت‌های واقعی خرید

دعوت به اقدام روی اسلاید آخر:
«پست را ذخیره کن و برای کسی که این روزها قصد خرید خودرو دارد بفرست.»

کپشن:
اعداد امروز بازار:
دلار: {usd}
طلای ۱۸ عیار: {gold}
سکه: {coin}
اونس: {ounce}

برای تصمیم در بازار خودرو، نرخ ارز فقط یکی از ورودی‌هاست.
ما آگهی‌ها را با نمونه‌های واقعی بازار هم مقایسه می‌کنیم.

@select_carr
@select_carrr

منبع محتوا: آخرین نرخ معتبر منبع بازار
"""


def post_from_cars(cars: dict[str, Any]) -> str:
    items = list(cars.get("items") or [])[:5]
    slides = []
    for index, item in enumerate(items, start=2):
        name = clean(item.get("name"), 50)
        price = fa_num(item.get("display_price") or "")
        slides.append(
            f"اسلاید {fa_num(index)}:\n{name}\n{price} میلیون تومان"
        )

    rows = "\n\n".join(slides) or "اسلاید ۲:\nآخرین لیست معتبر قیمت خودرو"

    return f"""🖼 بسته پست کاروسل آماده طراحی

هدف: ذخیره و اشتراک‌گذاری + جذب مخاطب خرید

تیتر کاور:
«۵ قیمت خودرو که امروز باید داشته باشی»

اسلاید ۱:
۵ قیمت خودرو که امروز باید داشته باشی
قبل از خرید، این پست را ذخیره کن

{rows}

اسلاید آخر:
عدد روز مهم است؛
اما فرصت خرید واقعی وقتی مشخص می‌شود که قیمت آگهی با نمونه‌های مشابه مقایسه شود.

دعوت به اقدام:
«مدلی که می‌خواهی قیمتش را دنبال کنیم در کامنت بنویس.»

کپشن:
۵ قیمت از آخرین لیست معتبر بازار خودرو.

این اعداد برای دیدن جهت بازار مفیدند، اما تصمیم خرید باید با مقایسه نمونه‌های واقعی تکمیل شود.

مدل موردنظرت را کامنت کن.
@select_carr
@select_carrr

منبع محتوا: آخرین لیست معتبر قیمت خودرو
"""


def post_from_deal(deal: dict[str, Any]) -> str:
    title = deal_title(deal)
    price = money_toman(deal.get("candidate_price"))
    average = money_toman(deal.get("average_price"))
    discount = discount_text(deal)
    sample_count = int(deal.get("sample_count") or 0)

    return f"""🖼 بسته پست کاروسل آماده طراحی

هدف: آموزش روش Select Carr + اعتمادسازی

تیتر کاور:
«چطور یک فرصت خرید را تشخیص می‌دهیم؟»

اسلاید ۱:
چطور یک فرصت خرید را تشخیص می‌دهیم؟

اسلاید ۲:
نمونه واقعی:
{title}

اسلاید ۳:
قیمت آگهی
{price}

اسلاید ۴:
میانگین نمونه‌های مقایسه
{average}

اسلاید ۵:
اختلاف ثبت‌شده
{discount}

اسلاید ۶:
تعداد نمونه مقایسه
{fa_num(sample_count) if sample_count else "چند"} نمونه معتبر

اسلاید ۷:
چرا هنوز نمی‌گوییم «حتماً بخر»؟
چون وضعیت بدنه، کارکرد و سلامت فنی باید با اختلاف قیمت هم‌خوان باشد.

اسلاید آخر:
فرصت خرید واقعی =
قیمت مناسب + داده کافی + بررسی وضعیت

دعوت به اقدام:
«اگر دنبال خودرو هستی، درخواست خریدت را ثبت کن.»

کپشن:
ما فقط دنبال ارزان‌ترین آگهی نیستیم.
هدف، پیدا کردن اختلاف قیمتی است که با داده بازار قابل دفاع باشد.

نمونه: {title}
قیمت آگهی: {price}
میانگین مقایسه: {average}
اختلاف: {discount}

@select_carr
@select_carrr

منبع محتوا: Deal واقعی سیستم Select Carr
"""


def post_from_buyer(buyer: dict[str, Any]) -> str:
    model = clean(buyer.get("model"), 70) or "خودروی مشخص"
    budget = money_toman(buyer.get("budget"))
    urgency = clean(buyer.get("urgency"), 30) or "فعال"

    return f"""🖼 بسته پست کاروسل آماده طراحی

هدف: جذب فروشنده برای تقاضای واقعی

تیتر کاور:
«بازار فقط فروشنده نیست؛ خریدار واقعی هم داریم»

اسلاید ۱:
یک درخواست خرید واقعی در سیستم ثبت شده

اسلاید ۲:
خودروی موردنظر:
{model}

اسلاید ۳:
بودجه:
تا {budget}

اسلاید ۴:
اولویت خرید:
{urgency}

اسلاید ۵:
اگر خودروی متناسب داری:
مشخصات را ارسال کن
→ بررسی قیمت
→ تطبیق با خریدار
→ هماهنگی معامله

اسلاید آخر:
Select Carr
وصل کردن تقاضای واقعی به فروشنده واقعی

دعوت به اقدام:
«اگر فروشنده این مدل هستی، کلمه «فروش» را پیام بده.»

کپشن:
یک درخواست خرید واقعی برای {model} در سیستم فعال است.
بودجه ثبت‌شده: {budget}

اگر خودروی متناسب داری، مشخصات را ارسال کن.
@select_carr
@select_carrr

منبع محتوا: درخواست خرید واقعی سیستم
"""


def post_evergreen() -> str:
    return """🖼 بسته پست کاروسل آماده طراحی

هدف: آموزش + ذخیره‌سازی

تیتر کاور:
«قبل از خرید خودرو این ۵ مورد را چک کن»

اسلاید ۱:
قبل از خرید خودرو این ۵ مورد را چک کن

اسلاید ۲:
مدل، تیپ و سال دقیق
مقایسه اشتباه از همین‌جا شروع می‌شود.

اسلاید ۳:
کارکرد
دو خودرو با نام یکسان می‌توانند به‌خاطر کارکرد فاصله قیمت جدی داشته باشند.

اسلاید ۴:
بدنه و رنگ
هر اختلاف قیمت، تخفیف نیست.

اسلاید ۵:
تعداد نمونه مقایسه
یک آگهی به‌تنهایی بازار نیست.

اسلاید ۶:
قیمت معامله مهم‌تر از قیمت‌سازی آگهی است.

اسلاید آخر:
فرصت واقعی خرید را با داده پیدا کن، نه با حدس.

دعوت به اقدام:
«این پست را ذخیره کن و برای خریدار بعدی بفرست.»

کپشن:
خرید خوب از مقایسه درست شروع می‌شود.
@select_carr
@select_carrr
"""


def build_post(ctx: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
    family = choose_family("post", ctx, state)

    # Prefer a different family than today's reel when possible.
    reel_family = str(state.get("last_reel_family") or "")
    available = {
        "deal": bool(ctx.get("deal")),
        "buyer": bool(ctx.get("buyer")),
        "market": bool(ctx.get("market")),
        "cars": bool(ctx.get("cars")),
    }
    if family == reel_family:
        for candidate in ("market", "cars", "deal", "buyer"):
            if candidate != reel_family and available.get(candidate):
                family = candidate
                break

    if family == "deal":
        text = post_from_deal(ctx["deal"])
    elif family == "buyer":
        text = post_from_buyer(ctx["buyer"])
    elif family == "market":
        text = post_from_market(ctx["market"])
    elif family == "cars":
        text = post_from_cars(ctx["cars"])
    else:
        text = post_evergreen()

    return family, text


def build(kind: str) -> dict[str, Any]:
    if kind not in {"reel", "post"}:
        raise ValueError("kind must be reel or post")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    ctx = collect_context()

    if kind == "reel":
        family, text = build_reel(ctx, state)
    else:
        family, text = build_post(ctx, state)

    key = f"{today_key()}:{kind}"
    output_path = RUNTIME_DIR / f"{kind}.txt"
    output_path.write_text(text.strip() + "\n", encoding="utf-8")

    manifest = {
        "version": VERSION,
        "generated_at_tehran": now_tehran().isoformat(),
        "content_key": key,
        "kind": kind,
        "family": family,
        "output": str(output_path),
        "source_posts_scanned": ctx.get("source_posts_scanned", 0),
        "has_market": bool(ctx.get("market")),
        "has_cars": bool(ctx.get("cars")),
        "has_deal": bool(ctx.get("deal")),
        "has_buyer": bool(ctx.get("buyer")),
        "warnings": ctx.get("warnings", []),
    }

    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return manifest


def telegram_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Telegram bot token is missing")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        detail = body.strip() or str(exc)
        try:
            parsed = json.loads(body)
            detail = str(parsed.get("description") or parsed)
        except Exception:
            pass
        raise RuntimeError(
            f"Telegram {method} HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Telegram {method} network error: {exc.reason}"
        ) from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise RuntimeError(
            f"Telegram {method} returned invalid JSON: {raw[:500]}"
        ) from exc

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram {method} failed: "
            f"{data.get('description') or json.dumps(data, ensure_ascii=False)}"
        )

    return data


def check_telegram(send_test: bool = False) -> dict[str, Any]:
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "CONTENT_TELEGRAM_CHAT_ID is missing. "
            "Set it to the numeric channel id, for example -1001234567890."
        )

    if not re.fullmatch(r"-100\d{6,}", TELEGRAM_CHAT_ID):
        raise RuntimeError(
            "CONTENT_TELEGRAM_CHAT_ID must be the numeric channel id "
            "starting with -100, not @username."
        )

    me = telegram_api("getMe", {})
    username = clean((me.get("result") or {}).get("username"), 80).lstrip("@")

    if EXPECTED_BOT_USERNAME and username.casefold() != EXPECTED_BOT_USERNAME.casefold():
        raise RuntimeError(
            f"Telegram bot mismatch: got=@{username}, expected=@{EXPECTED_BOT_USERNAME}"
        )

    chat = telegram_api("getChat", {"chat_id": TELEGRAM_CHAT_ID})
    chat_result = chat.get("result") or {}

    result = {
        "status": "ok",
        "bot_username": f"@{username}",
        "chat_id": chat_result.get("id"),
        "chat_title": chat_result.get("title"),
        "chat_username": (
            f"@{chat_result.get('username')}"
            if chat_result.get("username")
            else None
        ),
    }

    if send_test:
        telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": (
                    "✅ تست موتور محتوای Select Carr\n"
                    "اتصال بات و کانال مدیریت درست است."
                ),
                "disable_web_page_preview": "true",
            },
        )
        result["test_message_sent"] = True

    return result


def parse_iso(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TEHRAN)
    return dt


def instagram_content_type(content_key: str) -> str:
    key = str(content_key or "")
    if key.startswith("opportunity:"):
        return "استوری فرصت خرید"
    if key.startswith("buy_request:"):
        return "استوری درخواست خرید"
    if key.startswith("market:"):
        return "پست طلا و ارز"
    if key.startswith("cars:"):
        return "پست قیمت خودرو"
    return "محتوای اینستاگرام"


def _stage4_rows() -> dict[str, dict[str, Any]]:
    if not STAGE4_STATE_PATH.is_file():
        return {}

    try:
        payload = json.loads(STAGE4_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Stage 4 publish state is unreadable: {exc}") from exc

    published = payload.get("published") if isinstance(payload, dict) else None
    if not isinstance(published, dict):
        return {}

    out: dict[str, dict[str, Any]] = {}
    for token, row in published.items():
        if not isinstance(row, dict):
            continue
        channel = clean(row.get("channel"), 80)
        content_key = clean(row.get("content_key"), 240)
        published_at = clean(row.get("published_at"), 80)
        if not channel or not content_key or not published_at:
            continue
        out[str(token)] = dict(row)

    return out


def _instagram_notification_text(
    content_key: str,
    all_rows: dict[str, dict[str, Any]],
) -> str:
    rows = [
        row
        for row in all_rows.values()
        if str(row.get("content_key") or "") == content_key
    ]

    rows.sort(key=lambda row: str(row.get("published_at") or ""))
    newest = rows[-1] if rows else {}
    published_dt = parse_iso(newest.get("published_at"))
    if published_dt is not None:
        time_text = published_dt.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")
    else:
        time_text = now_tehran().strftime("%Y-%m-%d %H:%M:%S")

    by_channel = {
        str(row.get("channel") or ""): row
        for row in rows
    }

    lines = [
        "✅ انتشار جدید در اینستاگرام",
        f"نوع: {instagram_content_type(content_key)}",
        f"زمان تهران: {time_text}",
        f"شناسه محتوا: {content_key}",
        "",
    ]

    for channel in ("select_carr", "select_carrr"):
        row = by_channel.get(channel)
        if row:
            post_id = clean(row.get("post_id"), 100)
            suffix = f" | Buffer ID: {post_id}" if post_id else ""
            lines.append(f"@{channel}: منتشر شد ✅{suffix}")
        else:
            lines.append(f"@{channel}: هنوز ثبت نشده")

    return "\n".join(lines)


def notify_instagram() -> dict[str, Any]:
    rows = _stage4_rows()
    state = load_state()
    notified = state.setdefault("instagram_notified_tokens", {})

    if not rows:
        return {
            "status": "no_stage4_publications",
            "stage4_state": str(STAGE4_STATE_PATH),
        }

    # First activation must not flood the management channel with old history.
    # Historical rows older than 20 minutes are silently marked as seen.
    initialized = bool(state.get("instagram_notification_initialized"))
    recent_cutoff = now_tehran() - timedelta(minutes=20)

    if not initialized:
        for token, row in rows.items():
            dt = parse_iso(row.get("published_at"))
            if dt is None or dt.astimezone(TEHRAN) < recent_cutoff:
                notified[token] = {
                    "status": "bootstrap_seen",
                    "published_at": row.get("published_at"),
                    "content_key": row.get("content_key"),
                }
        state["instagram_notification_initialized"] = True
        save_state(state)

    pending_tokens = [
        token
        for token, row in rows.items()
        if token not in notified
        and parse_iso(row.get("published_at")) is not None
    ]

    pending_tokens.sort(
        key=lambda token: str(rows[token].get("published_at") or "")
    )

    if not pending_tokens:
        return {
            "status": "no_new_instagram_publications",
            "published_records": len(rows),
        }

    # Verify bot + destination before marking any publication as notified.
    connection = check_telegram(send_test=False)

    grouped: dict[str, list[str]] = {}
    for token in pending_tokens:
        key = str(rows[token].get("content_key") or "")
        grouped.setdefault(key, []).append(token)

    sent_messages: list[dict[str, Any]] = []

    for content_key, tokens in grouped.items():
        response = telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": _instagram_notification_text(content_key, rows),
                "disable_web_page_preview": "true",
            },
        )
        message_id = int((response.get("result") or {}).get("message_id") or 0)

        for token in tokens:
            row = rows[token]
            notified[token] = {
                "status": "notified",
                "notified_at": now_tehran().isoformat(),
                "published_at": row.get("published_at"),
                "content_key": row.get("content_key"),
                "channel": row.get("channel"),
                "message_id": message_id,
            }

        sent_messages.append(
            {
                "content_key": content_key,
                "type": instagram_content_type(content_key),
                "records": len(tokens),
                "message_id": message_id,
            }
        )

        save_state(state)

    # Keep state bounded.
    if len(notified) > 1200:
        ordered = sorted(
            notified.items(),
            key=lambda item: str((item[1] or {}).get("published_at") or ""),
            reverse=True,
        )[:1200]
        state["instagram_notified_tokens"] = dict(ordered)
        save_state(state)

    return {
        "status": "instagram_notifications_sent",
        "telegram": connection,
        "messages": sent_messages,
        "new_records": len(pending_tokens),
    }


def split_message(text: str, limit: int = 3900) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        candidate = (current + "\n\n" + paragraph).strip() if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        while len(paragraph) > limit:
            cut = paragraph.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = paragraph.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()

        current = paragraph

    if current:
        chunks.append(current)

    return chunks


def send(kind: str) -> dict[str, Any]:
    state = load_state()
    key = f"{today_key()}:{kind}"

    if key in (state.get("sent") or {}):
        return {
            "status": "already_sent",
            "content_key": key,
            "sent_at": state["sent"][key].get("sent_at"),
        }

    manifest = build(kind)
    content = Path(manifest["output"]).read_text(encoding="utf-8").strip()

    connection = check_telegram(send_test=False)

    sent_message_ids: list[int] = []
    for part_number, chunk in enumerate(split_message(content), start=1):
        prefix = ""
        if part_number > 1:
            prefix = f"ادامه بسته {('ریلز' if kind == 'reel' else 'پست')} — بخش {fa_num(part_number)}\n\n"

        response = telegram_api(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": prefix + chunk,
                "disable_web_page_preview": "true",
            },
        )
        message_id = int((response.get("result") or {}).get("message_id") or 0)
        if message_id:
            sent_message_ids.append(message_id)

    sent = state.setdefault("sent", {})
    sent[key] = {
        "sent_at": now_tehran().isoformat(),
        "kind": kind,
        "family": manifest["family"],
        "message_ids": sent_message_ids,
    }
    state[f"last_{kind}_family"] = manifest["family"]
    save_state(state)

    return {
        "status": "sent",
        "content_key": key,
        "kind": kind,
        "family": manifest["family"],
        "message_ids": sent_message_ids,
        "telegram": connection,
        "manifest": manifest,
    }


def preview(kind: str) -> int:
    manifest = build(kind)
    text = Path(manifest["output"]).read_text(encoding="utf-8")
    print(text)
    print(
        json.dumps(
            {
                "status": "preview",
                "manifest": manifest,
            },
            ensure_ascii=False,
        )
    )
    return 0


def selftest() -> int:
    fake_deal = {
        "brand": "Toyota",
        "model": "Corolla",
        "model_year": 2026,
        "candidate_price": 4_500_000_000,
        "average_price": 4_700_000_000,
        "discount_percent": 4.3,
        "sample_count": 12,
    }
    fake_buyer = {
        "model": "تویوتا کرولا",
        "budget": 5_000_000_000,
        "urgency": "همین هفته",
    }
    fake_market = {
        "usd": {"display": "115,000", "unit": "تومان"},
        "gold18": {"display": "10,500", "unit": "هزار تومان"},
        "coin": {"display": "115,000", "unit": "هزار تومان"},
        "ounce": {"display": "4,000", "unit": "دلار"},
    }
    fake_cars = {
        "items": [
            {"name": "ساینا", "display_price": "700"},
            {"name": "کوییک", "display_price": "760"},
            {"name": "شاهین", "display_price": "1,250"},
            {"name": "تارا", "display_price": "1,450"},
            {"name": "دنا", "display_price": "1,600"},
        ]
    }

    ctx = {
        "deal": fake_deal,
        "buyer": fake_buyer,
        "market": fake_market,
        "cars": fake_cars,
    }
    state: dict[str, Any] = {}

    samples = [
        reel_from_deal(fake_deal),
        reel_from_buyer(fake_buyer),
        reel_from_market(fake_market),
        reel_from_cars(fake_cars),
        post_from_deal(fake_deal),
        post_from_buyer(fake_buyer),
        post_from_market(fake_market),
        post_from_cars(fake_cars),
    ]

    assert all(len(x.strip()) > 300 for x in samples)
    assert all("090" not in x for x in samples)
    assert choose_family("reel", ctx, state) in {"deal", "buyer", "market", "cars"}
    assert len(split_message("\n\n".join(samples), limit=1000)) > 1

    print(
        json.dumps(
            {
                "status": "ok",
                "version": VERSION,
                "samples_tested": len(samples),
                "telegram_split_test": "ok",
                "privacy_test": "ok",
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "check_telegram",
            "test_telegram",
            "preview",
            "send",
            "notify_instagram",
            "selftest",
        ],
    )

    parser.add_argument(
        "--type",
        dest="kind",
        choices=["reel", "post"],
        default="reel",
    )

    args = parser.parse_args()

    if args.action == "check_telegram":
        print(json.dumps(check_telegram(False), ensure_ascii=False))
        return 0

    if args.action == "test_telegram":
        print(json.dumps(check_telegram(True), ensure_ascii=False))
        return 0

    if args.action == "preview":
        return preview(args.kind)

    if args.action == "send":
        result = send(args.kind)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.action == "notify_instagram":
        result = notify_instagram()
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.action == "selftest":
        return selftest()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
