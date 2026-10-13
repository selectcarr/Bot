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


VERSION = "1.2.0"
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
    """Collect only non-personal market context for educational content.

    Individual deals and buyer requests are intentionally excluded from the
    content engine. They remain available to Stage 4 for stories, but are not
    used as Reel/Post subjects here.
    """
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


def _safe_float(value: object) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except Exception:
        return 0.0


def _stable_variant(label: str, count: int) -> int:
    if count <= 1:
        return 0
    seed = sum(ord(ch) for ch in f"{today_key()}:{label}")
    return seed % count


def _deal_numbers(deal: dict[str, Any]) -> tuple[int, int, int, float, int]:
    candidate = int(
        _safe_float(
            deal.get("candidate_price")
            or deal.get("asking_price")
            or deal.get("price")
        )
    )
    average = int(
        _safe_float(
            deal.get("average_price")
            or deal.get("market_average")
            or deal.get("median_price")
        )
    )
    gap = max(0, average - candidate) if average and candidate else 0
    discount = _safe_float(deal.get("discount_percent"))
    if discount <= 0 and average > 0 and candidate > 0 and candidate < average:
        discount = ((average - candidate) / average) * 100.0
    samples = int(_safe_float(deal.get("sample_count")))
    return candidate, average, gap, discount, samples


def _content_strength(
    family: str,
    kind: str,
    ctx: dict[str, Any],
    state: dict[str, Any],
) -> float:
    score = 5.5

    if family == "deal" and ctx.get("deal"):
        _, _, _, discount, samples = _deal_numbers(ctx["deal"])
        score = 7.6 + min(discount, 12.0) * 0.11 + min(samples, 20) * 0.045
        if kind == "reel":
            score += 0.45

    elif family == "buyer" and ctx.get("buyer"):
        buyer = ctx["buyer"]
        urgency = clean(buyer.get("urgency"), 40).casefold()
        budget = _safe_float(buyer.get("budget"))
        score = 7.6
        if any(word in urgency for word in ("فوری", "همین هفته", "امروز", "urgent")):
            score += 0.65
        if budget > 0:
            score += 0.25
        if kind == "reel":
            score += 0.45

    elif family == "market" and ctx.get("market"):
        score = 7.15
        if kind == "post":
            score += 0.55

    elif family == "cars" and ctx.get("cars"):
        count = len(list((ctx["cars"] or {}).get("items") or []))
        score = 7.1 + min(count, 80) * 0.012
        if kind == "post":
            score += 0.55

    previous = str(
        state.get("last_reel_family" if kind == "reel" else "last_post_family")
        or ""
    )
    if previous == family:
        score -= 1.8

    return round(score, 2)


EDUCATIONAL_REEL_THEMES = (
    "cheap_trap",
    "price_vs_value",
    "inspection_mistakes",
    "seller_questions",
    "negotiation",
    "mileage_myth",
    "paint_body",
    "market_psychology",
    "hidden_costs",
    "resale_value",
    "scam_red_flags",
    "buying_timing",
)

EDUCATIONAL_POST_THEMES = (
    "buy_checklist",
    "ad_red_flags",
    "seller_questions",
    "price_checklist",
    "inspection_checklist",
    "negotiation_checklist",
    "documents",
    "hidden_costs",
    "resale_value",
    "market_myths",
    "mileage_myth",
    "body_paint",
)


def choose_family(
    kind: str,
    ctx: dict[str, Any],
    state: dict[str, Any],
) -> str:
    """Choose an educational/viral topic; never an individual deal or buyer."""
    themes = EDUCATIONAL_REEL_THEMES if kind == "reel" else EDUCATIONAL_POST_THEMES
    previous = str(state.get("last_reel_family" if kind == "reel" else "last_post_family") or "")
    index = _stable_variant(f"education:{kind}", len(themes))
    family = themes[index]
    if family == previous and len(themes) > 1:
        family = themes[(index + 1) % len(themes)]
    return family


def _viral_score_text(
    family: str,
    kind: str,
    ctx: dict[str, Any],
    state: dict[str, Any],
) -> str:
    score = min(9.6, max(5.5, _content_strength(family, kind, ctx, state)))
    return f"{fa_num(f'{score:.1f}')} از ۱۰"


def _reel_header(
    family: str,
    ctx: dict[str, Any],
    state: dict[str, Any],
    why: str,
    hooks: list[str],
) -> str:
    chosen = hooks[_stable_variant(f"reel:{family}", len(hooks))]
    alternatives = [hook for hook in hooks if hook != chosen]
    alt_lines = "\n".join(
        f"• {hook}" for hook in alternatives[:3]
    )
    return f"""🎬 پیشنهاد ریلز منتخب امروز

پتانسیل پیشنهادی جذب و اشتراک‌گذاری: {_viral_score_text(family, 'reel', ctx, state)}
دلیل انتخاب سوژه: {why}

قلاب اصلی پیشنهادی:
«{chosen}»

قلاب‌های جایگزین برای تست:
{alt_lines}

قاعده اجرا:
• ۲ ثانیه اول بدون سلام و مقدمه؛ مستقیم با عدد، سؤال یا تضاد شروع کن.
• هر ۲ تا ۴ ثانیه قاب یا نوشته عوض شود.
• اطلاعات کلیدی را یک‌باره لو نده؛ نتیجه اصلی را بعد از ایجاد سؤال نشان بده.
• ویدئو بین ۲۴ تا ۳۲ ثانیه بماند.
• متن روی تصویر کوتاه و درشت باشد؛ هر قاب حداکثر یک پیام.
"""


def _post_header(
    family: str,
    ctx: dict[str, Any],
    state: dict[str, Any],
    why: str,
    covers: list[str],
) -> str:
    chosen = covers[_stable_variant(f"post:{family}", len(covers))]
    alternatives = [cover for cover in covers if cover != chosen]
    alt_lines = "\n".join(
        f"• {cover}" for cover in alternatives[:3]
    )
    return f"""🖼 پیشنهاد پست کاروسل منتخب امروز

پتانسیل پیشنهادی ذخیره و ارسال: {_viral_score_text(family, 'post', ctx, state)}
دلیل انتخاب سوژه: {why}

کاور اصلی پیشنهادی:
«{chosen}»

کاورهای جایگزین برای تست:
{alt_lines}

قاعده طراحی:
• کاور باید یک سؤال یا تضاد روشن بسازد؛ نه توضیح طولانی.
• هر اسلاید فقط یک نکته اصلی داشته باشد.
• اسلاید دوم باید کنجکاوی کاور را بیشتر کند، نه اینکه فوراً همه پاسخ را بدهد.
• نتیجه یا عدد اصلی در میانه کاروسل آشکار شود.
• اسلاید آخر فقط یک دعوت به اقدام روشن داشته باشد.
"""


