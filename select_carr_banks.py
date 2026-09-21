#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

SCHEMA_VERSION = "2.0.0"
BUYER_DEFAULT_TTL_DAYS = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    if not re.fullmatch(r"09\d{9}", digits):
        raise ValueError("phone must be a valid Iranian mobile number")
    return digits


def _clean(value: object, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def _tracking_code(prefix: str, size: int = 5) -> str:
    return f"{prefix}-{secrets.token_hex(size).upper()}"


def _deal_id(source_key: str) -> str:
    digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:14].upper()
    return f"DL-{digest}"


@dataclass(frozen=True, slots=True)
class DealSnapshot:
    source_key: str
    source: str
    source_ad_id: str
    url: str
    title: str
    brand: str
    model: str
    trim: str
    model_year: int
    condition: str
    body_condition: str
    mileage: int | None
    price: int
    average_price: int
    discount_percent: float
    sample_count: int
    city: str = ""
    image_url: str = ""
    availability: str = "unknown"  # unknown|active|sold|deleted|expired
    detected_at: str = ""


@dataclass(frozen=True, slots=True)
class BuyerRequestInput:
    phone: str
    city: str
    desired_models: Sequence[str]
    min_year: int | None = None
    max_year: int | None = None
    min_budget: int | None = None
    max_budget: int | None = None
    payment_type: str = "cash"  # cash|installment
    down_payment: int | None = None
    max_monthly_payment: int | None = None
    purchase_timing: str = ""
    source: str = "unknown"
    campaign_code: str = ""
    content_code: str = ""
    consent_match: bool = True
    consent_public_buyer_story: bool = False
    expires_days: int = BUYER_DEFAULT_TTL_DAYS


SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sc_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sc_deals (
    deal_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    trim TEXT NOT NULL DEFAULT '',
    model_year INTEGER NOT NULL,
    condition TEXT NOT NULL,
    body_condition TEXT NOT NULL DEFAULT 'unknown',
    mileage INTEGER,
    city TEXT NOT NULL DEFAULT '',
    image_url TEXT NOT NULL DEFAULT '',
    current_price INTEGER NOT NULL CHECK(current_price > 0),
    current_average_price INTEGER NOT NULL CHECK(current_average_price > 0),
    current_discount_percent REAL NOT NULL,
    current_sample_count INTEGER NOT NULL DEFAULT 0,
    availability TEXT NOT NULL DEFAULT 'unknown',
    lifecycle_status TEXT NOT NULL DEFAULT 'active',
    first_detected_at TEXT NOT NULL,
    last_detected_at TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    telegram_status TEXT NOT NULL DEFAULT 'not_published',
    telegram_message_id INTEGER,
    telegram_published_at TEXT,
    instagram_story_status TEXT NOT NULL DEFAULT 'not_published',
    instagram_story_media_id TEXT,
    instagram_published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sc_deals_vehicle
ON sc_deals(brand, model, trim, model_year, lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_sc_deals_status
ON sc_deals(lifecycle_status, availability, last_detected_at);

CREATE TABLE IF NOT EXISTS sc_deal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    price INTEGER,
    average_price INTEGER,
    discount_percent REAL,
    sample_count INTEGER,
    availability TEXT,
    lifecycle_status TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(deal_id) REFERENCES sc_deals(deal_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_sc_deal_history
ON sc_deal_history(deal_id, id);

CREATE TABLE IF NOT EXISTS sc_buyers (
    buyer_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS sc_buyer_requests (
    request_id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    min_year INTEGER,
    max_year INTEGER,
    min_budget INTEGER,
    max_budget INTEGER,
    payment_type TEXT NOT NULL CHECK(payment_type IN ('cash','installment')),
    down_payment INTEGER,
    max_monthly_payment INTEGER,
    purchase_timing TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'unknown',
    campaign_code TEXT NOT NULL DEFAULT '',
    content_code TEXT NOT NULL DEFAULT '',
    tracking_code TEXT NOT NULL UNIQUE,
    consent_match INTEGER NOT NULL DEFAULT 1,
    consent_public_buyer_story INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    closed_at TEXT,
    close_reason TEXT,
    FOREIGN KEY(buyer_id) REFERENCES sc_buyers(buyer_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_sc_buyer_requests_active
ON sc_buyer_requests(status, expires_at, city, payment_type);
CREATE INDEX IF NOT EXISTS idx_sc_buyer_requests_source
ON sc_buyer_requests(source, campaign_code, content_code);

CREATE TABLE IF NOT EXISTS sc_buyer_request_models (
    request_id TEXT NOT NULL,
    model_key TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(request_id, model_key),
    FOREIGN KEY(request_id) REFERENCES sc_buyer_requests(request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sc_buyer_models
ON sc_buyer_request_models(model_key, request_id);

CREATE TABLE IF NOT EXISTS sc_buyer_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(request_id) REFERENCES sc_buyer_requests(request_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS sc_attribution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    event_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    campaign_code TEXT NOT NULL DEFAULT '',
    content_code TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(request_id) REFERENCES sc_buyer_requests(request_id) ON DELETE RESTRICT
);
"""


class SelectCarrBanks:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            con.execute(
                "INSERT INTO sc_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )

    def record_deal(self, item: DealSnapshot) -> tuple[str, str]:
        if not item.source_key.strip():
            raise ValueError("source_key is required")
        if item.price <= 0 or item.average_price <= 0:
            raise ValueError("deal price and average price must be positive")
        deal_id = _deal_id(item.source_key)
        stamp = item.detected_at or now_iso()
        with self.connect() as con:
            old = con.execute("SELECT * FROM sc_deals WHERE deal_id=?", (deal_id,)).fetchone()
            if old is None:
                con.execute(
                    """
                    INSERT INTO sc_deals(
                        deal_id,source_key,source,source_ad_id,url,title,brand,model,trim,model_year,
                        condition,body_condition,mileage,city,image_url,current_price,current_average_price,
                        current_discount_percent,current_sample_count,availability,lifecycle_status,
                        first_detected_at,last_detected_at,last_changed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        deal_id,item.source_key,item.source,item.source_ad_id,item.url,item.title,
                        item.brand,item.model,item.trim,item.model_year,item.condition,item.body_condition,
                        item.mileage,item.city,item.image_url,item.price,item.average_price,
                        item.discount_percent,item.sample_count,item.availability,"active",stamp,stamp,stamp,
                    ),
                )
                self._deal_event(con, deal_id, "detected", item, "active", stamp, {})
                return deal_id, "inserted"

            changed = any([
                int(old["current_price"]) != int(item.price),
                int(old["current_average_price"]) != int(item.average_price),
                float(old["current_discount_percent"]) != float(item.discount_percent),
                int(old["current_sample_count"]) != int(item.sample_count),
                str(old["availability"]) != item.availability,
                str(old["url"]) != item.url,
                str(old["title"]) != item.title,
                str(old["body_condition"]) != item.body_condition,
                (old["mileage"] if old["mileage"] is not None else None) != item.mileage,
            ])
            con.execute(
                """
                UPDATE sc_deals SET
                    source=?,source_ad_id=?,url=?,title=?,brand=?,model=?,trim=?,model_year=?,condition=?,
                    body_condition=?,mileage=?,city=?,image_url=?,current_price=?,current_average_price=?,
                    current_discount_percent=?,current_sample_count=?,availability=?,last_detected_at=?,
                    last_changed_at=CASE WHEN ? THEN ? ELSE last_changed_at END
                WHERE deal_id=?
                """,
                (
                    item.source,item.source_ad_id,item.url,item.title,item.brand,item.model,item.trim,
                    item.model_year,item.condition,item.body_condition,item.mileage,item.city,item.image_url,
                    item.price,item.average_price,item.discount_percent,item.sample_count,item.availability,
                    stamp,1 if changed else 0,stamp,deal_id,
                ),
            )
            if changed:
                self._deal_event(con, deal_id, "changed", item, str(old["lifecycle_status"]), stamp, {})
                return deal_id, "changed"
            return deal_id, "seen"

    def _deal_event(self, con, deal_id: str, event_type: str, item: DealSnapshot | None,
                    lifecycle_status: str, stamp: str, payload: dict) -> None:
        con.execute(
            """
            INSERT INTO sc_deal_history(
                deal_id,event_type,event_at,price,average_price,discount_percent,sample_count,
                availability,lifecycle_status,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                deal_id,event_type,stamp,
                item.price if item else None,item.average_price if item else None,
                item.discount_percent if item else None,item.sample_count if item else None,
                item.availability if item else None,lifecycle_status,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def mark_deal_lifecycle(self, deal_id: str, status: str, *, reason: str = "") -> None:
        allowed = {"active","sold","deleted","expired","paused"}
        if status not in allowed:
            raise ValueError("invalid deal lifecycle status")
        stamp = now_iso()
        with self.connect() as con:
            row = con.execute("SELECT * FROM sc_deals WHERE deal_id=?", (deal_id,)).fetchone()
            if row is None:
                raise KeyError(deal_id)
            con.execute(
                "UPDATE sc_deals SET lifecycle_status=?, last_changed_at=? WHERE deal_id=?",
                (status, stamp, deal_id),
            )
            con.execute(
                "INSERT INTO sc_deal_history(deal_id,event_type,event_at,lifecycle_status,payload_json) VALUES(?,?,?,?,?)",
                (deal_id,"lifecycle_changed",stamp,status,json.dumps({"reason": reason}, ensure_ascii=False)),
            )

    def mark_deal_published(self, deal_id: str, platform: str, external_id: str | int | None = None) -> None:
        stamp = now_iso()
        if platform not in {"telegram","instagram_story"}:
            raise ValueError("unsupported platform")
        with self.connect() as con:
            if con.execute("SELECT 1 FROM sc_deals WHERE deal_id=?", (deal_id,)).fetchone() is None:
                raise KeyError(deal_id)
            if platform == "telegram":
                con.execute(
                    "UPDATE sc_deals SET telegram_status='published',telegram_message_id=?,telegram_published_at=? WHERE deal_id=?",
                    (int(external_id) if external_id not in (None, "") else None, stamp, deal_id),
                )
            else:
                con.execute(
                    "UPDATE sc_deals SET instagram_story_status='published',instagram_story_media_id=?,instagram_published_at=? WHERE deal_id=?",
                    (_clean(external_id, 200), stamp, deal_id),
                )
            con.execute(
                "INSERT INTO sc_deal_history(deal_id,event_type,event_at,lifecycle_status,payload_json) "
                "SELECT deal_id,?,?,lifecycle_status,? FROM sc_deals WHERE deal_id=?",
                (f"published_{platform}", stamp, json.dumps({"external_id": str(external_id or "")}), deal_id),
            )

    def create_buyer_request(self, data: BuyerRequestInput) -> tuple[str, str, str]:
        phone = _normalize_phone(data.phone)
        models = [re.sub(r"\s+", " ", _clean(x, 120)).strip().lower() for x in data.desired_models]
        models = list(dict.fromkeys(x for x in models if x))
        if not models:
            raise ValueError("at least one desired model is required")
        if data.payment_type not in {"cash", "installment"}:
            raise ValueError("payment_type must be cash or installment")
        if data.expires_days < 1 or data.expires_days > 90:
            raise ValueError("expires_days must be between 1 and 90")
        if data.min_budget is not None and data.max_budget is not None and data.min_budget > data.max_budget:
            raise ValueError("min_budget cannot exceed max_budget")
        if data.min_year is not None and data.max_year is not None and data.min_year > data.max_year:
            raise ValueError("min_year cannot exceed max_year")

        stamp = now_iso()
        expiry = (datetime.now(timezone.utc) + timedelta(days=data.expires_days)).isoformat()
        with self.connect() as con:
            buyer = con.execute("SELECT * FROM sc_buyers WHERE phone=?", (phone,)).fetchone()
            if buyer is None:
                buyer_id = _tracking_code("BU", 6)
                con.execute(
                    "INSERT INTO sc_buyers(buyer_id,phone,created_at,updated_at,status) VALUES(?,?,?,?,?)",
                    (buyer_id, phone, stamp, stamp, "active"),
                )
            else:
                buyer_id = str(buyer["buyer_id"])
                con.execute("UPDATE sc_buyers SET updated_at=?,status='active' WHERE buyer_id=?", (stamp,buyer_id))

            request_id = _tracking_code("BR", 6)
            tracking_code = _tracking_code("REQ", 4)
            con.execute(
                """
                INSERT INTO sc_buyer_requests(
                    request_id,buyer_id,city,min_year,max_year,min_budget,max_budget,payment_type,
                    down_payment,max_monthly_payment,purchase_timing,source,campaign_code,content_code,
                    tracking_code,consent_match,consent_public_buyer_story,status,created_at,last_confirmed_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id,buyer_id,_clean(data.city,120),data.min_year,data.max_year,
                    data.min_budget,data.max_budget,data.payment_type,data.down_payment,
                    data.max_monthly_payment,_clean(data.purchase_timing,120),_clean(data.source,120),
                    _clean(data.campaign_code,120),_clean(data.content_code,120),tracking_code,
                    int(bool(data.consent_match)),int(bool(data.consent_public_buyer_story)),"active",
                    stamp,stamp,expiry,
                ),
            )
            for priority, model in enumerate(models):
                con.execute(
                    "INSERT INTO sc_buyer_request_models(request_id,model_key,priority) VALUES(?,?,?)",
                    (request_id,model,priority),
                )
            payload = {"models": models, "expires_days": data.expires_days}
            con.execute(
                "INSERT INTO sc_buyer_history(request_id,event_type,event_at,payload_json) VALUES(?,?,?,?)",
                (request_id,"created",stamp,json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )
            con.execute(
                "INSERT INTO sc_attribution_events(request_id,event_name,event_at,source,campaign_code,content_code,payload_json) VALUES(?,?,?,?,?,?,?)",
                (request_id,"request_created",stamp,_clean(data.source,120),_clean(data.campaign_code,120),
                 _clean(data.content_code,120),"{}"),
            )
            return request_id, tracking_code, buyer_id

    def refresh_buyer_request(self, request_id: str, days: int = BUYER_DEFAULT_TTL_DAYS) -> str:
        if days < 1 or days > 90:
            raise ValueError("days must be between 1 and 90")
        stamp = now_iso()
        expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        with self.connect() as con:
            row = con.execute("SELECT * FROM sc_buyer_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            con.execute(
                "UPDATE sc_buyer_requests SET status='active',last_confirmed_at=?,expires_at=?,closed_at=NULL,close_reason=NULL WHERE request_id=?",
                (stamp,expiry,request_id),
            )
            con.execute(
                "INSERT INTO sc_buyer_history(request_id,event_type,event_at,payload_json) VALUES(?,?,?,?)",
                (request_id,"refreshed",stamp,json.dumps({"days": days})),
            )
        return expiry

    def close_buyer_request(self, request_id: str, status: str, reason: str = "") -> None:
        allowed = {"purchased","cancelled","expired"}
        if status not in allowed:
            raise ValueError("invalid closing status")
        stamp = now_iso()
        with self.connect() as con:
            if con.execute("SELECT 1 FROM sc_buyer_requests WHERE request_id=?", (request_id,)).fetchone() is None:
                raise KeyError(request_id)
            con.execute(
                "UPDATE sc_buyer_requests SET status=?,closed_at=?,close_reason=? WHERE request_id=?",
                (status,stamp,_clean(reason,500),request_id),
            )
            con.execute(
                "INSERT INTO sc_buyer_history(request_id,event_type,event_at,payload_json) VALUES(?,?,?,?)",
                (request_id,"closed",stamp,json.dumps({"status": status,"reason": reason}, ensure_ascii=False)),
            )

    def expire_old_requests(self, at: str | None = None) -> int:
        stamp = at or now_iso()
        with self.connect() as con:
            rows = con.execute(
                "SELECT request_id FROM sc_buyer_requests WHERE status='active' AND expires_at<=?",
                (stamp,),
            ).fetchall()
            for row in rows:
                con.execute(
                    "UPDATE sc_buyer_requests SET status='expired',closed_at=?,close_reason='automatic_30_day_expiry' WHERE request_id=?",
                    (stamp,row["request_id"]),
                )
                con.execute(
                    "INSERT INTO sc_buyer_history(request_id,event_type,event_at,payload_json) VALUES(?,?,?,?)",
                    (row["request_id"],"expired",stamp,"{}"),
                )
            return len(rows)

    def get_active_buyers(self) -> list[dict]:
        self.expire_old_requests()
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT r.*, b.phone,
                       GROUP_CONCAT(m.model_key, '|||') AS models
                FROM sc_buyer_requests r
                JOIN sc_buyers b ON b.buyer_id=r.buyer_id
                JOIN sc_buyer_request_models m ON m.request_id=r.request_id
                WHERE r.status='active'
                GROUP BY r.request_id
                ORDER BY r.created_at DESC
                """
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["models"] = str(item.pop("models") or "").split("|||" )
            output.append(item)
        return output

    def attribution_summary(self) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT source,campaign_code,content_code,
                       COUNT(*) AS requests,
                       SUM(status='active') AS active,
                       SUM(status='purchased') AS purchased
                FROM sc_buyer_requests
                GROUP BY source,campaign_code,content_code
                ORDER BY requests DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]


def snapshot_from_service_deal(deal) -> DealSnapshot:
    listing = deal.listing
    return DealSnapshot(
        source_key=str(listing.source_key),
        source=str(listing.source),
        source_ad_id=str(listing.source_ad_id),
        url=str(listing.url),
        title=str(listing.title),
        brand=str(listing.brand),
        model=str(listing.model),
        trim=str(listing.trim or ""),
        model_year=int(listing.model_year),
        condition=str(listing.condition),
        body_condition=str(getattr(listing, "body_condition", "unknown") or "unknown"),
        mileage=getattr(listing, "mileage", None),
        price=int(listing.price),
        average_price=int(deal.average_price),
        discount_percent=float(deal.discount_percent),
        sample_count=int(deal.sample_count),
        detected_at=now_iso(),
    )
