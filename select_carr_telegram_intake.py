#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from select_carr_banks import BuyerRequestInput, SelectCarrBanks

ROOT = Path(__file__).resolve().parent
ENC_DB = Path(os.getenv("SELECT_CARR_ENCRYPTED_DB", ROOT / "private_data/select_carr_banks.sqlite3.enc"))
OFFSET_FILE = Path(os.getenv("SELECT_CARR_TELEGRAM_OFFSET_FILE", ROOT / "private_data/telegram_offset.txt"))
BOT_TOKEN = os.getenv("SELECT_CARR_INTAKE_BOT_TOKEN", "").strip()
DATA_SECRET = os.getenv("SELECT_CARR_DATA_SECRET", "").strip()

RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS sc_intake_submissions (
    submission_id TEXT PRIMARY KEY,
    request_type TEXT NOT NULL CHECK(request_type IN ('buy','sell')),
    telegram_update_id INTEGER NOT NULL UNIQUE,
    telegram_user_id INTEGER,
    phone TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    budget INTEGER,
    down_payment INTEGER,
    payment_type TEXT NOT NULL DEFAULT '',
    vehicle_condition TEXT NOT NULL DEFAULT '',
    urgency TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    mileage INTEGER,
    asking_price INTEGER,
    body_condition TEXT NOT NULL DEFAULT '',
    mechanical_status TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'telegram_intake',
    campaign_code TEXT NOT NULL DEFAULT '',
    content_code TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    promoted_request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sc_intake_active
ON sc_intake_submissions(request_type,status,expires_at);
"""

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def int_or_none(value):
    raw = re.sub(r"\D+", "", str(value or ""))
    return int(raw) if raw else None

def valid_phone(value):
    raw = re.sub(r"\D+", "", str(value or ""))
    return raw if re.fullmatch(r"09\d{9}", raw) else ""

def api(method, params=None):
    if not BOT_TOKEN:
        raise RuntimeError("SELECT_CARR_INTAKE_BOT_TOKEN is missing")
    query = urllib.parse.urlencode(params or {})
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    if query: url += "?" + query
    with urllib.request.urlopen(url, timeout=35) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError("Telegram API request failed")
    return payload["result"]

def parse_message(text):
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    if not lines or lines[0] not in {"#SCBUY","#SCSELL"}:
        return None
    out = {"request_type": "buy" if lines[0]=="#SCBUY" else "sell"}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip().lower()] = value.strip()
    if out.get("version") != "1" or not out.get("id"):
        return None
    return out

def fernet():
    if not DATA_SECRET:
        raise RuntimeError("SELECT_CARR_DATA_SECRET is missing")
    import hashlib
    key = base64.urlsafe_b64encode(
        hashlib.sha256(("select-carr-bank-v1:" + DATA_SECRET).encode("utf-8")).digest()
    )
    return Fernet(key)

def load_offset():
    try: return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    except Exception: return 0

def save_offset(value):
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(int(value)), encoding="utf-8")

def decrypt_db(tmp_path):
    ENC_DB.parent.mkdir(parents=True, exist_ok=True)
    if not ENC_DB.exists():
        return
    try:
        tmp_path.write_bytes(fernet().decrypt(ENC_DB.read_bytes()))
    except InvalidToken as exc:
        raise RuntimeError("Could not decrypt private database") from exc

def encrypt_db(tmp_path):
    ENC_DB.parent.mkdir(parents=True, exist_ok=True)
    ENC_DB.write_bytes(fernet().encrypt(tmp_path.read_bytes()))

def ensure_raw_table(db_path):
    con = sqlite3.connect(db_path)
    try:
        con.executescript(RAW_SCHEMA)
        con.commit()
    finally:
        con.close()

def insert_raw(db_path, item, update_id, user_id):
    phone = valid_phone(item.get("phone"))
    if not phone:
        raise ValueError("invalid phone")
    stamp = now_iso()
    expiry = (datetime.now(timezone.utc)+timedelta(days=30)).isoformat() if item["request_type"]=="buy" else None
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT OR IGNORE INTO sc_intake_submissions(
            submission_id,request_type,telegram_update_id,telegram_user_id,phone,city,model,
            budget,down_payment,payment_type,vehicle_condition,urgency,purpose,mileage,asking_price,
            body_condition,mechanical_status,description,source,campaign_code,content_code,status,
            created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item["id"][:100],item["request_type"],update_id,user_id,phone,item.get("city","")[:120],
                item.get("model","")[:200],int_or_none(item.get("budget")),int_or_none(item.get("down")),
                item.get("payment","")[:40],item.get("condition","")[:40],item.get("urgency","")[:80],
                item.get("purpose","")[:100],int_or_none(item.get("mileage")),int_or_none(item.get("price")),
                item.get("body","")[:300],item.get("mechanical","")[:100],item.get("description","")[:1200],
                item.get("source","telegram_intake")[:100],item.get("campaign","")[:100],item.get("content","")[:100],
                "active",stamp,expiry
            )
        )
        con.commit()
    finally:
        con.close()

def try_promote_buyer(db_path, item):
    if item["request_type"] != "buy":
        return None
    model = item.get("model","").strip()
    pay = {"نقدی":"cash","اقساطی":"installment"}.get(item.get("payment",""))
    if not model or not pay:
        return None
    banks = SelectCarrBanks(db_path)
    request_id, _, _ = banks.create_buyer_request(BuyerRequestInput(
        phone=valid_phone(item.get("phone")),
        city=item.get("city",""),
        desired_models=[model],
        max_budget=int_or_none(item.get("budget")),
        payment_type=pay,
        down_payment=int_or_none(item.get("down")),
        purchase_timing=item.get("urgency",""),
        source=item.get("source","telegram_intake"),
        campaign_code=item.get("campaign",""),
        content_code=item.get("content",""),
        consent_match=True,
        consent_public_buyer_story=False,
        expires_days=30,
    ))
    con = sqlite3.connect(db_path)
    try:
        con.execute("UPDATE sc_intake_submissions SET promoted_request_id=? WHERE submission_id=?",
                    (request_id,item["id"][:100]))
        con.commit()
    finally:
        con.close()
    return request_id

def expire_raw_buyers(db_path):
    con=sqlite3.connect(db_path)
    try:
        con.execute("""UPDATE sc_intake_submissions SET status='expired'
                       WHERE request_type='buy' AND status='active'
                       AND expires_at IS NOT NULL AND expires_at<=?""",(now_iso(),))
        con.commit()
    finally:
        con.close()

def main():
    if not BOT_TOKEN or not DATA_SECRET:
        print("Required GitHub Secrets are missing.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as td:
        db_path=Path(td)/"select_carr_banks.sqlite3"
        decrypt_db(db_path)

        banks=SelectCarrBanks(db_path)
        banks.initialize()
        ensure_raw_table(db_path)

        offset=load_offset()
        updates=api("getUpdates", {"offset":offset,"limit":100,"timeout":0,"allowed_updates":json.dumps(["message"])})
        imported=ignored=errors=0
        max_seen=offset-1

        for upd in updates:
            update_id=int(upd.get("update_id",0))
            max_seen=max(max_seen,update_id)
            msg=upd.get("message") or {}
            parsed=parse_message(msg.get("text",""))
            if not parsed:
                ignored+=1
                continue
            try:
                insert_raw(db_path,parsed,update_id,(msg.get("from") or {}).get("id"))
                try_promote_buyer(db_path,parsed)
                imported+=1
            except Exception as exc:
                errors+=1
                print(f"Import error update_id={update_id} type={type(exc).__name__}",file=sys.stderr)

        expire_raw_buyers(db_path)
        try:
            banks.expire_old_requests()
        except Exception:
            pass

        encrypt_db(db_path)
        if max_seen >= offset:
            save_offset(max_seen+1)

    print(json.dumps({"imported":imported,"ignored":ignored,"errors":errors},ensure_ascii=False))
    return 0 if errors==0 else 1

if __name__=="__main__":
    raise SystemExit(main())