def reel_from_deal(deal: dict[str, Any]) -> str:
    title = deal_title(deal)
    candidate, average, gap, discount, samples = _deal_numbers(deal)
    price = money_toman(candidate)
    average_text = money_toman(average)
    gap_text = money_toman(gap) if gap > 0 else "اختلاف قابل توجه"
    discount_text_value = (
        f"{fa_num(f'{discount:.1f}'.rstrip('0').rstrip('.'))}٪"
        if discount > 0
        else "زیر میانگین بازار"
    )
    proof = (
        f"با {fa_num(samples)} نمونه مشابه مقایسه شده"
        if samples > 0
        else "با نمونه‌های مشابه بازار مقایسه شده"
    )

    ctx = {"deal": deal}
    state = load_state()
    header = _reel_header(
        "deal",
        ctx,
        state,
        "عدد واقعی + تضاد «فرصت یا تله» + امکان ایجاد تعلیق تا نمایش میانگین بازار",
        [
            f"این {title} حدود {gap_text} زیر میانگین بازاره؛ فرصت یا تله؟",
            f"اگه {price} داشتی، این {title} رو می‌خریدی؟ صبر کن عدد دوم رو ببینی.",
            f"این آگهی {discount_text_value} زیر میانگین دیده شده؛ اما ارزون بودن هنوز دلیل خرید نیست.",
            f"ما این {title} رو با {fa_num(samples) if samples else 'چند'} نمونه مقایسه کردیم؛ نتیجه این شد.",
        ],
    )

    return header + f"""

سناریوی پیشنهادی:

۰ تا ۳ ثانیه — توقف اسکرول
تصویر: نام خودرو + قیمت آگهی، بدون توضیح اضافه
متن روی تصویر:
{title}
{price}
گویندگی:
«این قیمت خوب به نظر می‌رسه؛ ولی فقط یک عدد کم‌تر، فرصت خرید نمی‌سازه.»

۳ تا ۹ ثانیه — ایجاد سؤال
متن روی تصویر:
«بازار واقعاً چند؟»
گویندگی:
«سیستم Select Carr این آگهی رو با نمونه‌های هم‌گروه مقایسه کرد.»

۹ تا ۱۵ ثانیه — آشکارسازی عدد اصلی
متن روی تصویر:
میانگین مقایسه: {average_text}
اختلاف: {discount_text_value}
گویندگی:
«میانگین مقایسه حدود {average_text} بود؛ یعنی اختلاف این آگهی حدود {gap_text} است.»

۱۵ تا ۲۲ ثانیه — شکستن تصور ساده
متن روی تصویر:
«ارزون = خوب؟ نه همیشه»
گویندگی:
«حالا بخش مهم: رنگ، بدنه، کارکرد و وضعیت واقعی خودرو باید این اختلاف رو توجیه نکنه؛ وگرنه تخفیف ظاهریه.»

۲۲ تا ۲۹ ثانیه — اثبات روش
متن روی تصویر:
{proof}
گویندگی:
«برای همین ما اول داده رو مقایسه می‌کنیم و بعد فرصت رو جدا می‌کنیم؛ نه برعکس.»

۲۹ تا ۳۲ ثانیه — دعوت به اقدام
متن روی تصویر:
«خریدار یا فروشنده‌ای؟»
گویندگی:
«اگر خریدار هستی «خرید» و اگر فروشنده‌ای «فروش» رو بفرست.»

پیشنهاد تدوین:
• قیمت آگهی با ضربه تصویری در ثانیه اول ظاهر شود.
• میانگین بازار تا ثانیه ۹ کامل نشان داده نشود.
• اختلاف قیمت با شمارنده یا حرکت عددی نمایش داده شود.
• هیچ ادعای «حتماً بخر» یا تضمین سود نداشته باش.

کپشن پیشنهادی:
این آگهی فقط چون ارزان‌تر است، خودکار «فرصت خرید» نمی‌شود.

{title}
قیمت آگهی: {price}
میانگین مقایسه: {average_text}
اختلاف ثبت‌شده: {discount_text_value}
{proof}

بعد از عدد، وضعیت واقعی خودرو تعیین‌کننده است.
اگر دنبال خرید یا فروش هستی، پیام بده.
@select_carr
@select_carrr

منبع محتوا: Deal واقعی سیستم Select Carr
"""


def reel_from_buyer(buyer: dict[str, Any]) -> str:
    model = clean(buyer.get("model"), 70) or "خودروی مشخص"
    budget = money_toman(buyer.get("budget"))
    urgency = clean(buyer.get("urgency"), 40) or "فعال"
    ctx = {"buyer": buyer}
    state = load_state()
    header = _reel_header(
        "buyer",
        ctx,
        state,
        "تقاضای واقعی + عدد بودجه + دعوت مستقیم فروشنده؛ محتوایی که هم تعامل می‌گیرد هم سرنخ فروش می‌سازد",
        [
            f"{model} داری؟ یک خریدار با بودجه تا {budget} داخل سیستم فعاله.",
            f"این بار دنبال فروشنده‌ایم، نه خریدار؛ {model} داری؟",
            f"برای {model} خریدار ثبت‌شده داریم؛ شاید ماشین تو دقیقاً همونیه که می‌خواد.",
            f"اگر {model} برای فروش داری، قبل از آگهی‌کردن این عدد رو ببین: {budget}.",
        ],
    )

    return header + f"""

سناریوی پیشنهادی:

۰ تا ۳ ثانیه — توقف اسکرول
متن روی تصویر:
«{model} داری؟»
گویندگی:
«اگر {model} داری، این ویدئو احتمالاً برای توئه.»

۳ تا ۹ ثانیه — اثبات تقاضا
متن روی تصویر:
«درخواست خرید فعال»
گویندگی:
«یک درخواست خرید واقعی برای {model} داخل سیستم ثبت شده.»

۹ تا ۱۵ ثانیه — عددی که مخاطب را نگه می‌دارد
متن روی تصویر:
بودجه تا {budget}
گویندگی:
«قدرت خرید ثبت‌شده تا {budget} است و اولویت خرید: {urgency}.»

۱۵ تا ۲۲ ثانیه — تفاوت برند
متن روی تصویر:
«آگهی بیشتر نه؛ تطبیق بهتر»
گویندگی:
«هدف ما فقط زیاد کردن آگهی نیست؛ فروشنده مناسب باید به خریدار متناسب وصل بشه.»

۲۲ تا ۲۹ ثانیه — اقدام واضح
متن روی تصویر:
«فروشنده {model} هستی؟»
گویندگی:
«اگر خودروی متناسب داری، مشخصاتش رو بفرست تا قیمت و تطبیق بررسی بشه.»

۲۹ تا ۳۲ ثانیه — پایان
متن روی تصویر:
«کلمه «فروش» را بفرست»

پیشنهاد تدوین:
• مدل خودرو از همان فریم اول درشت باشد.
• بودجه را قبل از ثانیه ۹ کامل نشان نده تا کنجکاوی حفظ شود.
• هیچ شماره تماس یا اطلاعات خصوصی خریدار نمایش داده نشود.

کپشن پیشنهادی:
برای {model} یک درخواست خرید فعال داخل سیستم ثبت شده.
بودجه ثبت‌شده: {budget}
اولویت: {urgency}

اگر خودروی متناسب برای فروش داری، مشخصات را ارسال کن.
@select_carr
@select_carrr

منبع محتوا: درخواست خرید واقعی ثبت‌شده در سیستم
"""


