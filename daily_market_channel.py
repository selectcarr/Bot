#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent Stage 6 mirror for selected @DO_L4 Telegram posts.

- Market rates: latest same-day rate table, published at 12:00 and 18:00 Tehran.
- Zero-car prices: checked hourly; first qualifying post of each Tehran day is
  forwarded on the first scan after publication.
- Source branding/tail commentary is removed per project policy and replaced
  with the project's support admin.
- No System B database, collectors, deal engine, or sent-deal memory is touched.
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


LOGGER = logging.getLogger("daily_market_channel")
TEHRAN = ZoneInfo("Asia/Tehran")
SOURCE_CHANNEL = "DO_L4"
SOURCE_URL = f"https://t.me/s/{SOURCE_CHANNEL}"
SUPPORT_ADMIN = "@chanelll_vip"
STATE_VERSION = 1
DEFAULT_STATE = Path("daily_market_runtime/state.json")
MAX_TELEGRAM_TEXT = 4096

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.6",
}


@dataclass(frozen=True)
class SourcePost:
    post_id: int
    text: str
    published_at: datetime

    @property
    def tehran_date(self) -> str:
        return self.published_at.astimezone(TEHRAN).date().isoformat()


def normalize_text(value: str) -> str:
    value = str(value or "")
    value = value.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    value = value.replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"[ \t]+", " ", value).strip()


