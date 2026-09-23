#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS sc_deal_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    trim TEXT NOT NULL DEFAULT '',
    model_year INTEGER,
    vehicle_condition TEXT NOT NULL DEFAULT '',
    body_condition TEXT NOT NULL DEFAULT '',
    mileage INTEGER,
    deal_price INTEGER NOT NULL,
    reference_price INTEGER NOT NULL,
    discount_percent REAL NOT NULL,
    first_seen TEXT,
    last_seen TEXT,
    bridged_at TEXT NOT NULL,
    UNIQUE(source_key, sent_at)
);

CREATE INDEX IF NOT EXISTS idx_sc_deal_history_vehicle
ON sc_deal_history(brand, model, trim, model_year);

CREATE INDEX IF NOT EXISTS idx_sc_deal_history_sent
ON sc_deal_history(sent_at);

CREATE TABLE IF NOT EXISTS sc_deal_current (
    source_key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    trim TEXT NOT NULL DEFAULT '',
    model_year INTEGER,
    vehicle_condition TEXT NOT NULL DEFAULT '',
    body_condition TEXT NOT NULL DEFAULT '',
    mileage INTEGER,
    deal_price INTEGER NOT NULL,
    reference_price INTEGER NOT NULL,
    discount_percent REAL NOT NULL,
    first_seen TEXT,
    last_seen TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    bridged_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sc_deal_current_vehicle
ON sc_deal_current(brand, model, trim, model_year, status);
"""

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

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

def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None

def fetch_sent_deals(system_db: Path) -> list[dict]:
    if not system_db.exists():
        raise RuntimeError("System B database does not exist")

    con = sqlite3.connect(system_db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        if not table_exists(con, "aa_sent_deals"):
            raise RuntimeError("System B aa_sent_deals table is missing")
        if not table_exists(con, "aa_listings"):
            raise RuntimeError("System B aa_listings table is missing")

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
                l.first_seen,
                l.last_seen
            FROM aa_sent_deals AS s
            LEFT JOIN aa_listings AS l
              ON l.source_key = s.source_key
            ORDER BY s.sent_at ASC, s.source_key ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()

def bridge_into_bank(bank_db: Path, rows: list[dict]) -> dict:
    con = sqlite3.connect(bank_db)
    con.row_factory = sqlite3.Row
    inserted_history = 0
    upserted_current = 0
    skipped_no_identity = 0
    try:
        con.executescript(SCHEMA)
        stamp = now_iso()

        for r in rows:
            source_key = str(r.get("source_key") or "").strip()
            sent_at = str(r.get("sent_at") or "").strip()
            if not source_key or not sent_at:
                skipped_no_identity += 1
                continue

            values = (
                source_key,
                sent_at,
                str(r.get("source") or ""),
                str(r.get("source_ad_id") or ""),
                str(r.get("url") or ""),
                str(r.get("title") or ""),
                str(r.get("brand") or ""),
                str(r.get("model") or ""),
                str(r.get("trim") or ""),
                r.get("model_year"),
                str(r.get("vehicle_condition") or ""),
                str(r.get("body_condition") or ""),
                r.get("mileage"),
                int(r.get("deal_price") or 0),
                int(r.get("reference_price") or 0),
                float(r.get("discount_percent") or 0.0),
                r.get("first_seen"),
                r.get("last_seen"),
                stamp,
            )

            cur = con.execute(
                """
                INSERT OR IGNORE INTO sc_deal_history(
                    source_key, sent_at, source, source_ad_id,
                    url, title, brand, model, trim, model_year,
                    vehicle_condition, body_condition, mileage,
                    deal_price, reference_price, discount_percent,
                    first_seen, last_seen, bridged_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            if cur.rowcount == 1:
                inserted_history += 1

            con.execute(
                """
                INSERT INTO sc_deal_current(
                    source_key, sent_at, source, source_ad_id,
                    url, title, brand, model, trim, model_year,
                    vehicle_condition, body_condition, mileage,
                    deal_price, reference_price, discount_percent,
                    first_seen, last_seen, status, bridged_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?)
                ON CONFLICT(source_key) DO UPDATE SET
                    sent_at=excluded.sent_at,
                    source=excluded.source,
                    source_ad_id=excluded.source_ad_id,
                    url=excluded.url,
                    title=excluded.title,
                    brand=excluded.brand,
                    model=excluded.model,
                    trim=excluded.trim,
                    model_year=excluded.model_year,
                    vehicle_condition=excluded.vehicle_condition,
                    body_condition=excluded.body_condition,
                    mileage=excluded.mileage,
                    deal_price=excluded.deal_price,
                    reference_price=excluded.reference_price,
                    discount_percent=excluded.discount_percent,
                    first_seen=excluded.first_seen,
                    last_seen=excluded.last_seen,
                    bridged_at=excluded.bridged_at
                """,
                values,
            )
            upserted_current += 1

        con.commit()
        return {
            "system_b_sent_rows": len(rows),
            "history_inserted": inserted_history,
            "current_upserted": upserted_current,
            "skipped_no_identity": skipped_no_identity,
        }
    finally:
        con.close()

def main() -> int:
    if not DATA_SECRET:
        print("SELECT_CARR_DATA_SECRET is missing", file=sys.stderr)
        return 2

    rows = fetch_sent_deals(SYSTEM_B_DB)

    with tempfile.TemporaryDirectory() as td:
        bank_db = Path(td) / "select_carr_banks.sqlite3"
        decrypt_bank(bank_db)

        before = hashlib.sha256(bank_db.read_bytes()).hexdigest()
        report = bridge_into_bank(bank_db, rows)
        after = hashlib.sha256(bank_db.read_bytes()).hexdigest()

        if before != after:
            encrypt_bank(bank_db)

    report["bank_changed"] = before != after
    print(json.dumps(report, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