def reel_from_market(market: dict[str, Any]) -> str:
    usd = display_rate(market.get("usd"))
    gold = display_rate(market.get("gold18"))
    coin = display_rate(market.get("coin"))
    ctx = {"market": market}
    state = load_state()
    header = _reel_header(
        "market",
        ctx,
        state,
        "موضوع روز + عدد قابل ذخیره + شکستن باور ساده «دلار بالا رفت پس خودرو فوراً همان‌قدر گران می‌شود»",
        [
            f"دلار {usd}؛ یعنی ماشین هم باید همین الان گرون‌تر بشه؟ نه لزوماً.",
            "بزرگ‌ترین اشتباه امروز بازار خودرو: قیمت دلار رو مستقیم روی ماشین ضرب کنی.",
            "قبل از اینکه با دیدن دلار تصمیم خرید ماشین بگیری، این ۳۰ ثانیه رو ببین.",
            "قیمت آگهی با قیمت معامله یکی نیست؛ مخصوصاً روزهایی که دلار تکان می‌خورد.",
        ],
    )

    return header + f"""

سناریوی پیشنهادی:

۰ تا ۳ ثانیه — سؤال متضاد
متن روی تصویر:
دلار: {usd}
«ماشین هم فوراً گرون می‌شه؟»
گویندگی:
«نه؛ این رابطه اون‌قدر مستقیم که خیلی‌ها فکر می‌کنن نیست.»

۳ تا ۹ ثانیه — زمینه روز
متن روی تصویر:
طلای ۱۸ عیار: {gold}
گویندگی:
«امروز این اعداد بازارند؛ مهم‌اند، ولی برای قیمت خودرو فقط یک ورودی محسوب می‌شن.»

۹ تا ۱۶ ثانیه — شکستن باور
متن روی تصویر:
«قیمت آگهی ≠ قیمت معامله»
گویندگی:
«فروشنده می‌تونه عدد آگهی رو در یک دقیقه عوض کنه؛ اما خریدار واقعی و قیمت معامله با تأخیر واکنش نشون می‌ده.»

۱۶ تا ۲۳ ثانیه — عدد سوم
متن روی تصویر:
سکه: {coin}
گویندگی:
«برای تصمیم خرید، باید کنار ارز و طلا، آگهی‌های مشابه و رفتار واقعی بازار خودرو هم دیده بشه.»

۲۳ تا ۳۰ ثانیه — جمع‌بندی برند
متن روی تصویر:
«عدد روز + مقایسه واقعی»
گویندگی:
«Select Carr دقیقاً همین دو لایه رو کنار هم می‌ذاره: قیمت روز و مقایسه نمونه‌های واقعی.»

دعوت به اقدام:
«ذخیره‌اش کن؛ اگر بازار تکان خورد، فردا این عددها را با امروز مقایسه کن.»

کپشن پیشنهادی:
حرکت دلار مهم است، اما تنها متغیر بازار خودرو نیست.

دلار: {usd}
طلای ۱۸ عیار: {gold}
سکه: {coin}

قیمت آگهی، قیمت معامله و تعداد خریدار واقعی باید کنار هم دیده شوند.
@select_carr
@select_carrr

منبع محتوا: آخرین نرخ معتبر منبع بازار
"""


def _car_rows(cars: dict[str, Any], limit: int) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in list(cars.get("items") or [])[:limit]:
        name = clean(item.get("name"), 55) or "خودرو"
        price = clean(item.get("display_price"), 30)
        if price:
            price = f"{fa_num(price)} میلیون تومان"
        else:
            price = "قیمت ثبت‌شده بازار"
        rows.append((name, price))
    return rows


def reel_from_cars(cars: dict[str, Any]) -> str:
    rows = _car_rows(cars, 3)
    prices = "\n".join(f"{name} — {price}" for name, price in rows) or "آخرین لیست معتبر قیمت خودرو"
    names = "، ".join(name for name, _ in rows) or "چند خودروی پرفروش"
    ctx = {"cars": cars}
    state = load_state()
    header = _reel_header(
        "cars",
        ctx,
        state,
        "عددهای کاربردی + قابلیت ذخیره + مخاطب با نیت خرید بالا",
        [
            f"قیمت {names} رو قبل از خرید امروز ببین.",
            "۳ عددی که اگر امروز دنبال ماشین باشی باید اسکرین‌شات بگیری.",
            "قبل از تماس با فروشنده، این ۳ قیمت رو توی ذهنت داشته باش.",
            "قیمت روز رو ببین؛ بعد تصمیم بگیر کدوم آگهی واقعاً ارزونه.",
        ],
    )

    return header + f"""

سناریوی پیشنهادی:

۰ تا ۳ ثانیه — توقف اسکرول
متن روی تصویر:
«قبل از خرید امروز، این ۳ عدد رو ببین»

۳ تا ۱۵ ثانیه — نمایش سریع سه قیمت
هر خودرو ۳ تا ۴ ثانیه:
{prices}

گویندگی:
«این‌ها از آخرین لیست معتبر قیمت خودرو استخراج شده‌اند. اسکرین‌شات بگیر چون مرحله بعد مهم‌تره.»

۱۵ تا ۲۲ ثانیه — تغییر زاویه
متن روی تصویر:
«قیمت روز ≠ فرصت خرید»
گویندگی:
«عدد روز فقط نقطه شروعه. برای اینکه بفهمی یک آگهی واقعاً ارزونه، مدل، سال و وضعیتش باید با نمونه‌های مشابه مقایسه بشه.»

۲۲ تا ۳۰ ثانیه — جمع‌بندی
متن روی تصویر:
«قیمت روز + Deal واقعی»
گویندگی:
«در Select Carr هم عدد روز رو می‌بینی، هم فرصت‌هایی که نسبت به بازار اختلاف قابل دفاع دارن.»

دعوت به اقدام:
«اسم مدلی که می‌خوای قیمتش رو دنبال کنیم کامنت کن.»

پیشنهاد تدوین:
• هر قیمت با کات سریع و صدای کوتاه تغییر کند.
• متن‌ها بزرگ و قابل اسکرین‌شات باشند.
• اسلاید قیمت را شلوغ نکن؛ یک خودرو در هر قاب.

کپشن پیشنهادی:
۳ عدد از آخرین لیست معتبر قیمت خودرو:
{prices}

قیمت روز مهم است؛ اما فرصت واقعی خرید بعد از مقایسه با نمونه‌های مشابه مشخص می‌شود.
مدل موردنظرت را بنویس.
@select_carr
@select_carrr
"""


def reel_evergreen() -> str:
    ctx: dict[str, Any] = {}
    state = load_state()
    header = _reel_header(
        "evergreen",
        ctx,
        state,
        "آموزش کاربردی + ترس از تصمیم اشتباه + قابلیت ذخیره بالا",
        [
            "قیمت پایین دیدی؟ قبل از تماس، این ۳ چیز رو چک کن.",
            "ارزون‌ترین آگهی بازار می‌تونه بدترین خریدت باشه؛ چرا؟",
            "۳ اشتباه که باعث می‌شه یک آگهی معمولی رو Deal ببینی.",
            "قبل از اینکه بگی «خیلی ارزونه»، این سه سؤال رو جواب بده.",
        ],
    )
    return header + """

سناریوی پیشنهادی:

۰ تا ۳ ثانیه:
«ارزون‌ترین آگهی همیشه بهترین خرید نیست.»

۳ تا ۱۰ ثانیه:
«اول مدل، تیپ و سال دقیق رو با هم‌گروه خودش مقایسه کن.»

۱۰ تا ۱۸ ثانیه:
«دوم، رنگ، بدنه و کارکرد می‌تونه تمام اختلاف قیمت رو توضیح بده.»

۱۸ تا ۲۵ ثانیه:
«سوم، یک آگهی به‌تنهایی بازار نیست؛ چند نمونه معتبر لازم داری.»

۲۵ تا ۳۰ ثانیه:
«فرصت واقعی یعنی قیمت مناسب + داده کافی + وضعیت قابل قبول.»

دعوت به اقدام:
«ذخیره کن؛ دفعه بعد که آگهی ارزون دیدی این سه مورد رو چک کن.»

کپشن پیشنهادی:
ارزان بودن با فرصت خرید بودن فرق دارد.
قبل از تصمیم، نمونه‌های هم‌گروه و وضعیت واقعی خودرو را کنار هم ببین.
@select_carr
@select_carrr
"""