def _parse_datetime(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def fetch_source_html() -> str:
    response = requests.get(
        SOURCE_URL,
        headers=HEADERS,
        timeout=(8, 25),
        allow_redirects=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        raise RuntimeError("Unexpected Telegram source content type")
    return response.text


def parse_posts(page_html: str) -> list[SourcePost]:
    soup = BeautifulSoup(page_html, "html.parser")
    posts: list[SourcePost] = []
    for message in soup.select(".tgme_widget_message[data-post]"):
        key = str(message.get("data-post", ""))
        if "/" not in key:
            continue
        channel, raw_id = key.rsplit("/", 1)
        if channel.casefold() != SOURCE_CHANNEL.casefold() or not raw_id.isdigit():
            continue
        body = message.select_one(".tgme_widget_message_text")
        clock = message.select_one("time[datetime]")
        if body is None or clock is None:
            continue
        stamp = _parse_datetime(clock.get("datetime", ""))
        if stamp is None:
            continue
        text = body.get_text("\n", strip=True)
        text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
        if text:
            posts.append(SourcePost(int(raw_id), text, stamp))
    posts.sort(key=lambda p: (p.published_at, p.post_id))
    return posts


def is_market_rates(post: SourcePost) -> bool:
    text = normalize_text(post.text)
    return (
        "نرخ فروش" in text
        and "دلار" in text
        and "ارز" in text
        and ("سکه" in text or "طلا" in text)
    )


def is_zero_car_prices(post: SourcePost) -> bool:
    text = normalize_text(post.text)
    return (
        "آخرین بروزرسانی قیمت خودروهای پرفروش پلاک ملی" in text
        and len(_car_price_lines(post.text)) >= 8
    )


def _car_price_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    return [
        line
        for line in lines
        if ("⬅" in line or "←" in line)
        and re.search(r"\d", line)
    ]


def _replace_source_signature(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if "@Dollar_Offical" in line or "@dollar_offical" in line.casefold():
            continue
        lines.append(line.rstrip())
    cleaned = "\n".join(lines).strip()
    return cleaned


def support_footer() -> str:
    return f"👇\n( ادمین پشتیبانی : {SUPPORT_ADMIN} )"


def format_market_rates(post: SourcePost) -> str:
    body = _replace_source_signature(post.text)
    return body.rstrip() + "\n\n" + support_footer()


def format_zero_car_prices(post: SourcePost) -> str:
    """Keep the source header + car list, discard everything after last car row."""
    lines = [line.rstrip() for line in post.text.splitlines()]
    matching = [
        i for i, line in enumerate(lines)
        if ("⬅" in line or "←" in line) and re.search(r"\d", line)
    ]
    if len(matching) < 8:
        raise ValueError("Insufficient car-price lines")
    last = matching[-1]
    body_lines = lines[: last + 1]
    body = _replace_source_signature("\n".join(body_lines)).strip()
    return body + "\n\n" + support_footer()


def _empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "market_slots": {},
        "car_sent_dates": {},
    }


def load_state(path: Path) -> dict:
    if not path.is_file():
        return _empty_state()
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Stage 6 state is unreadable")
    if not isinstance(obj, dict) or obj.get("version") != STATE_VERSION:
        raise RuntimeError("Stage 6 state version mismatch")
    obj.setdefault("market_slots", {})
    obj.setdefault("car_sent_dates", {})
    if not isinstance(obj["market_slots"], dict) or not isinstance(obj["car_sent_dates"], dict):
        raise RuntimeError("Stage 6 state shape mismatch")
    return obj


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _split_text(text: str, limit: int = MAX_TELEGRAM_TEXT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            for start in range(0, len(line), limit):
                chunks.append(line[start:start + limit].rstrip())
            continue
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def send_telegram(text: str) -> list[int]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram secrets are missing")
    ids: list[int] = []
    for part in _split_text(text):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": part,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        payload = response.json() if response.content else {}
        if response.status_code != 200 or not isinstance(payload, dict) or payload.get("ok") is not True:
            description = payload.get("description", "unknown") if isinstance(payload, dict) else "unknown"
            raise RuntimeError(
                f"Telegram send failed: HTTP {response.status_code} {description}"
            )
        ids.append(int(payload["result"]["message_id"]))
    return ids


def _latest_today(posts: Iterable[SourcePost], predicate, today: str) -> SourcePost | None:
    matches = [p for p in posts if predicate(p) and p.tehran_date == today]
    return max(matches, key=lambda p: (p.published_at, p.post_id), default=None)


def run_rates(*, slot: str, state_path: Path, dry_run: bool = False, force: bool = False) -> int:
    now = datetime.now(TEHRAN)
    today = now.date().isoformat()
    slot = str(slot).strip()
    if slot not in {"12", "18"}:
        raise ValueError("Rate slot must be 12 or 18")
    state = load_state(state_path)
    slot_key = f"{today}:{slot}"
    if not force and slot_key in state["market_slots"]:
        LOGGER.info("MarketRatesSkipped reason=slot_already_sent slot=%s", slot_key)
        return 0

    posts = parse_posts(fetch_source_html())
    post = _latest_today(posts, is_market_rates, today)
    if post is None:
        LOGGER.warning("MarketRatesSkipped reason=no_same_day_source_post slot=%s", slot_key)
        return 0

    output = format_market_rates(post)
    LOGGER.info(
        "MarketRatesSelected source_post=%s source_time=%s slot=%s dry_run=%s",
        post.post_id, post.published_at.isoformat(), slot_key, dry_run,
    )
    if dry_run:
        print(output)
        return 0

    message_ids = send_telegram(output)
    state["market_slots"][slot_key] = {
        "source_post_id": post.post_id,
        "sent_at": now.isoformat(),
        "message_ids": message_ids,
    }
    # Bound state to recent slots only.
    if len(state["market_slots"]) > 30:
        for key in sorted(state["market_slots"])[:-30]:
            del state["market_slots"][key]
    save_state(state_path, state)
    LOGGER.info("MarketRatesSent source_post=%s message_ids=%s", post.post_id, message_ids)
    return 0


def run_cars(*, state_path: Path, dry_run: bool = False, force: bool = False) -> int:
    now = datetime.now(TEHRAN)
    today = now.date().isoformat()
    state = load_state(state_path)
    if not force and today in state["car_sent_dates"]:
        LOGGER.info("ZeroCarSkipped reason=today_already_sent date=%s", today)
        return 0

    posts = parse_posts(fetch_source_html())
    post = _latest_today(posts, is_zero_car_prices, today)
    if post is None:
        LOGGER.info("ZeroCarSkipped reason=no_same_day_source_post date=%s", today)
        return 0

    output = format_zero_car_prices(post)
    LOGGER.info(
        "ZeroCarSelected source_post=%s source_time=%s car_rows=%s dry_run=%s",
        post.post_id, post.published_at.isoformat(), len(_car_price_lines(post.text)), dry_run,
    )
    if dry_run:
        print(output)
        return 0

    message_ids = send_telegram(output)
    state["car_sent_dates"][today] = {
        "source_post_id": post.post_id,
        "sent_at": now.isoformat(),
        "message_ids": message_ids,
    }
    if len(state["car_sent_dates"]) > 15:
        for key in sorted(state["car_sent_dates"])[:-15]:
            del state["car_sent_dates"][key]
    save_state(state_path, state)
    LOGGER.info("ZeroCarSent source_post=%s message_ids=%s", post.post_id, message_ids)
    return 0


def self_test() -> None:
    fixture = """
    <html><body>
    <div class="tgme_widget_message" data-post="DO_L4/100">
      <div class="tgme_widget_message_text">نرخ فروش #دلار، #ارز، #سکه و #طلا در بازار
💵 دلار: 232,410 تومان
💫 گرم ۱۸ عیار: 24,520 هـ.تومان
💰 @Dollar_Offical</div>
      <time datetime="2026-09-21T08:00:00+00:00"></time>
    </div>
    <div class="tgme_widget_message" data-post="DO_L4/101">
      <div class="tgme_widget_message_text">🟢 آخرین بروزرسانی قیمت خودروهای پرفروش پلاک ملی طبق استعلام
❌ این رسانه هیچ نقشی در تعیین قیمتها ندارد
ساینا⬅️1.440
سهند⬅️1.555
کوییک(S)⬅️1.406
اطلس⬅️1.705
شاهین⬅️2.170
سورن⬅️1.807
رانا⬅️1.910
پ۲۰۷⬅️1.895
دنا⬅️2.420
تارا⬅️2.275
.
‼️ آماده باش کامل بازار خودرو
🟢 زیر دیگ بازار خودرو روشن شده
💰 @Dollar_Offical</div>
      <time datetime="2026-09-21T09:00:00+00:00"></time>
    </div>
    </body></html>
    """
    posts = parse_posts(fixture)
    if len(posts) != 2:
        raise AssertionError("Telegram source parsing failed")
    rate, car = posts
    if not is_market_rates(rate):
        raise AssertionError("Rate classifier failed")
    if not is_zero_car_prices(car):
        raise AssertionError("Car classifier failed")
    transformed_rate = format_market_rates(rate)
    if "@Dollar_Offical" in transformed_rate or SUPPORT_ADMIN not in transformed_rate:
        raise AssertionError("Rate signature replacement failed")
    transformed_car = format_zero_car_prices(car)
    if "زیر دیگ بازار" in transformed_car or "آماده باش" in transformed_car:
        raise AssertionError("Car tail commentary was not removed")
    if "تارا⬅️2.275" not in transformed_car or SUPPORT_ADMIN not in transformed_car:
        raise AssertionError("Car body/footer transform failed")
    if len(_split_text("a\n" * 5000)) < 2:
        raise AssertionError("Telegram chunking failed")
    print("daily market channel self-test: OK")
    print("source=@DO_L4 rates=12,18_tehran zero_car_scan=hourly support=@chanelll_vip")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("rates", "cars", "self_test"), required=True)
    parser.add_argument("--slot", choices=("12", "18"))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if args.mode == "self_test":
        self_test()
        return
    state_path = Path(args.state)
    if args.mode == "rates":
        if not args.slot:
            parser.error("--slot is required for rates mode")
        raise SystemExit(run_rates(
            slot=args.slot, state_path=state_path, dry_run=args.dry_run, force=args.force
        ))
    raise SystemExit(run_cars(
        state_path=state_path, dry_run=args.dry_run, force=args.force
    ))


if __name__ == "__main__":
    main()
