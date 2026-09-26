#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent
MARKET_DB = Path(os.getenv(
    "SELECT_CARR_MARKET_DB",
    ROOT / "select_carr_market_runtime" / "select_carr_market.sqlite3",
))
ENC_BANK = Path(os.getenv(
    "SELECT_CARR_ENCRYPTED_DB",
    ROOT / "private_data" / "select_carr_banks.sqlite3.enc",
))
DATA_SECRET = os.getenv("SELECT_CARR_DATA_SECRET", "").strip()
BOT_TOKEN = os.getenv("SELECT_CARR_STAGE3_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("SELECT_CARR_STAGE3_CHAT_ID", "").strip()
DEAL_MAX_AGE_HOURS = int(os.getenv("SELECT_CARR_MATCH_DEAL_MAX_AGE_HOURS", "168"))
STAGE3_VERSION = "2.0.0"

STAGE3_SCHEMA = """
CREATE TABLE IF NOT EXISTS sc_stage3_matches (
    buyer_submission_id TEXT NOT NULL,
    deal_source_key TEXT NOT NULL,
    matched_at TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    buyer_phone TEXT NOT NULL,
    buyer_model TEXT NOT NULL,
    buyer_budget INTEGER,
    deal_price INTEGER NOT NULL,
    reference_price INTEGER NOT NULL,
    discount_percent REAL NOT NULL,
    deal_source TEXT NOT NULL,
    deal_url TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (buyer_submission_id, deal_source_key)
);

CREATE INDEX IF NOT EXISTS idx_sc_stage3_matches_notified
ON sc_stage3_matches(notified_at);
"""

ALIASES = {
    "پژو": "peugeot", "peugeot": "peugeot",
    "مرسدس": "mercedes", "مرسدس بنز": "mercedes", "بنز": "mercedes",
    "mercedes": "mercedes", "mercedes-benz": "mercedes", "mercedesbenz": "mercedes",
    "بی ام و": "bmw", "بی‌ام‌و": "bmw", "bmw": "bmw",
    "هیوندای": "hyundai", "hyundai": "hyundai",
    "کیا": "kia", "kia": "kia",
    "تویوتا": "toyota", "toyota": "toyota",
    "رنو": "renault", "renault": "renault",
    "چری": "chery", "chery": "chery",
    "فونیکس": "fownix", "fownix": "fownix",
    "لاماری": "lamari", "lamari": "lamari",
    "جک": "jac", "jac": "jac",
    "ام وی ام": "mvm", "ام‌وی‌ام": "mvm", "mvm": "mvm",
    "دنا": "dena", "dena": "dena",
    "تارا": "tara", "tara": "tara",
    "شاهین": "shahin", "shahin": "shahin",
}

STOPWORDS = {
    "خودرو", "ماشین", "مدل", "صفر", "کارکرده", "اتومات", "اتوماتیک",
    "دنده", "دنده‌ای", "دنده ای", "فول", "تیپ", "سدان", "شاسی", "بلند"
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def fernet() -> Fernet:
    if not DATA_SECRET:
        raise RuntimeError("SELECT_CARR_DATA_SECRET is missing")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(("select-carr-bank-v1:" + DATA_SECRET).encode("utf-8")).digest()
    )
    return Fernet(key)


def decrypt_bank(out_path: Path) -> None:
    if not ENC_BANK.exists():
        raise RuntimeError("Encrypted Select Carr bank does not exist")
    try:
        out_path.write_bytes(fernet().decrypt(ENC_BANK.read_bytes()))
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt Select Carr bank") from exc


def encrypt_bank(in_path: Path) -> None:
    ENC_BANK.parent.mkdir(parents=True, exist_ok=True)
    ENC_BANK.write_bytes(fernet().encrypt(in_path.read_bytes()))


def normalize_text(value: object) -> str:
    s = str(value or "").lower().strip()
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    fa = "۰۱۲۳۴۵۶۷۸۹"
    ar = "٠١٢٣٤٥٦٧٨٩"
    s = s.translate(str.maketrans(fa + ar, "0123456789" * 2))
    s = re.sub(r"[-_/.,()]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for source, target in sorted(ALIASES.items(), key=lambda x: -len(x[0])):
        s = s.replace(source, target)
    return re.sub(r"\s+", " ", s).strip()


def model_tokens(value: object) -> list[str]:
    text = normalize_text(value)
    tokens = []
    for token in re.findall(r"[a-z0-9\u0600-\u06ff]+", text):
        if token in STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.append(token)
    return list(dict.fromkeys(tokens))


def requested_model_options(value: object) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?:\s+یا\s+|\s+or\s+|[,،;/|\n]+)", raw, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()] or [raw]


def model_matches(buyer_model: str, deal_text: str) -> bool:
    hay = normalize_text(deal_text)
    for option in requested_model_options(buyer_model):
        wanted = model_tokens(option)
        if wanted and all(token in hay for token in wanted):
            return True
    return False


def parse_iso(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def load_buyers(bank_db: Path) -> list[dict]:
    con = sqlite3.connect(bank_db)
    con.row_factory = sqlite3.Row
    try:
        if not table_exists(con, "sc_intake_submissions"):
            raise RuntimeError("sc_intake_submissions is missing from Select Carr bank")
        rows = con.execute(
            """
            SELECT submission_id, phone, city, model, budget, down_payment,
                   payment_type, vehicle_condition, urgency, purpose,
                   source, campaign_code, content_code, status, created_at, expires_at
            FROM sc_intake_submissions
            WHERE request_type='buy' AND status='active'
            ORDER BY created_at ASC
            """
        ).fetchall()
        now = now_utc()
        buyers = []
        for row in rows:
            item = dict(row)
            expiry = parse_iso(item.get("expires_at"))
            if expiry is not None and expiry <= now:
                continue
            if not str(item.get("model") or "").strip():
                continue
            buyers.append(item)
        return buyers
    finally:
        con.close()


def load_deals(market_db: Path) -> list[dict]:
    if not market_db.exists():
        raise RuntimeError("Select Carr market database does not exist")
    con = sqlite3.connect(market_db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        if not table_exists(con, "market_deals"):
            raise RuntimeError("Select Carr market table is missing: market_deals")
        rows = con.execute(
            """
            SELECT event_key, source_key, source, source_ad_id,
                   candidate_price AS deal_price,
                   average_price AS reference_price,
                   discount_percent, sample_count, system_execution_id,
                   candidate_seen_at, queued_at, sent_at, status,
                   COALESCE(url, '') AS url,
                   '' AS title,
                   COALESCE(brand, '') AS brand,
                   COALESCE(model, '') AS model,
                   COALESCE(trim, '') AS trim,
                   model_year,
                   COALESCE(condition, '') AS vehicle_condition,
                   COALESCE(body_condition, '') AS body_condition,
                   mileage
            FROM market_deals
            WHERE status IN ('sent', 'suppressed_system_b')
            ORDER BY COALESCE(sent_at, queued_at, candidate_seen_at) DESC, event_key ASC
            """
        ).fetchall()
        cutoff = now_utc() - timedelta(hours=max(1, DEAL_MAX_AGE_HOURS))
        deals = []
        for row in rows:
            item = dict(row)
            effective_time = (
                parse_iso(item.get("sent_at"))
                or parse_iso(item.get("queued_at"))
                or parse_iso(item.get("candidate_seen_at"))
            )
            if effective_time is None or effective_time < cutoff:
                continue
            if int(item.get("deal_price") or 0) <= 0:
                continue
            deals.append(item)
        return deals
    finally:
        con.close()


def deal_event_key(deal: dict) -> str:
    return str(deal.get("event_key") or deal.get("source_key") or "").strip()


def buyer_deal_matches(buyer: dict, deal: dict) -> bool:
    budget = buyer.get("budget")
    if budget is not None and int(deal.get("deal_price") or 0) > int(budget):
        return False
    deal_text = " ".join(
        str(deal.get(k) or "")
        for k in ("title", "brand", "model", "trim")
    )
    return model_matches(str(buyer.get("model") or ""), deal_text)


def already_notified(bank_db: Path, buyer_id: str, deal: dict) -> bool:
    event = deal_event_key(deal)
    source_key = str(deal.get("source_key") or "")
    price = int(deal.get("deal_price") or 0)
    con = sqlite3.connect(bank_db)
    try:
        con.executescript(STAGE3_SCHEMA)
        row = con.execute(
            """
            SELECT 1 FROM sc_stage3_matches
            WHERE buyer_submission_id=?
              AND (deal_source_key=? OR (deal_source_key=? AND deal_price=?))
            LIMIT 1
            """,
            (buyer_id, event, source_key, price),
        ).fetchone()
        con.commit()
        return row is not None
    finally:
        con.close()


def save_match(bank_db: Path, buyer: dict, deal: dict) -> None:
    con = sqlite3.connect(bank_db)
    try:
        con.executescript(STAGE3_SCHEMA)
        stamp = now_iso()
        con.execute(
            """
            INSERT OR IGNORE INTO sc_stage3_matches(
                buyer_submission_id, deal_source_key, matched_at, notified_at,
                buyer_phone, buyer_model, buyer_budget,
                deal_price, reference_price, discount_percent,
                deal_source, deal_url
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(buyer["submission_id"]),
                deal_event_key(deal),
                stamp,
                stamp,
                str(buyer.get("phone") or ""),
                str(buyer.get("model") or ""),
                buyer.get("budget"),
                int(deal.get("deal_price") or 0),
                int(deal.get("reference_price") or 0),
                float(deal.get("discount_percent") or 0.0),
                str(deal.get("source") or ""),
                str(deal.get("url") or ""),
            ),
        )
        con.commit()
    finally:
        con.close()


def send_telegram(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Stage 3 Telegram secrets are missing")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = json.dumps({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("ok") is not True:
        raise RuntimeError("Telegram send failed")


def money(value: object) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "-"


def match_message(buyer: dict, deal: dict) -> str:
    vehicle = " ".join(
        str(deal.get(k) or "").strip()
        for k in ("brand", "model", "trim")
        if str(deal.get(k) or "").strip()
    )
    year = deal.get("model_year")
    if year:
        vehicle = f"{vehicle} مدل {year}".strip()

    return (
        "🎯 تطبیق جدید Select Carr\n\n"
        "👤 درخواست خریدار:\n"
        f"خودرو: {buyer.get('model') or '-'}\n"
        f"بودجه: {money(buyer.get('budget'))} تومان\n"
        f"شهر: {buyer.get('city') or '-'}\n"
        f"فوریت: {buyer.get('urgency') or '-'}\n"
        f"تماس: {buyer.get('phone') or '-'}\n\n"
        "🚗 فرصت موجود:\n"
        f"خودرو: {vehicle or deal.get('title') or '-'}\n"
        f"قیمت: {money(deal.get('deal_price'))} تومان\n"
        f"قیمت مرجع: {money(deal.get('reference_price'))} تومان\n"
        f"زیر بازار: {float(deal.get('discount_percent') or 0):.2f}٪\n"
        f"منبع: {deal.get('source') or '-'}\n"
        f"لینک: {deal.get('url') or '-'}"
    )


def run_stage3() -> int:
    missing = [
        name for name, value in (
            ("SELECT_CARR_DATA_SECRET", DATA_SECRET),
            ("SELECT_CARR_STAGE3_BOT_TOKEN", BOT_TOKEN),
            ("SELECT_CARR_STAGE3_CHAT_ID", CHAT_ID),
        ) if not value
    ]

    if missing:
        print(
            "Missing secrets: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    buyers_count = 0
    deals_count = 0
    matches_sent = 0

    with tempfile.TemporaryDirectory() as td:
        bank_db = Path(td) / "select_carr_banks.sqlite3"
        decrypt_bank(bank_db)

        con = sqlite3.connect(bank_db)
        try:
            con.executescript(STAGE3_SCHEMA)
            con.commit()
        finally:
            con.close()

        buyers = load_buyers(bank_db)
        deals = load_deals(MARKET_DB)

        buyers_count = len(buyers)
        deals_count = len(deals)

        for buyer in buyers:
            for deal in deals:
                if not buyer_deal_matches(buyer, deal):
                    continue

                buyer_id = str(buyer["submission_id"])

                if already_notified(
                    bank_db,
                    buyer_id,
                    deal,
                ):
                    continue

                send_telegram(
                    match_message(
                        buyer,
                        deal,
                    )
                )

                save_match(
                    bank_db,
                    buyer,
                    deal,
                )

                matches_sent += 1

        encrypt_bank(bank_db)

    print(json.dumps({
        "stage3_version": STAGE3_VERSION,
        "deal_source": "select_carr_market.market_deals",
        "buyers_checked": buyers_count,
        "deals_checked": deals_count,
        "new_matches_sent": matches_sent,
    }, ensure_ascii=False))

    return 0


# Offline tests: no network and no real bank/secrets.

MARKET_TEST_SCHEMA = """
CREATE TABLE market_deals(
    event_key TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,
    url TEXT NOT NULL,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    trim TEXT NOT NULL,
    model_year INTEGER NOT NULL,
    condition TEXT NOT NULL,
    body_condition TEXT NOT NULL,
    mileage INTEGER,
    candidate_price INTEGER NOT NULL,
    average_price INTEGER NOT NULL,
    discount_percent REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    system_execution_id INTEGER NOT NULL,
    candidate_seen_at TEXT NOT NULL,
    status TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    sent_at TEXT,
    telegram_message_id INTEGER,
    last_error TEXT
);
"""


class Stage3Tests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)

        self.market = root / "market.sqlite3"
        self.bank = root / "bank.sqlite3"

        with sqlite3.connect(self.market) as con:
            con.executescript(MARKET_TEST_SCHEMA)

        with sqlite3.connect(self.bank) as con:
            con.executescript(STAGE3_SCHEMA)

    def tearDown(self):
        self.tmp.cleanup()

    def deal(
        self,
        *,
        event="evt1",
        source_key="divar|1",
        price=900,
        status="sent",
        sent_at=None,
        model="207",
        brand="Peugeot",
        trim="اتوماتیک",
    ):
        stamp = sent_at or now_iso()

        return {
            "event_key": event,
            "source_key": source_key,
            "source": "divar",
            "source_ad_id": "1",
            "url": "https://example.test/1",
            "brand": brand,
            "model": model,
            "trim": trim,
            "model_year": 1403,
            "condition": "used",
            "body_condition": "clean",
            "mileage": 50000,
            "candidate_price": price,
            "average_price": 1000,
            "discount_percent": 10.0,
            "sample_count": 3,
            "system_execution_id": 10,
            "candidate_seen_at": stamp,
            "status": status,
            "queued_at": stamp,
            "sent_at": stamp,
            "telegram_message_id": 1 if status == "sent" else None,
            "last_error": None,
        }

    def insert_deal(self, row):
        with sqlite3.connect(self.market) as con:
            con.execute(
                """
                INSERT INTO market_deals VALUES(
                    :event_key,
                    :source_key,
                    :source,
                    :source_ad_id,
                    :url,
                    :brand,
                    :model,
                    :trim,
                    :model_year,
                    :condition,
                    :body_condition,
                    :mileage,
                    :candidate_price,
                    :average_price,
                    :discount_percent,
                    :sample_count,
                    :system_execution_id,
                    :candidate_seen_at,
                    :status,
                    :queued_at,
                    :sent_at,
                    :telegram_message_id,
                    :last_error
                )
                """,
                row,
            )

    def test_01_sent_loaded(self):
        self.insert_deal(
            self.deal()
        )

        rows = load_deals(
            self.market
        )

        self.assertEqual(
            (
                len(rows),
                rows[0]["event_key"],
                rows[0]["deal_price"],
            ),
            (
                1,
                "evt1",
                900,
            ),
        )

    def test_02_suppressed_system_b_loaded(self):
        self.insert_deal(
            self.deal(
                status="suppressed_system_b"
            )
        )

        self.assertEqual(
            load_deals(
                self.market
            )[0]["status"],
            "suppressed_system_b",
        )

    def test_03_pending_not_loaded(self):
        self.insert_deal(
            self.deal(
                status="pending"
            )
        )

        self.assertEqual(
            load_deals(
                self.market
            ),
            [],
        )

    def test_04_old_deal_excluded(self):
        old = (
            now_utc()
            - timedelta(
                hours=DEAL_MAX_AGE_HOURS + 2
            )
        ).isoformat()

        self.insert_deal(
            self.deal(
                sent_at=old
            )
        )

        self.assertEqual(
            load_deals(
                self.market
            ),
            [],
        )

    def test_05_budget_blocks(self):
        self.assertFalse(
            buyer_deal_matches(
                {
                    "model": "پژو 207",
                    "budget": 850,
                },
                {
                    "title": "",
                    "brand": "Peugeot",
                    "model": "207",
                    "trim": "",
                    "deal_price": 900,
                },
            )
        )

    def test_06_model_budget_match(self):
        self.assertTrue(
            buyer_deal_matches(
                {
                    "model": "پژو 207",
                    "budget": 950,
                },
                {
                    "title": "",
                    "brand": "Peugeot",
                    "model": "207",
                    "trim": "",
                    "deal_price": 900,
                },
            )
        )

    def test_07_multiple_models_are_alternatives(self):
        self.assertTrue(
            buyer_deal_matches(
                {
                    "model": "پژو 206 یا پژو 207",
                    "budget": 1000,
                },
                {
                    "title": "",
                    "brand": "Peugeot",
                    "model": "207",
                    "trim": "",
                    "deal_price": 900,
                },
            )
        )

    def test_08_unrelated_model_no_match(self):
        self.assertFalse(
            buyer_deal_matches(
                {
                    "model": "تویوتا کرولا",
                    "budget": 1000,
                },
                {
                    "title": "",
                    "brand": "Peugeot",
                    "model": "207",
                    "trim": "",
                    "deal_price": 900,
                },
            )
        )

    def test_09_legacy_same_price_suppressed(self):
        with sqlite3.connect(
            self.bank
        ) as con:

            con.execute(
                """
                INSERT INTO sc_stage3_matches
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "b1",
                    "divar|1",
                    now_iso(),
                    now_iso(),
                    "09",
                    "207",
                    1000,
                    900,
                    1000,
                    10.0,
                    "divar",
                    "u",
                ),
            )

        self.assertTrue(
            already_notified(
                self.bank,
                "b1",
                {
                    "event_key": "evt",
                    "source_key": "divar|1",
                    "deal_price": 900,
                },
            )
        )

    def test_10_legacy_new_price_allowed(self):
        with sqlite3.connect(
            self.bank
        ) as con:

            con.execute(
                """
                INSERT INTO sc_stage3_matches
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "b1",
                    "divar|1",
                    now_iso(),
                    now_iso(),
                    "09",
                    "207",
                    1000,
                    900,
                    1000,
                    10.0,
                    "divar",
                    "u",
                ),
            )

        self.assertFalse(
            already_notified(
                self.bank,
                "b1",
                {
                    "event_key": "evt2",
                    "source_key": "divar|1",
                    "deal_price": 850,
                },
            )
        )

    def test_11_new_memory_uses_event_key(self):
        buyer = {
            "submission_id": "b1",
            "phone": "09",
            "model": "207",
            "budget": 1000,
        }

        deal = {
            "event_key": "evt1",
            "source_key": "divar|1",
            "deal_price": 900,
            "reference_price": 1000,
            "discount_percent": 10.0,
            "source": "divar",
            "url": "u",
        }

        save_match(
            self.bank,
            buyer,
            deal,
        )

        with sqlite3.connect(
            self.bank
        ) as con:

            stored = con.execute(
                """
                SELECT deal_source_key
                FROM sc_stage3_matches
                """
            ).fetchone()[0]

        self.assertEqual(
            stored,
            "evt1",
        )

        self.assertTrue(
            already_notified(
                self.bank,
                "b1",
                deal,
            )
        )

    def test_12_event_fallback(self):
        self.assertEqual(
            deal_event_key({
                "source_key": "telegram|x"
            }),
            "telegram|x",
        )


def run_self_test() -> int:
    result = unittest.TextTestRunner(
        verbosity=2
    ).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(
            Stage3Tests
        )
    )

    if not result.wasSuccessful():
        return 1

    print(
        f"select_carr_stage3 self-test: OK tests={result.testsRun}"
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    return run_stage3()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