def _fresh_market_angle(ctx: dict[str, Any]) -> str:
    market = ctx.get("market") or {}
    if not market:
        return ""
    usd = display_rate(market.get("usd"))
    gold = display_rate(market.get("gold18"))
    return (
        f"\nداده روز برای مثال زنده، بدون تبدیل شدن به گزارش قیمت:\n"
        f"دلار: {usd} | طلای ۱۸ عیار: {gold}\n"
    )


def _reel_educational(theme: str, ctx: dict[str, Any]) -> str:
    themes: dict[str, dict[str, Any]] = {
        "cheap_trap": {
            "cover": "ارزون دیدی؟ هنوز خوشحال نشو",
            "hooks": [
                "ارزون‌ترین آگهی بازار ممکنه گرون‌ترین اشتباهت باشه.",
                "این ۳ ثانیه می‌تونه جلوی یک خرید اشتباه رو بگیره.",
                "قیمت پایین دیدی؟ قبل از تماس این سه چیز رو چک کن.",
            ],
            "beats": [
                "اول: ببین واقعاً با همان تیپ و سال مقایسه می‌کنی یا نه.",
                "دوم: رنگ، بدنه، کارکرد و سابقه تعمیر می‌تواند تمام اختلاف قیمت را توضیح بدهد.",
                "سوم: یک آگهی بازار نیست؛ چند نمونه هم‌گروه لازم داری.",
                "نتیجه: فرصت واقعی یعنی قیمت مناسب + مقایسه درست + وضعیت قابل قبول.",
            ],
            "comment": "آخرین باری که یک آگهی خیلی ارزان دیدی، اولین شکت چی بود؟",
        },
        "price_vs_value": {
            "cover": "قیمت آگهی با قیمت واقعی فرق دارد",
            "hooks": [
                "عددی که توی آگهی می‌بینی، لزوماً قیمت بازار نیست.",
                "چرا دو ماشین ظاهراً مشابه، صدها میلیون اختلاف دارند؟",
                "قبل از اینکه بگی گرونه یا ارزونه، این اشتباه رو نکن.",
            ],
            "beats": [
                "قیمت آگهی فقط پیشنهاد فروشنده است.",
                "قیمت قابل اتکا وقتی شکل می‌گیرد که چند نمونه مشابه را کنار هم ببینی.",
                "سال، تیپ، کارکرد و وضعیت بدنه باید هم‌زمان مقایسه شوند.",
                "پس سؤال درست این نیست که «قیمتش چنده؟»؛ سؤال درست اینه «نسبت به هم‌گروه خودش کجاست؟»",
            ],
            "comment": "تو برای قیمت‌گیری اول کجا رو نگاه می‌کنی؟",
        },
        "inspection_mistakes": {
            "cover": "۳ اشتباه قبل از کارشناسی",
            "hooks": [
                "قبل از کارشناسی، این سه کار می‌تونه معامله رو خراب کنه.",
                "فقط به ظاهر تمیز ماشین اعتماد نکن.",
                "ماشین برق می‌زنه؛ ولی این یعنی سالمه؟ نه لزوماً.",
            ],
            "beats": [
                "اول: قبل از کارشناسی درباره سلامت قطعی نتیجه نگیر.",
                "دوم: فقط به رنگ بدنه نگاه نکن؛ شاسی و سابقه ضربه مهم‌تر می‌شود.",
                "سوم: حرف فروشنده را با گزارش کارشناسی تطبیق بده، نه با حس خودت.",
                "قانون ساده: قبل از پول، مدرک؛ قبل از هیجان، بررسی.",
            ],
            "comment": "به نظرت مهم‌ترین بخش کارشناسی چیه؟",
        },
        "seller_questions": {
            "cover": "قبل از بازدید این ۵ سؤال را بپرس",
            "hooks": [
                "قبل از اینکه راه بیفتی برای بازدید، این پنج سؤال رو تلفنی بپرس.",
                "با پنج سؤال، نصف بازدیدهای بی‌فایده حذف می‌شن.",
                "این سؤال‌ها رو نپرسی، ممکنه یک ساعت راه بری برای هیچی.",
            ],
            "beats": [
                "یک: مالک خود شمایید؟",
                "دو: رنگ یا تعویض قطعه داشته؟",
                "سه: کارکرد واقعی و قابل استناده؟",
                "چهار: آخرین سرویس مهم چی بوده؟ پنج: دلیل فروش چیه؟",
            ],
            "comment": "چه سؤال دیگری قبل از بازدید می‌پرسی؟",
        },
        "negotiation": {
            "cover": "این‌طوری تخفیف بگیر، نه با چونه‌زنی",
            "hooks": [
                "برای تخفیف گرفتن، جمله «آخرش چند؟» ضعیف‌ترین شروعه.",
                "تخفیف واقعی با دلیل میاد، نه با التماس.",
                "اگر می‌خوای مذاکره کنی، اول این سه عدد رو داشته باش.",
            ],
            "beats": [
                "قیمت چند نمونه مشابه را آماده کن.",
                "هزینه ایرادهای واقعی خودرو را جدا حساب کن.",
                "پیشنهادت را با عدد و دلیل بگو، نه با جمله کلی.",
                "اگر فاصله منطقی نیست، بهترین مذاکره گاهی ترک معامله است.",
            ],
            "comment": "تو معمولاً چند درصد زیر قیمت پیشنهادی شروع می‌کنی؟",
        },
        "mileage_myth": {
            "cover": "کارکرد پایین همیشه بهتر نیست",
            "hooks": [
                "کارکرد پایین دیدی؟ هنوز امتیاز کامل نده.",
                "یک ماشین کم‌کار می‌تونه انتخاب بدتری از یک ماشین پرکار باشه.",
                "فقط کیلومتر رو نبین؛ داستان پشت کیلومتر مهم‌تره.",
            ],
            "beats": [
                "کارکرد باید با سن خودرو و سابقه سرویس منطقی باشد.",
                "خواب طولانی خودرو هم می‌تواند هزینه بسازد.",
                "کیفیت نگهداری از عدد کیلومتر مهم‌تر می‌شود.",
                "پس کم‌کار بودن یک نشانه است، نه حکم نهایی.",
            ],
            "comment": "کم‌کارِ بدون سابقه سرویس یا پرکارِ مرتب؟ کدوم رو انتخاب می‌کنی؟",
        },
        "paint_body": {
            "cover": "رنگ‌شدگی همیشه یعنی نخریم؟",
            "hooks": [
                "هر ماشین رنگ‌دار، ماشین بدی نیست؛ ولی هر رنگی هم بی‌اهمیت نیست.",
                "یک تکه رنگ با آسیب شاسی یکی نیست.",
                "اینجا خیلی‌ها موقع خرید اشتباه قضاوت می‌کنن.",
            ],
            "beats": [
                "اول فرق ترمیم ظاهری با ضربه سازه‌ای را بفهم.",
                "محل آسیب مهم است؛ همه قطعات ارزش و ریسک یکسان ندارند.",
                "افت قیمت باید متناسب با نوع آسیب سنجیده شود.",
                "تصمیم درست یعنی کارشناسی + قیمت‌گذاری متناسب، نه ترس از یک کلمه.",
            ],
            "comment": "تو با یک لکه رنگ از خرید منصرف می‌شی؟",
        },
        "market_psychology": {
            "cover": "بازار گرون شد؛ الان بخریم؟",
            "hooks": [
                "وقتی بازار می‌ره بالا، بدترین تصمیم می‌تونه تصمیم عجولانه باشه.",
                "ترس از جا موندن، یکی از گرون‌ترین اشتباه‌های خرید ماشینه.",
                "قیمت‌ها تکون خورد؛ ولی این یعنی هر ماشینی ارزش خرید داره؟",
            ],
            "beats": [
                "حرکت بازار با ارزش هر خودرو یکی نیست.",
                "در بازار هیجانی، فاصله قیمت‌های پیشنهادی بیشتر می‌شود.",
                "به‌جای دنبال کردن شایعه، هم‌گروه همان خودرو را مقایسه کن.",
                "تصمیم خوب باید با داده امروز گرفته شود، نه ترس امروز.",
            ],
            "comment": "تو بازار صعودی صبر می‌کنی یا سریع می‌خری؟",
        },
        "hidden_costs": {
            "cover": "قیمت خرید، کل هزینه ماشین نیست",
            "hooks": [
                "ممکنه ماشین رو ارزون بخری و ماه اول چند ده میلیون خرج کنی.",
                "قبل از خرید فقط پول معامله رو حساب نکن.",
                "این هزینه‌ها بعد از خرید غافلگیرت می‌کنن.",
            ],
            "beats": [
                "سرویس اولیه، لاستیک، باتری و مصرفی‌ها را جدا ببین.",
                "هزینه انتقال، بیمه و ایرادهای عقب‌افتاده را فراموش نکن.",
                "ماشین ارزان با هزینه پنهان بالا ممکن است اصلاً ارزان نباشد.",
                "بودجه خرید باید یک حاشیه امن هم داشته باشد.",
            ],
            "comment": "بزرگ‌ترین خرجی که بعد از خرید ماشین کردی چی بود؟",
        },
        "resale_value": {
            "cover": "فقط خرید مهم نیست؛ فروش بعدی هم مهمه",
            "hooks": [
                "ماشینی که امروز دوست داری، فردا راحت فروخته می‌شه؟",
                "قبل از خرید یک سؤال بپرس: اگر مجبور شدم بفروشم چی؟",
                "قیمت خوب خریدی؛ ولی نقدشوندگی رو حساب کردی؟",
            ],
            "beats": [
                "تقاضای بازار، فراوانی قطعه و شناخت عمومی روی فروش بعدی اثر می‌گذارند.",
                "رنگ خاص یا تیپ کم‌تقاضا ممکن است زمان فروش را طولانی کند.",
                "خودروی خوب فقط خرید خوب نیست؛ خروج خوب هم دارد.",
                "قبل از خرید، سناریوی فروش شش ماه بعد را هم تصور کن.",
            ],
            "comment": "برای تو لذت رانندگی مهم‌تره یا فروش راحت‌تر؟",
        },
        "scam_red_flags": {
            "cover": "۵ زنگ خطر در آگهی خودرو",
            "hooks": [
                "اگر دو تا از این نشونه‌ها رو دیدی، خیلی محتاط شو.",
                "آگهی جذابه؟ این پنج زنگ خطر رو قبل از تماس ببین.",
                "بعضی آگهی‌ها با قیمت خوب، فقط برای عجله دادن تو ساخته شدن.",
            ],
            "beats": [
                "قیمت غیرعادی پایین بدون توضیح منطقی.",
                "اصرار برای بیعانه قبل از بازدید.",
                "تناقض بین توضیحات، عکس‌ها و صحبت فروشنده.",
                "فشار برای تصمیم فوری. هر کدام به‌تنهایی حکم نیست؛ کنار هم مهم می‌شوند.",
            ],
            "comment": "کدوم زنگ خطر رو بیشتر توی آگهی‌ها دیدی؟",
        },
        "buying_timing": {
            "cover": "بهترین زمان خرید؟ این سؤال اشتباهه",
            "hooks": [
                "بهترین روز خرید ماشین وجود نداره؛ بهترین موقعیت خرید وجود داره.",
                "منتظر کف بازار نباش؛ ممکنه چیزی که لازم داری رو از دست بدی.",
                "زمان خرید رو با یک عدد تعیین نکن.",
            ],
            "beats": [
                "اول نیاز و بودجه خودت را مشخص کن.",
                "بعد قیمت نمونه‌های مشابه را بسنج.",
                "وقتی خودرو مناسب با قیمت قابل دفاع پیدا شد، تصمیم معنا پیدا می‌کند.",
                "شکار کف بازار سخت است؛ شکار معامله منطقی عملی‌تر است.",
            ],
            "comment": "تو برای خرید منتظر کاهش قیمت می‌مونی یا دنبال گزینه مناسب می‌گردی؟",
        },
    }

    item = themes[theme]
    hook_index = _stable_variant(f"hook:{theme}", len(item["hooks"]))
    hook = item["hooks"][hook_index]
    alternatives = [h for i, h in enumerate(item["hooks"]) if i != hook_index]
    alt_text = "\n".join(f"• {h}" for h in alternatives)
    beat_text = "\n".join(
        f"{fa_num(i)}. {text}"
        for i, text in enumerate(item["beats"], start=1)
    )

    fresh = _fresh_market_angle(ctx) if theme == "market_psychology" else ""

    return f"""🎬 پیشنهاد ریلز آموزشی و وایرال امروز

هدف اصلی: فالو + ذخیره + اشتراک‌گذاری
موضوع: {item['cover']}

قلاب اصلی ۰ تا ۲ ثانیه:
«{hook}»

قلاب‌های جایگزین برای تست:
{alt_text}

سناریوی ۲۵ تا ۳۲ ثانیه:
{beat_text}
{fresh}
ریتم اجرا:
• بدون سلام و معرفی شروع کن.
• هر ۲ تا ۳ ثانیه کات، زوم یا نوشته عوض شود.
• جمله اول باید سؤال، تضاد یا هشدار واقعی بسازد؛ نه وعده دروغ.
• نتیجه اصلی را در نیمه دوم بده تا مخاطب بماند.
• زیرنویس درشت و کوتاه؛ هر قاب یک پیام.

متن روی کاور:
«{item['cover']}»

سؤال کامنت‌گیر:
«{item['comment']}»

دعوت به فالو:
«اگر می‌خوای قبل از خرید و فروش ماشین، اشتباه‌های گرون رو بشناسی، فالو کن؛ هر روز یک نکته کاربردی بازار خودرو.»

کپشن پیشنهادی:
این محتوا برای تصمیم‌گیری بهتر در بازار خودرو ساخته شده، نه برای معرفی یک آگهی خاص.
اگر نکات کاربردی خرید، فروش، قیمت‌گذاری و کارشناسی خودرو به کارت میاد، صفحه رو دنبال کن.

@select_carr
@select_carrr
"""


