#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent
SYSTEM_B_DB = Path(os.getenv(
    "SELECT_CARR_SYSTEM_B_DB",
    ROOT / "accurate_average_runtime" / "accurate_average.db",
))
ENC_BANK = Path(os.getenv(
    "SELECT_CARR_ENCRYPTED_DB",
    ROOT / "private_data" / "select_carr_banks.sqlite3.enc",
))
DATA_SECRET = os.getenv("SELECT_CARR_DATA_SECRET", "").strip()
BOT_TOKEN = os.getenv("SELECT_CARR_STAGE3_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("SELECT_CARR_STAGE3_CHAT_ID", "").strip()
DEAL_MAX_AGE_HOURS = int(os.getenv("SELECT_CARR_MATCH_DEAL_MAX_AGE_HOURS", "168"))

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
    "پژو": "peugeot",
    "peugeot": "peugeot",
    "مرسدس": "mercedes",
    "مرسدس بنز": "mercedes",
    "بنز": "mercedes",
    "mercedes": "mercedes",
    "mercedes-benz": "mercedes",
    "mercedesbenz": "mercedes",
    "بی ام و": "bmw",
    "بی‌ام‌و": "bmw",
    "bmw": "bmw",
    "هیوندای": "hyundai",
    "hyundai": "hyundai",
    "کیا": "kia",
    "kia": "kia",
    "تویوتا": "toyota",
    "toyota": "toyota",
    "رنو": "renault",
    "renault": "renault",
    "چری": "chery",
    "chery": "chery",
    "فونیکس": "fownix",
    "fownix": "fownix",
    "لاماری": "lamari",
    "lamari": "lamari",
    "جک": "jac",
    "jac": "jac",
    "ام وی ام": "mvm",
    "ام‌وی‌ام": "mvm",
    "mvm": "mvm",
    "دنا": "dena",
    "dena": "dena",
    "تارا": "tara",
    "tara": "tara",
    "شاهین": "shahin",
    "shahin": "shahin",
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

    # Phrase aliases first.
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
    # Keep order, drop duplicates.
    return list(dict.fromkeys(tokens))

def model_matches(buyer_model: str, deal_text: str) -> bool:
    wanted = model_tokens(buyer_model)
    if not wanted:
        return False
    hay = normalize_text(deal_text)
    # Require every significant requested token. This favors precision over spam.
    return all(token in hay for token in wanted)

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
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None

def load_buyers(bank_db: Path) -> list[dict]:
    con = sqlite3.connect(bank_db)
    con.row_factory = sqlite3.Row
    try:
        if not table_exists(con, "sc_intake_submissions"):
            raise RuntimeError("sc_intake_submissions is missing from Select Carr bank")

        rows = con.execute(
            """
            SELECT
                submission_id, phone, city, model, budget, down_payment,
                payment_type, vehicle_condition, urgency, purpose,
                source, campaign_code, content_code, status, created_at, expires_at
            FROM sc_intake_submissions
            WHERE request_type='buy'
              AND status='active'
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

def load_deals(system_db: Path) -> list[dict]:
    if not system_db.exists():
        raise RuntimeError("System B database does not exist")

    con = sqlite3.connect(system_db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")

        for required in ("aa_sent_deals", "aa_listings"):
            if not table_exists(con, required):
                raise RuntimeError(f"System B table is missing: {required}")

        rows = con.execute(
            """
            SELECT
                s.source_key,
                s.source,
                s.source_ad_id,
                s.price AS deal_price,
                s.average_price AS reference_price,
                s.discount_percent,
                s.sent_at,
                COALESCE(l.url, '') AS url,
                COALESCE(l.title, '') AS title,
                COALESCE(l.brand, '') AS brand,
                COALESCE(l.model, '') AS model,
                COALESCE(l.trim, '') AS trim,
                l.model_year,
                COALESCE(l.condition, '') AS vehicle_condition,
                COALESCE(l.body_condition, '') AS body_condition,
                l.mileage,
                l.last_seen
            FROM aa_sent_deals AS s
            INNER JOIN aa_listings AS l
              ON l.source_key = s.source_key
            ORDER BY s.sent_at DESC, s.source_key ASC
            """
        ).fetchall()

        cutoff = now_utc() - timedelta(hours=max(1, DEAL_MAX_AGE_HOURS))
        deals = []
        for row in rows:
            item = dict(row)
            sent = parse_iso(item.get("sent_at"))
            if sent is None or sent < cutoff:
                continue
            deals.append(item)
        return deals
    finally:
        con.close()

def already_notified(bank_db: Path, buyer_id: str, deal_key: str) -> bool:
    con = sqlite3.connect(bank_db)
    try:
        con.executescript(STAGE3_SCHEMA)
        row = con.execute(
            """
            SELECT 1
            FROM sc_stage3_matches
            WHERE buyer_submission_id=? AND deal_source_key=?
            LIMIT 1
            """,
            (buyer_id, deal_key),
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
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(buyer["submission_id"]),
                str(deal["source_key"]),
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

def main() -> int:
    missing = [
        name for name, value in (
            ("SELECT_CARR_DATA_SECRET", DATA_SECRET),
            ("SELECT_CARR_STAGE3_BOT_TOKEN", BOT_TOKEN),
            ("SELECT_CARR_STAGE3_CHAT_ID", CHAT_ID),
        )
        if not value
    ]
    if missing:
        print("Missing secrets: " + ", ".join(missing), file=sys.stderr)
        return 2

    buyers_count = deals_count = matches_sent = 0

    with tempfile.TemporaryDirectory() as td:
        bank_db = Path(td) / "select_carr_banks.sqlite3"
        decrypt_bank(bank_db)

        # Ensure only our new Stage 3 table is added.
        con = sqlite3.connect(bank_db)
        try:
            con.executescript(STAGE3_SCHEMA)
            con.commit()
        finally:
            con.close()

        buyers = load_buyers(bank_db)
        deals = load_deals(SYSTEM_B_DB)
        buyers_count = len(buyers)
        deals_count = len(deals)

        for buyer in buyers:
            buyer_budget = buyer.get("budget")

            for deal in deals:
                if buyer_budget is not None and int(deal["deal_price"]) > int(buyer_budget):
                    continue

                deal_text = " ".join(
                    str(deal.get(k) or "")
                    for k in ("title", "brand", "model", "trim")
                )

                if not model_matches(str(buyer.get("model") or ""), deal_text):
                    continue

                buyer_id = str(buyer["submission_id"])
                deal_key = str(deal["source_key"])

                if already_notified(bank_db, buyer_id, deal_key):
                    continue

                # Mark only after Telegram accepted the message.
                send_telegram(match_message(buyer, deal))
                save_match(bank_db, buyer, deal)
                matches_sent += 1

        # Re-encrypt after Stage 3 schema/match memory updates.
        encrypt_bank(bank_db)

    print(json.dumps({
        "buyers_checked": buyers_count,
        "deals_checked": deals_count,
        "new_matches_sent": matches_sent,
    }, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