def build_reel(ctx: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
    family = choose_family("reel", ctx, state)
    return family, _reel_educational(family, ctx)


def post_from_market(market: dict[str, Any]) -> str:
    usd = display_rate(market.get("usd"))
    gold = display_rate(market.get("gold18"))
    coin = display_rate(market.get("coin"))
    ounce = display_rate(market.get("ounce"))
    ctx = {"market": market}
    state = load_state()
    header = _post_header(
        "market",
        ctx,
        state,
        "اعداد روز + قابلیت ذخیره + نتیجه کاربردی برای مخاطب خودرو",
        [
            "۴ عدد امروز بازار؛ اما فقط یکی‌شون قیمت ماشین رو تعیین نمی‌کنه",
            "امروز بازار چه می‌گه؟ ۴ عدد + یک نتیجه برای خودرو",
            "قبل از تصمیم خرید خودرو، این ۴ عدد رو کنار هم ببین",
            "دلار، طلا، سکه؛ کدوم‌شون واقعاً روی تصمیم خودرو اثر می‌ذاره؟",
        ],
    )

    return header + f"""

ساختار کاروسل:

اسلاید ۱ — کاور
«امروز بازار چه می‌گه؟»
زیرتیتر کوچک:
۴ عدد + یک نتیجه برای بازار خودرو

اسلاید ۲ — دلار
{usd}
متن کوچک:
مهم است، اما به‌تنهایی قیمت معامله خودرو را تعیین نمی‌کند.

اسلاید ۳ — طلای ۱۸ عیار
{gold}

اسلاید ۴ — سکه
{coin}

اسلاید ۵ — اونس
{ounce}

اسلاید ۶ — نقطه چرخش
تیتر:
«اشتباه رایج کجاست؟»
متن:
قیمت آگهی می‌تواند سریع تغییر کند؛ قیمت معامله و تقاضای واقعی نه لزوماً.

اسلاید ۷ — نتیجه
تیتر:
«برای خودرو، فقط دلار رو نگاه نکن»
متن:
نرخ روز + نمونه‌های مشابه + تعداد خریدار واقعی را کنار هم ببین.

اسلاید آخر — دعوت به اقدام
«این پست رو ذخیره کن و فردا اعداد رو مقایسه کن.»

کپشن پیشنهادی:
اعداد امروز بازار:
دلار: {usd}
طلای ۱۸ عیار: {gold}
سکه: {coin}
اونس: {ounce}

برای تصمیم در بازار خودرو، نرخ ارز فقط یکی از ورودی‌هاست.
@select_carr
@select_carrr

منبع محتوا: آخرین نرخ معتبر منبع بازار
"""


def post_from_cars(cars: dict[str, Any]) -> str:
    rows = _car_rows(cars, 5)
    row_text = "\n".join(f"• {name}: {price}" for name, price in rows) or "• آخرین لیست معتبر قیمت خودرو"
    ctx = {"cars": cars}
    state = load_state()
    header = _post_header(
        "cars",
        ctx,
        state,
        "پست مرجع و قابل ذخیره؛ مخاطب می‌تواند قیمت‌ها را برای مقایسه بعدی نگه دارد",
        [
            "۵ قیمت خودرو که امروز باید اسکرین‌شات بگیری",
            "قبل از خرید، این ۵ قیمت رو ذخیره کن",
            "امروز این ۵ خودرو چندن؟ عددها رو نگه دار",
            "قیمت روز رو داشته باش؛ بعد برو سراغ Deal واقعی",
        ],
    )

    slide_lines = []
    for idx, (name, price) in enumerate(rows, start=2):
        slide_lines.append(
            f"اسلاید {fa_num(idx)}:\n{name}\n{price}\nمتن کوچک: «این عدد نقطه شروع مقایسه است، نه حکم خرید.»"
        )

    slides = "\n\n".join(slide_lines) or "اسلاید ۲:\nآخرین لیست معتبر قیمت خودرو"

    return header + f"""

ساختار کاروسل:

اسلاید ۱ — کاور
«۵ قیمت خودرو که امروز باید داشته باشی»
زیرتیتر:
قبل از تماس با فروشنده، ذخیره کن

{slides}

اسلاید بعدی — نکته‌ای که پست را از لیست قیمت ساده جدا می‌کند
تیتر:
«عدد روز، Deal نیست»
متن:
فرصت خرید وقتی مشخص می‌شود که همان مدل، سال و شرایط با نمونه‌های واقعی مقایسه شود.

اسلاید آخر — تعامل
«مدلی که می‌خوای فردا قیمتش رو دنبال کنیم کامنت کن.»

خلاصه قیمت‌ها برای کپشن:
{row_text}

کپشن پیشنهادی:
این ۵ عدد را برای مقایسه نگه دار.
قیمت روز جهت بازار را نشان می‌دهد؛ اما خرید خوب بعد از مقایسه نمونه‌های واقعی اتفاق می‌افتد.

مدل موردنظرت را بنویس.
@select_carr
@select_carrr

منبع محتوا: آخرین لیست معتبر قیمت خودرو
"""


def post_from_deal(deal: dict[str, Any]) -> str:
    title = deal_title(deal)
    candidate, average, gap, discount, samples = _deal_numbers(deal)
    price = money_toman(candidate)
    average_text = money_toman(average)
    gap_text = money_toman(gap) if gap > 0 else "اختلاف قابل توجه"
    discount_text_value = (
        f"{fa_num(f'{discount:.1f}'.rstrip('0').rstrip('.'))}٪"
        if discount > 0
        else "زیر میانگین بازار"
    )
    ctx = {"deal": deal}
    state = load_state()
    header = _post_header(
        "deal",
        ctx,
        state,
        "مطالعه موردی واقعی + تعلیق «فرصت یا تله» + آموزش روش تشخیص Deal",
        [
            f"{title}: فرصت خرید یا فقط یک آگهی ارزون؟",
            f"این خودرو حدود {gap_text} زیر میانگینه؛ چرا هنوز نمی‌گیم بخر؟",
            "پشت یک Deal واقعی چه عددهایی هست؟",
            f"ما این {title} رو چطور از بین آگهی‌ها جدا کردیم؟",
        ],
    )

    return header + f"""

ساختار کاروسل:

اسلاید ۱ — کاور
«فرصت خرید یا تله قیمت؟»
{title}

اسلاید ۲ — فقط یک عدد
قیمت آگهی:
{price}
متن کوچک:
«فعلاً تصمیم نگیر.»

اسلاید ۳ — ایجاد شک
تیتر:
«بازار واقعی چند؟»
متن:
برای مقایسه، نمونه‌های هم‌گروه بررسی شدند.

اسلاید ۴ — آشکارسازی
میانگین مقایسه:
{average_text}

اسلاید ۵ — اختلاف
{discount_text_value}
حدود {gap_text}

اسلاید ۶ — اعتبار داده
{fa_num(samples) if samples else 'چند'} نمونه مقایسه‌شده

اسلاید ۷ — چرا هنوز خرید قطعی نیست؟
بدنه، رنگ، کارکرد و وضعیت فنی باید بررسی شود تا معلوم شود اختلاف قیمت واقعی است یا جبران یک ایراد.

اسلاید آخر — نتیجه
«Deal واقعی = عدد مناسب + داده کافی + وضعیت قابل قبول»
دعوت به اقدام:
«اگر دنبال خودرو هستی، درخواست خریدت رو ثبت کن.»

کپشن پیشنهادی:
یک آگهی فقط با قیمت پایین تبدیل به فرصت خرید نمی‌شود.

{title}
قیمت آگهی: {price}
میانگین مقایسه: {average_text}
اختلاف: {discount_text_value}
نمونه مقایسه: {fa_num(samples) if samples else 'چند'}

بعد از داده، وضعیت واقعی خودرو تعیین‌کننده است.
@select_carr
@select_carrr

منبع محتوا: Deal واقعی سیستم Select Carr
"""


def post_from_buyer(buyer: dict[str, Any]) -> str:
    model = clean(buyer.get("model"), 70) or "خودروی مشخص"
    budget = money_toman(buyer.get("budget"))
    urgency = clean(buyer.get("urgency"), 40) or "فعال"
    ctx = {"buyer": buyer}
    state = load_state()
    header = _post_header(
        "buyer",
        ctx,
        state,
        "تقاضای واقعی و قابل تبدیل به فروشنده؛ محتوا فقط برای لایک نیست و می‌تواند سرنخ معامله بسازد",
        [
            f"{model} داری؟ خریدار واقعی با بودجه تا {budget} داریم",
            "این پست برای خریدارها نیست؛ برای یک فروشنده مشخصه",
            f"دنبال فروشنده {model} هستیم",
            f"یک تقاضای واقعی: {model} تا {budget}",
        ],
    )

    return header + f"""

ساختار کاروسل:

اسلاید ۱ — کاور
«{model} داری؟»
زیرتیتر:
یک درخواست خرید واقعی فعال است

اسلاید ۲ — تقاضا
خودروی موردنظر:
{model}

اسلاید ۳ — قدرت خرید
تا {budget}

اسلاید ۴ — فوریت
{urgency}

اسلاید ۵ — چرا این پست مهم است؟
«اینجا فقط آگهی جمع نمی‌کنیم؛ تقاضا و عرضه باید به هم بخورند.»

اسلاید ۶ — مسیر فروشنده
مشخصات خودرو
→ بررسی قیمت
→ تطبیق با خریدار
→ هماهنگی معامله

اسلاید آخر — اقدام
«اگر فروشنده این مدل هستی، کلمه «فروش» رو بفرست.»

کپشن پیشنهادی:
یک درخواست خرید واقعی برای {model} داخل سیستم فعال است.
بودجه ثبت‌شده: {budget}
اولویت: {urgency}

اگر خودروی متناسب داری، مشخصات را ارسال کن.
@select_carr
@select_carrr

منبع محتوا: درخواست خرید واقعی سیستم
"""


def post_evergreen() -> str:
    ctx: dict[str, Any] = {}
    state = load_state()
    header = _post_header(
        "evergreen",
        ctx,
        state,
        "ترس از اشتباه خرید + آموزش کوتاه + قابلیت ذخیره و ارسال برای دوست خریدار",
        [
            "قبل از خرید خودرو این ۵ اشتباه رو نکن",
            "ارزون دیدی؟ این ۵ مورد رو قبل از تماس چک کن",
            "۵ سؤال که قبل از هر خرید خودرو باید جواب بدی",
            "چطور یک آگهی معمولی رو اشتباهی Deal نبینیم؟",
        ],
    )
    return header + """

ساختار کاروسل:

اسلاید ۱:
«قبل از خرید خودرو این ۵ اشتباه رو نکن»

اسلاید ۲:
مقایسه مدل یا تیپ اشتباه
هم‌نام بودن دو خودرو یعنی هم‌قیمت بودنشان نیست.

اسلاید ۳:
نادیده گرفتن کارکرد
کارکرد می‌تواند اختلاف جدی قیمت بسازد.

اسلاید ۴:
نادیده گرفتن بدنه و رنگ
هر اختلاف قیمت، تخفیف نیست.

اسلاید ۵:
قضاوت با یک آگهی
یک آگهی به‌تنهایی بازار نیست.

اسلاید ۶:
یکی گرفتن قیمت آگهی با قیمت معامله
عددی که نوشته شده همیشه عددی نیست که معامله می‌شود.

اسلاید آخر:
«فرصت واقعی را با داده پیدا کن، نه با حدس.»
دعوت به اقدام:
«ذخیره کن و برای کسی که دنبال ماشین می‌گرده بفرست.»

کپشن پیشنهادی:
خرید خوب از مقایسه درست شروع می‌شود.
قبل از تصمیم، مدل، سال، کارکرد، بدنه و نمونه‌های هم‌گروه را کنار هم ببین.
@select_carr
@select_carrr
"""


def _post_educational(theme: str, ctx: dict[str, Any]) -> str:
    themes: dict[str, dict[str, Any]] = {
        "buy_checklist": {
            "cover": "قبل از خرید خودرو این ۷ مورد را چک کن",
            "slides": [
                "مدل، تیپ و سال دقیق را مشخص کن.",
                "کارکرد را با سن خودرو بسنج.",
                "بدنه و شاسی را کارشناسی کن.",
                "چند نمونه هم‌گروه را قیمت بگیر.",
                "هزینه‌های عقب‌افتاده را حساب کن.",
                "مدارک و مالکیت را قبل از پرداخت بررسی کن.",
                "برای تصمیم، حاشیه امن مالی داشته باش.",
            ],
            "comment": "کدوم مورد رو خودت همیشه اول چک می‌کنی؟",
        },
        "ad_red_flags": {
            "cover": "۶ زنگ خطر در آگهی خودرو",
            "slides": [
                "قیمت خیلی پایین بدون دلیل روشن.",
                "اصرار برای بیعانه قبل از بازدید.",
                "عکس‌های کم، قدیمی یا نامرتبط.",
                "تناقض بین توضیحات و حرف فروشنده.",
                "فشار برای تصمیم فوری.",
                "جواب مبهم درباره رنگ، شاسی یا مالکیت.",
            ],
            "comment": "کدوم زنگ خطر رو بیشتر دیدی؟",
        },
        "seller_questions": {
            "cover": "قبل از بازدید این ۷ سؤال را بپرس",
            "slides": [
                "مالک خودرو خود شمایید؟",
                "رنگ یا تعویض قطعه داشته؟",
                "آسیب شاسی یا ضربه جدی داشته؟",
                "کارکرد واقعی و قابل استناده؟",
                "آخرین سرویس مهم چه زمانی بوده؟",
                "بیمه و معاینه فنی چه وضعی دارد؟",
                "دلیل فروش چیست؟",
            ],
            "comment": "یک سؤال مهم دیگه که می‌پرسی چیه؟",
        },
        "price_checklist": {
            "cover": "چطور بفهمیم قیمت یک ماشین منطقیه؟",
            "slides": [
                "فقط اسم مدل را مقایسه نکن؛ تیپ مهم است.",
                "سال تولید باید یکسان یا نزدیک باشد.",
                "کارکرد را جداگانه اثر بده.",
                "رنگ و بدنه می‌تواند اختلاف قیمت بسازد.",
                "یک آگهی ملاک نیست؛ چند نمونه لازم داری.",
                "قیمت آگهی را با قیمت معامله یکی نگیر.",
            ],
            "comment": "تو برای قیمت‌گیری از چند آگهی استفاده می‌کنی؟",
        },
        "inspection_checklist": {
            "cover": "کارشناسی خودرو فقط دستگاه رنگ نیست",
            "slides": [
                "بدنه و ضخامت رنگ.",
                "شاسی و نقاط سازه‌ای.",
                "موتور و نشتی‌ها.",
                "گیربکس و رفتار در حرکت.",
                "جلوبندی، ترمز و لاستیک.",
                "برق، آپشن‌ها و خطاهای ثبت‌شده.",
                "تطبیق وضعیت واقعی با ادعای فروشنده.",
            ],
            "comment": "کدوم بخش کارشناسی رو بیشتر جدی می‌گیری؟",
        },
        "negotiation_checklist": {
            "cover": "برای تخفیف گرفتن این ۵ کار را انجام بده",
            "slides": [
                "قبل از مذاکره قیمت هم‌گروه‌ها را داشته باش.",
                "ایراد واقعی را با هزینه تقریبی جدا کن.",
                "پیشنهاد عددی بده، نه «آخرش چند؟».",
                "سکوت بعد از پیشنهاد را تحمل کن.",
                "اگر فاصله غیرمنطقی است، از معامله خارج شو.",
            ],
            "comment": "سخت‌ترین بخش مذاکره برای تو کدومه؟",
        },
        "documents": {
            "cover": "قبل از پرداخت پول، این مدارک را ببین",
            "slides": [
                "تطبیق مشخصات مالک با مدارک.",
                "تطبیق شماره‌های خودرو با اسناد.",
                "وضعیت خلافی و تعهدات مرتبط.",
                "اعتبار بیمه و مدارک لازم انتقال.",
                "شفاف بودن وکالت یا نمایندگی در صورت وجود.",
                "پرداخت را با فرآیند رسمی معامله هماهنگ کن.",
            ],
            "comment": "تا حالا معامله‌ای به خاطر مدارک عقب افتاده؟",
        },
        "hidden_costs": {
            "cover": "۷ خرجی که بعد از خرید ماشین یادت می‌ره",
            "slides": [
                "سرویس اولیه و روغن‌ها.",
                "لاستیک.",
                "باتری.",
                "بیمه و هزینه‌های انتقال.",
                "ایرادهای عقب‌افتاده مالک قبلی.",
                "مصرفی‌های ترمز و جلوبندی.",
                "حاشیه امن برای خرابی غیرمنتظره.",
            ],
            "comment": "بزرگ‌ترین خرج بعد از خریدت چی بوده؟",
        },
        "resale_value": {
            "cover": "قبل از خرید، به فروش بعدی هم فکر کن",
            "slides": [
                "تقاضای بازار آن مدل را ببین.",
                "فراوانی قطعه و تعمیرکار مهم است.",
                "تیپ‌های کم‌تقاضا ممکن است دیرتر فروش بروند.",
                "رنگ‌های خاص می‌توانند دایره خریدار را محدود کنند.",
                "شرایط نگهداری روی فروش بعدی اثر دارد.",
                "خودروی خوب باید مسیر خروج منطقی هم داشته باشد.",
            ],
            "comment": "نقدشوندگی برای تو چقدر مهمه؟",
        },
        "market_myths": {
            "cover": "۵ باور اشتباه درباره بازار خودرو",
            "slides": [
                "هر رشد دلار یعنی همه خودروها همان لحظه گران می‌شوند.",
                "هر آگهی ارزان یعنی فرصت خرید.",
                "کارکرد پایین همیشه یعنی خودروی بهتر.",
                "هر رنگ‌شدگی یعنی خودرو ارزش خرید ندارد.",
                "قیمت آگهی همان قیمت معامله است.",
            ],
            "comment": "کدوم باور رو بیشتر از بقیه شنیدی؟",
        },
        "mileage_myth": {
            "cover": "کارکرد پایین همیشه امتیاز نیست",
            "slides": [
                "کارکرد را با سن خودرو مقایسه کن.",
                "سابقه سرویس از عدد تنها مهم‌تر است.",
                "خواب طولانی خودرو هم می‌تواند هزینه بسازد.",
                "کیفیت نگهداری را بررسی کن.",
                "عدد کیلومتر یک نشانه است، نه حکم نهایی.",
            ],
            "comment": "کم‌کار بدون سابقه یا پرکار با سرویس منظم؟",
        },
        "body_paint": {
            "cover": "رنگ‌شدگی را این‌طوری قضاوت کن",
            "slides": [
                "محل رنگ‌شدگی را مشخص کن.",
                "ترمیم ظاهری را با آسیب سازه‌ای یکی ندان.",
                "شاسی و نقاط اصلی را جدا بررسی کن.",
                "افت قیمت باید با نوع آسیب تناسب داشته باشد.",
                "کارشناسی نتیجه را قطعی‌تر می‌کند.",
            ],
            "comment": "یک لکه رنگ برای تو خط قرمزه؟",
        },
    }

    item = themes[theme]
    slides = "\n\n".join(
        f"اسلاید {fa_num(i + 2)}:\n{text}"
        for i, text in enumerate(item["slides"])
    )
    return f"""🖼 پیشنهاد پست آموزشی و وایرال امروز

هدف اصلی: ذخیره + اشتراک‌گذاری + فالو

۳ پیشنهاد کاور:
۱. «{item['cover']}»
۲. «این پست رو قبل از خرید ماشین ذخیره کن»
۳. «اشتباهی که می‌تونه چند ده میلیون هزینه بسازه»

ساختار کاروسل:

اسلاید ۱ — کاور
{item['cover']}
زیرتیتر: کوتاه، کاربردی، بدون حاشیه

{slides}

اسلاید آخر:
«اگر نکات واقعی خرید، فروش و کارشناسی خودرو به کارت میاد، فالو کن.»

سؤال کامنت‌گیر:
«{item['comment']}»

قاعده طراحی:
• هر اسلاید فقط یک نکته.
• تیتر حداکثر ۶ تا ۸ کلمه.
• اسلاید دوم باید سریع ارزش بدهد تا مخاطب ادامه بدهد.
• یک اسلاید وسط کار باید «باور اشتباه / نکته غیرمنتظره» داشته باشد.
• اسلاید آخر فقط دعوت به فالو و ذخیره، نه فروش مستقیم.

کپشن پیشنهادی:
این پست برای ذخیره کردنه.
قبل از تصمیم در بازار خودرو، این نکته‌ها رو یک‌بار مرور کن.
اگر محتوای کوتاه، کاربردی و بدون تبلیغ درباره خرید و فروش خودرو می‌خوای، صفحه رو دنبال کن.

@select_carr
@select_carrr
"""


def build_post(ctx: dict[str, Any], state: dict[str, Any]) -> tuple[str, str]:
    family = choose_family("post", ctx, state)
    return family, _post_educational(family, ctx)

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
    # Keep publications from the last 6 hours eligible for notification, while
    # silently baselining older history. This also survives delayed GitHub
    # workflow_run delivery.
    initialized = bool(state.get("instagram_notification_initialized"))
    recent_cutoff = now_tehran() - timedelta(hours=6)

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
        ]
    }
    ctx = {
        "deal": None,
        "buyer": None,
        "market": fake_market,
        "cars": fake_cars,
    }
    state: dict[str, Any] = {}

    samples = [
        _reel_educational(theme, ctx)
        for theme in EDUCATIONAL_REEL_THEMES
    ] + [
        _post_educational(theme, ctx)
        for theme in EDUCATIONAL_POST_THEMES
    ]

    forbidden = (
        "Deal واقعی",
        "درخواست خرید واقعی",
        "فرصت خرید امروز",
        "خریدار واقعی داریم",
    )

    assert all(len(x.strip()) > 300 for x in samples)
    assert all(not any(word in x for word in forbidden) for x in samples)
    assert choose_family("reel", ctx, state) in EDUCATIONAL_REEL_THEMES
    assert choose_family("post", ctx, state) in EDUCATIONAL_POST_THEMES
    assert len(split_message("\n\n".join(samples), limit=1000)) > 1

    print(
        json.dumps(
            {
                "status": "ok",
                "version": VERSION,
                "reel_themes": len(EDUCATIONAL_REEL_THEMES),
                "post_themes": len(EDUCATIONAL_POST_THEMES),
                "deal_promotion_test": "blocked",
                "buyer_promotion_test": "blocked",
                "telegram_split_test": "ok",
                "notification_logic": "unchanged",
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
