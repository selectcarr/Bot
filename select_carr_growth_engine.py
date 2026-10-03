#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import tempfile
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet, InvalidToken


ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"

TEHRAN = ZoneInfo("Asia/Tehran")

BUFFER_API = (
    os.getenv(
        "BUFFER_API_URL",
        "https://api.buffer.com",
    ).strip()
    or "https://api.buffer.com"
)

BUFFER_API_KEY = os.getenv(
    "BUFFER_API_KEY",
    "",
).strip()

TARGET_CHANNELS = (
    "select_carr",
    "select_carrr",
)

MARKET_DB = Path(
    os.getenv(
        "SELECT_CARR_MARKET_DB",
        ROOT
        / "select_carr_market_runtime"
        / "select_carr_market.sqlite3",
    )
)

ENC_BANK = Path(
    os.getenv(
        "SELECT_CARR_ENCRYPTED_DB",
        ROOT
        / "private_data"
        / "select_carr_banks.sqlite3.enc",
    )
)

DATA_SECRET = os.getenv(
    "SELECT_CARR_DATA_SECRET",
    "",
).strip()

STAGE4_PUBLISH_STATE = Path(
    os.getenv(
        "SELECT_CARR_STAGE4_PUBLISH_STATE",
        ROOT
        / "growth_inputs"
        / "stage4_publish_state.json",
    )
)

BALE_PUBLISH_STATE = Path(
    os.getenv(
        "SELECT_CARR_BALE_PUBLISH_STATE",
        ROOT
        / "growth_inputs"
        / "bale_publish_state.json",
    )
)

GROWTH_RUNTIME = Path(
    os.getenv(
        "SELECT_CARR_GROWTH_RUNTIME_DIR",
        ROOT
        / "growth_runtime",
    )
)

GROWTH_DB = (
    GROWTH_RUNTIME
    / "growth.sqlite3"
)

REPORT_PATH = (
    GROWTH_RUNTIME
    / "daily_report.txt"
)

CONTENT_PATH = (
    GROWTH_RUNTIME
    / "content_suggestion.txt"
)

DIAGNOSTICS_PATH = (
    GROWTH_RUNTIME
    / "diagnostics.json"
)

TELEGRAM_BOT_TOKEN = (
    os.getenv(
        "SELECT_CARR_GROWTH_BOT_TOKEN",
        "",
    ).strip()
    or os.getenv(
        "SELECT_CARR_STAGE3_BOT_TOKEN",
        "",
    ).strip()
)

TELEGRAM_CHAT_ID = (
    os.getenv(
        "SELECT_CARR_GROWTH_CHAT_ID",
        "",
    ).strip()
    or os.getenv(
        "SELECT_CARR_STAGE3_CHAT_ID",
        "",
    ).strip()
    or "@chanelll_vip"
)

SOURCE_CHANNEL_URL = (
    os.getenv(
        "SELECT_CARR_SOURCE_CHANNEL_URL",
        "https://t.me/s/DO_L4",
    ).strip()
    or "https://t.me/s/DO_L4"
)


SCHEMA = """
PRAGMA journal_mode=DELETE;

CREATE TABLE IF NOT EXISTS growth_events(
    event_key TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    content_code TEXT NOT NULL DEFAULT '',
    campaign_code TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_growth_events_time
ON growth_events(occurred_at, event_type);

CREATE INDEX IF NOT EXISTS idx_growth_events_content
ON growth_events(content_code, occurred_at);

CREATE TABLE IF NOT EXISTS growth_posts(
    post_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    external_link TEXT NOT NULL DEFAULT '',
    content_key TEXT NOT NULL DEFAULT '',
    content_kind TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_growth_posts_due
ON growth_posts(due_at, channel_name);

CREATE TABLE IF NOT EXISTS growth_post_metrics(
    snapshot_date TEXT NOT NULL,
    post_id TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    metrics_updated_at TEXT,
    PRIMARY KEY(
        snapshot_date,
        post_id,
        metric_type
    )
);

CREATE TABLE IF NOT EXISTS growth_channel_metrics(
    snapshot_date TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    window_label TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    metrics_updated_at TEXT,
    PRIMARY KEY(
        snapshot_date,
        channel_id,
        window_label,
        metric_type
    )
);

CREATE TABLE IF NOT EXISTS growth_model_snapshots(
    snapshot_date TEXT NOT NULL,
    group_key TEXT NOT NULL,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    trim TEXT NOT NULL,
    model_year INTEGER NOT NULL,
    median_price INTEGER NOT NULL,
    mean_price INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    source_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(
        snapshot_date,
        group_key
    )
);

CREATE INDEX IF NOT EXISTS idx_growth_model_snapshots_group
ON growth_model_snapshots(
    group_key,
    snapshot_date
);

CREATE TABLE IF NOT EXISTS growth_outcomes(
    outcome_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    value INTEGER,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_growth_outcomes_stage
ON growth_outcomes(
    stage,
    occurred_at
);

CREATE TABLE IF NOT EXISTS growth_reports(
    report_date TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    sent_at TEXT,
    telegram_message_id TEXT,
    report_text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS growth_content_suggestions(
    suggestion_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    text TEXT NOT NULL,
    sent_at TEXT,
    telegram_message_id TEXT
);

CREATE TABLE IF NOT EXISTS growth_capabilities(
    snapshot_date TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(
        snapshot_date,
        channel_id
    )
);

CREATE TABLE IF NOT EXISTS growth_meta(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


SENSITIVE_KEYS = {
    "phone",
    "phone_number",
    "telegram_user_id",
    "telegram_chat_id",
    "username",
    "name",
    "full_name",
    "first_name",
    "last_name",
}

OUTCOME_STAGES = {
    "contacted",
    "qualified",
    "negotiating",
    "closed_won",
    "closed_lost",
}


def now_utc() -> datetime:
    return datetime.now(
        timezone.utc
    )


def now_tehran() -> datetime:
    return datetime.now(
        TEHRAN
    )


def now_iso() -> str:
    return now_utc().isoformat()


def parse_iso(
    value: object,
) -> datetime | None:

    raw = str(
        value
        or ""
    ).strip()

    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(
            raw.replace(
                "Z",
                "+00:00",
            )
        )

    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=timezone.utc
        )

    return dt.astimezone(
        timezone.utc
    )


def clean(
    value: object,
    limit: int = 500,
) -> str:

    text = re.sub(
        r"\s+",
        " ",
        str(
            value
            or ""
        ),
    ).strip()

    if len(
        text
    ) <= limit:
        return text

    return (
        text[
            :max(
                1,
                limit - 1,
            )
        ].rstrip()
        + "…"
    )


def fa_to_en(
    value: object,
) -> str:

    return str(
        value
        or ""
    ).translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789",
        )
    )


def money(
    value: object,
) -> str:

    try:
        return f"{int(value):,}"

    except Exception:
        return "-"


def pct(
    value: object,
    digits: int = 1,
) -> str:

    try:
        n = float(
            value
        )

    except Exception:
        return "-"

    if abs(
        n
        - round(
            n
        )
    ) < 0.05:

        return str(
            int(
                round(
                    n
                )
            )
        )

    return (
        f"{n:.{digits}f}"
    )


def safe_json(
    value: Any,
) -> str:

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def redact_dict(
    row: dict[
        str,
        Any,
    ],
) -> dict[
    str,
    Any,
]:

    out = {}

    for key, value in row.items():

        if key.casefold() in SENSITIVE_KEYS:
            continue

        if isinstance(
            value,
            str,
        ):

            out[
                key
            ] = clean(
                value,
                500,
            )

        else:

            out[
                key
            ] = value

    return out


def content_kind(
    content_key: str,
) -> str:

    prefix = str(
        content_key
        or ""
    ).split(
        ":",
        1,
    )[0]

    return {
        "opportunity":
            "opportunity",

        "buy_request":
            "buy_request",

        "market":
            "market",

        "cars":
            "cars",
    }.get(
        prefix,
        prefix
        or "unknown",
    )


def db_connect() -> sqlite3.Connection:

    GROWTH_RUNTIME.mkdir(
        parents=True,
        exist_ok=True,
    )

    con = sqlite3.connect(
        GROWTH_DB
    )

    con.row_factory = (
        sqlite3.Row
    )

    con.executescript(
        SCHEMA
    )

    return con


def table_exists(
    con: sqlite3.Connection,
    name: str,
) -> bool:

    return (
        con.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table'
              AND name=?
            """,
            (
                name,
            ),
        ).fetchone()
        is not None
    )


def insert_event(
    con: sqlite3.Connection,
    *,
    event_key: str,
    event_type: str,
    occurred_at: str,
    source: str,
    entity_type: str,
    entity_id: str,
    platform: str = "",
    channel: str = "",
    content_code: str = "",
    campaign_code: str = "",
    payload: dict[
        str,
        Any,
    ] | None = None,
) -> bool:

    cur = con.execute(
        """
        INSERT OR IGNORE INTO growth_events(
            event_key,
            event_type,
            occurred_at,
            source,
            entity_type,
            entity_id,
            platform,
            channel,
            content_code,
            campaign_code,
            payload_json,
            imported_at
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            event_key,
            event_type,
            occurred_at,
            source,
            entity_type,
            entity_id,
            platform,
            channel,
            content_code,
            campaign_code,
            safe_json(
                payload
                or {}
            ),
            now_iso(),
        ),
    )

    return (
        cur.rowcount
        > 0
    )


def _fernet() -> Fernet:

    if not DATA_SECRET:

        raise RuntimeError(
            "SELECT_CARR_DATA_SECRET "
            "is missing"
        )

    key = (
        base64
        .urlsafe_b64encode(
            hashlib.sha256(
                (
                    "select-carr-bank-v1:"
                    + DATA_SECRET
                ).encode(
                    "utf-8"
                )
            ).digest()
        )
    )

    return Fernet(
        key
    )


def decrypt_bank(
    target: Path,
) -> None:

    if not ENC_BANK.is_file():

        raise RuntimeError(
            "Encrypted Select Carr "
            f"bank not found: {ENC_BANK}"
        )

    try:

        target.write_bytes(
            _fernet().decrypt(
                ENC_BANK.read_bytes()
            )
        )

    except InvalidToken as exc:

        raise RuntimeError(
            "Select Carr bank decrypt failed"
        ) from exc


def load_json(
    path: Path,
) -> dict[
    str,
    Any,
]:

    if not path.is_file():
        return {}

    try:

        value = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return {}

    return (
        value
        if isinstance(
            value,
            dict,
        )
        else {}
    )


def import_market_events(
    con: sqlite3.Connection,
) -> int:

    if not MARKET_DB.is_file():
        return 0

    src = sqlite3.connect(
        MARKET_DB
    )

    src.row_factory = (
        sqlite3.Row
    )

    added = 0

    try:

        if not table_exists(
            src,
            "market_deals",
        ):
            return 0

        for row in src.execute(
            """
            SELECT *
            FROM market_deals
            ORDER BY queued_at
            """
        ):

            item = dict(
                row
            )

            event_key = str(
                item.get(
                    "event_key"
                )
                or ""
            ).strip()

            if not event_key:
                continue

            occurred = str(
                item.get(
                    "sent_at"
                )
                or item.get(
                    "queued_at"
                )
                or item.get(
                    "candidate_seen_at"
                )
                or now_iso()
            )

            payload = {
                key:
                    item.get(
                        key
                    )
                for key
                in (
                    "source",
                    "brand",
                    "model",
                    "trim",
                    "model_year",
                    "condition",
                    "body_condition",
                    "mileage",
                    "candidate_price",
                    "average_price",
                    "discount_percent",
                    "sample_count",
                    "status",
                    "url",
                )
            }

            added += int(
                insert_event(
                    con,
                    event_key=(
                        f"deal:{event_key}"
                    ),
                    event_type=(
                        "deal_detected"
                    ),
                    occurred_at=(
                        occurred
                    ),
                    source=(
                        "market_engine"
                    ),
                    entity_type=(
                        "deal"
                    ),
                    entity_id=(
                        event_key
                    ),
                    payload=(
                        payload
                    ),
                )
            )

    finally:
        src.close()

    return added


def import_bank_events(
    con: sqlite3.Connection,
) -> dict[
    str,
    int,
]:

    counts = {
        "leads":
            0,

        "matches":
            0,
    }

    if not ENC_BANK.is_file():
        return counts

    with tempfile.TemporaryDirectory() as td:

        bank = (
            Path(
                td
            )
            / "bank.sqlite3"
        )

        decrypt_bank(
            bank
        )

        src = sqlite3.connect(
            bank
        )

        src.row_factory = (
            sqlite3.Row
        )

        try:

            if table_exists(
                src,
                "sc_intake_submissions",
            ):

                for row in src.execute(
                    """
                    SELECT *
                    FROM sc_intake_submissions
                    ORDER BY created_at
                    """
                ):

                    item = dict(
                        row
                    )

                    sid = str(
                        item.get(
                            "submission_id"
                        )
                        or ""
                    ).strip()

                    if not sid:
                        continue

                    payload = redact_dict(
                        item
                    )

                    counts[
                        "leads"
                    ] += int(
                        insert_event(
                            con,
                            event_key=(
                                f"lead:{sid}"
                            ),
                            event_type=(
                                "lead_created"
                            ),
                            occurred_at=str(
                                item.get(
                                    "created_at"
                                )
                                or now_iso()
                            ),
                            source=(
                                "lead_intake"
                            ),
                            entity_type=(
                                "lead"
                            ),
                            entity_id=(
                                sid
                            ),
                            content_code=str(
                                item.get(
                                    "content_code"
                                )
                                or ""
                            ),
                            campaign_code=str(
                                item.get(
                                    "campaign_code"
                                )
                                or ""
                            ),
                            payload=(
                                payload
                            ),
                        )
                    )

            if table_exists(
                src,
                "sc_stage3_matches",
            ):

                for row in src.execute(
                    """
                    SELECT *
                    FROM sc_stage3_matches
                    ORDER BY matched_at
                    """
                ):

                    item = dict(
                        row
                    )

                    buyer = str(
                        item.get(
                            "buyer_submission_id"
                        )
                        or ""
                    ).strip()

                    deal = str(
                        item.get(
                            "deal_source_key"
                        )
                        or ""
                    ).strip()

                    if (
                        not buyer
                        or not deal
                    ):
                        continue

                    payload = redact_dict(
                        item
                    )

                    counts[
                        "matches"
                    ] += int(
                        insert_event(
                            con,
                            event_key=(
                                f"match:"
                                f"{buyer}:"
                                f"{deal}"
                            ),
                            event_type=(
                                "match_created"
                            ),
                            occurred_at=str(
                                item.get(
                                    "matched_at"
                                )
                                or item.get(
                                    "notified_at"
                                )
                                or now_iso()
                            ),
                            source=(
                                "stage3"
                            ),
                            entity_type=(
                                "match"
                            ),
                            entity_id=(
                                f"{buyer}:"
                                f"{deal}"
                            ),
                            payload=(
                                payload
                            ),
                        )
                    )

        finally:
            src.close()

    return counts


def _bale_result_timestamp(
    result: Any,
) -> str | None:

    candidates: list[
        Any
    ] = []

    if isinstance(
        result,
        dict,
    ):
        candidates.append(
            result
        )

    elif isinstance(
        result,
        list,
    ):

        candidates.extend(
            x
            for x
            in result
            if isinstance(
                x,
                dict,
            )
        )

    for item in candidates:

        raw = item.get(
            "date"
        )

        try:

            return datetime.fromtimestamp(
                int(
                    raw
                ),
                tz=timezone.utc,
            ).isoformat()

        except Exception:
            continue

    return None


def import_publish_events(
    con: sqlite3.Connection,
) -> dict[
    str,
    int,
]:

    counts = {
        "instagram":
            0,

        "bale":
            0,
    }

    stage4 = load_json(
        STAGE4_PUBLISH_STATE
    )

    published = (
        stage4.get(
            "published"
        )
        if isinstance(
            stage4.get(
                "published"
            ),
            dict,
        )
        else {}
    )

    stage4_times: dict[
        str,
        str,
    ] = {}

    for token, row in published.items():

        if not isinstance(
            row,
            dict,
        ):
            continue

        channel = str(
            row.get(
                "channel"
            )
            or str(
                token
            ).split(
                "|",
                1,
            )[0]
        )

        key = str(
            row.get(
                "content_key"
            )
            or (
                str(
                    token
                ).split(
                    "|",
                    1,
                )[1]
                if "|"
                in str(
                    token
                )
                else ""
            )
        )

        if not key:
            continue

        post_id = str(
            row.get(
                "post_id"
            )
            or ""
        )

        occurred = str(
            row.get(
                "published_at"
            )
            or now_iso()
        )

        old_time = (
            stage4_times.get(
                key
            )
        )

        if (
            not old_time
            or occurred
            < old_time
        ):

            stage4_times[
                key
            ] = occurred

        counts[
            "instagram"
        ] += int(
            insert_event(
                con,
                event_key=(
                    f"instagram:"
                    f"{channel}:"
                    f"{key}"
                ),
                event_type=(
                    "content_published"
                ),
                occurred_at=(
                    occurred
                ),
                source=(
                    "stage4"
                ),
                entity_type=(
                    "content"
                ),
                entity_id=(
                    key
                ),
                platform=(
                    "instagram"
                ),
                channel=(
                    channel
                ),
                content_code=(
                    key
                ),
                payload={
                    "post_id":
                        post_id,

                    "content_kind":
                        content_kind(
                            key
                        ),
                },
            )
        )

    bale = load_json(
        BALE_PUBLISH_STATE
    )

    bale_published = (
        bale.get(
            "published"
        )
        if isinstance(
            bale.get(
                "published"
            ),
            dict,
        )
        else {}
    )

    for token, row in bale_published.items():

        key = str(
            token
        )

        if key.startswith(
            "bale|"
        ):
            key = key[
                5:
            ]

        if not key:
            continue

        result = (
            row.get(
                "result"
            )
            if isinstance(
                row,
                dict,
            )
            else None
        )

        occurred = (
            _bale_result_timestamp(
                result
            )
            or stage4_times.get(
                key
            )
            or now_iso()
        )

        counts[
            "bale"
        ] += int(
            insert_event(
                con,
                event_key=(
                    f"bale:{key}"
                ),
                event_type=(
                    "content_published"
                ),
                occurred_at=(
                    occurred
                ),
                source=(
                    "bale_publisher"
                ),
                entity_type=(
                    "content"
                ),
                entity_id=(
                    key
                ),
                platform=(
                    "bale"
                ),
                channel=(
                    "select_carr"
                ),
                content_code=(
                    key
                ),
                payload={
                    "content_kind":
                        content_kind(
                            key
                        ),
                },
            )
        )

    return counts


def snapshot_models(
    con: sqlite3.Connection,
) -> int:

    if not MARKET_DB.is_file():
        return 0

    src = sqlite3.connect(
        MARKET_DB
    )

    src.row_factory = (
        sqlite3.Row
    )

    today = (
        now_tehran()
        .date()
        .isoformat()
    )

    cutoff = (
        now_utc()
        - timedelta(
            days=20
        )
    ).isoformat()

    groups: dict[
        str,
        list[
            dict[
                str,
                Any,
            ]
        ],
    ] = defaultdict(
        list
    )

    try:

        if not table_exists(
            src,
            "market_listings",
        ):
            return 0

        rows = src.execute(
            """
            SELECT
                group_key,
                source,
                brand,
                model,
                trim,
                model_year,
                price,
                last_seen
            FROM market_listings
            WHERE price>0
              AND julianday(last_seen)>=julianday(?)
            """,
            (
                cutoff,
            ),
        ).fetchall()

        for row in rows:

            item = dict(
                row
            )

            groups[
                str(
                    item[
                        "group_key"
                    ]
                )
            ].append(
                item
            )

    finally:
        src.close()

    inserted = 0

    for group_key, items in groups.items():

        if len(
            items
        ) < 3:
            continue

        prices = [
            int(
                x[
                    "price"
                ]
            )
            for x
            in items
            if int(
                x.get(
                    "price"
                )
                or 0
            )
            > 0
        ]

        if len(
            prices
        ) < 3:
            continue

        rep = items[
            0
        ]

        cur = con.execute(
            """
            INSERT OR REPLACE INTO growth_model_snapshots(
                snapshot_date,
                group_key,
                brand,
                model,
                trim,
                model_year,
                median_price,
                mean_price,
                sample_count,
                source_count,
                created_at
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                today,
                group_key,
                str(
                    rep.get(
                        "brand"
                    )
                    or ""
                ),
                str(
                    rep.get(
                        "model"
                    )
                    or ""
                ),
                str(
                    rep.get(
                        "trim"
                    )
                    or ""
                ),
                int(
                    rep.get(
                        "model_year"
                    )
                    or 0
                ),
                int(
                    statistics.median(
                        prices
                    )
                ),
                int(
                    round(
                        statistics.mean(
                            prices
                        )
                    )
                ),
                len(
                    prices
                ),
                len(
                    {
                        str(
                            x.get(
                                "source"
                            )
                            or ""
                        )
                        for x
                        in items
                    }
                ),
                now_iso(),
            ),
        )

        inserted += int(
            cur.rowcount
            > 0
        )

    return inserted


def buffer_call(
    query: str,
) -> dict[
    str,
    Any,
]:

    if not BUFFER_API_KEY:

        raise RuntimeError(
            "BUFFER_API_KEY is missing"
        )

    req = urllib.request.Request(
        BUFFER_API,
        data=json.dumps(
            {
                "query":
                    query
            }
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type":
                "application/json",

            "Authorization":
                f"Bearer {BUFFER_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(
        req,
        timeout=40,
    ) as response:

        payload = json.loads(
            response.read()
            .decode(
                "utf-8"
            )
        )

    if payload.get(
        "errors"
    ):

        raise RuntimeError(
            json.dumps(
                payload[
                    "errors"
                ],
                ensure_ascii=False,
            )
        )

    return (
        payload.get(
            "data"
        )
        or {}
    )


def gql(
    value: object,
) -> str:

    return json.dumps(
        str(
            value
        ),
        ensure_ascii=False,
    )


def buffer_context() -> tuple[
    list[
        dict[
            str,
            Any,
        ]
    ],
    dict[
        str,
        dict[
            str,
            Any,
        ],
    ],
]:

    account = buffer_call(
        """
        query {
          account {
            organizations {
              id
              name
            }
          }
        }
        """
    )

    organizations = (
        (
            account.get(
                "account"
            )
            or {}
        )
        .get(
            "organizations"
        )
        or []
    )

    targets: dict[
        str,
        dict[
            str,
            Any,
        ],
    ] = {}

    for org in organizations:

        org_id = str(
            org.get(
                "id"
            )
            or ""
        )

        if not org_id:
            continue

        data = buffer_call(
            f"""
            query {{
              channels(
                input: {{
                  organizationId:
                    {gql(org_id)}
                }}
              ) {{
                id
                name
                displayName
                service
                isDisconnected
                isLocked
              }}
            }}
            """
        )

        for channel in (
            data.get(
                "channels"
            )
            or []
        ):

            name = str(
                channel.get(
                    "name"
                )
                or ""
            ).strip().lower()

            if name in TARGET_CHANNELS:

                item = dict(
                    channel
                )

                item[
                    "organizationId"
                ] = org_id

                item[
                    "organizationName"
                ] = str(
                    org.get(
                        "name"
                    )
                    or ""
                )

                targets[
                    name
                ] = item

    return (
        [
            dict(
                x
            )
            for x
            in organizations
        ],
        targets,
    )


def buffer_capabilities() -> dict[
    str,
    Any,
]:

    organizations, targets = (
        buffer_context()
    )

    result: dict[
        str,
        Any,
    ] = {
        "targets":
            {},

        "schema":
            {
                "query_fields":
                    [],

                "mutation_fields":
                    [],
            },
    }

    by_org: dict[
        str,
        list[
            str
        ],
    ] = defaultdict(
        list
    )

    for name, row in targets.items():

        by_org[
            str(
                row[
                    "organizationId"
                ]
            )
        ].append(
            name
        )

    for org_id, names in by_org.items():

        try:

            data = buffer_call(
                f"""
                query {{
                  configuration(
                    input: {{
                      organizationId:
                        {gql(org_id)}
                    }}
                  ) {{
                    channels {{
                      channelId
                      channelType

                      authorizationStatus {{
                        feature
                        status
                        reason
                      }}

                      engagement {{
                        engagementType

                        replying {{
                          supportsReplying
                          prefixReplyWithMention
                        }}
                      }}
                    }}
                  }}
                }}
                """
            )

        except Exception as exc:

            for name in names:

                result[
                    "targets"
                ][
                    name
                ] = {
                    "error":
                        str(
                            exc
                        )
                }

            continue

        configs = {
            str(
                x.get(
                    "channelId"
                )
            ):
                x
            for x
            in (
                (
                    data.get(
                        "configuration"
                    )
                    or {}
                )
                .get(
                    "channels"
                )
                or []
            )
        }

        for name in names:

            ch = targets[
                name
            ]

            cfg = (
                configs.get(
                    str(
                        ch.get(
                            "id"
                        )
                    )
                )
                or {}
            )

            auth = {
                str(
                    x.get(
                        "feature"
                    )
                ):
                    {
                        "status":
                            x.get(
                                "status"
                            ),

                        "reason":
                            x.get(
                                "reason"
                            ),
                    }
                for x
                in (
                    cfg.get(
                        "authorizationStatus"
                    )
                    or []
                )
            }

            engagement = {
                str(
                    x.get(
                        "engagementType"
                    )
                ):
                    {
                        "supportsReplying":
                            (
                                x.get(
                                    "replying"
                                )
                                or {}
                            ).get(
                                "supportsReplying"
                            ),

                        "prefixReplyWithMention":
                            (
                                x.get(
                                    "replying"
                                )
                                or {}
                            ).get(
                                "prefixReplyWithMention"
                            ),
                    }
                for x
                in (
                    cfg.get(
                        "engagement"
                    )
                    or []
                )
            }

            result[
                "targets"
            ][
                name
            ] = {
                "channel_id":
                    ch.get(
                        "id"
                    ),

                "channel_type":
                    cfg.get(
                        "channelType"
                    ),

                "authorization":
                    auth,

                "engagement":
                    engagement,
            }

    try:

        schema = buffer_call(
            """
            query {
              __schema {
                queryType {
                  fields {
                    name
                  }
                }

                mutationType {
                  fields {
                    name
                  }
                }
              }
            }
            """
        )

        raw = (
            schema.get(
                "__schema"
            )
            or {}
        )

        qfields = [
            str(
                x.get(
                    "name"
                )
            )
            for x
            in (
                (
                    raw.get(
                        "queryType"
                    )
                    or {}
                )
                .get(
                    "fields"
                )
                or []
            )
        ]

        mfields = [
            str(
                x.get(
                    "name"
                )
            )
            for x
            in (
                (
                    raw.get(
                        "mutationType"
                    )
                    or {}
                )
                .get(
                    "fields"
                )
                or []
            )
        ]

        wanted = re.compile(
            r"comment|engagement|mention|"
            r"direct|\bdm|message|reply",
            re.I,
        )

        result[
            "schema"
        ] = {
            "query_fields":
                [
                    x
                    for x
                    in qfields
                    if wanted.search(
                        x
                    )
                ],

            "mutation_fields":
                [
                    x
                    for x
                    in mfields
                    if wanted.search(
                        x
                    )
                ],
        }

    except Exception as exc:

        result[
            "schema"
        ] = {
            "error":
                str(
                    exc
                ),

            "query_fields":
                [],

            "mutation_fields":
                [],
        }

    result[
        "organizations"
    ] = organizations

    return result


def stage4_post_map() -> dict[
    str,
    dict[
        str,
        str,
    ],
]:

    data = load_json(
        STAGE4_PUBLISH_STATE
    )

    published = (
        data.get(
            "published"
        )
        if isinstance(
            data.get(
                "published"
            ),
            dict,
        )
        else {}
    )

    out = {}

    for token, row in published.items():

        if not isinstance(
            row,
            dict,
        ):
            continue

        post_id = str(
            row.get(
                "post_id"
            )
            or ""
        )

        if not post_id:
            continue

        key = str(
            row.get(
                "content_key"
            )
            or (
                str(
                    token
                ).split(
                    "|",
                    1,
                )[1]
                if "|"
                in str(
                    token
                )
                else ""
            )
        )

        out[
            post_id
        ] = {
            "content_key":
                key,

            "content_kind":
                content_kind(
                    key
                ),
        }

    return out


def sync_buffer_posts(
    con: sqlite3.Connection,
) -> dict[
    str,
    int,
]:

    _organizations, targets = (
        buffer_context()
    )

    if any(
        name
        not in targets
        for name
        in TARGET_CHANNELS
    ):

        missing = [
            name
            for name
            in TARGET_CHANNELS
            if name
            not in targets
        ]

        raise RuntimeError(
            "Missing Buffer channels: "
            + ", ".join(
                missing
            )
        )

    post_map = stage4_post_map()

    snapshot_date = (
        now_tehran()
        .date()
        .isoformat()
    )

    cutoff = (
        now_utc()
        - timedelta(
            days=65
        )
    )

    counts = {
        "posts":
            0,

        "metrics":
            0,

        "aggregates":
            0,
    }

    for name in TARGET_CHANNELS:

        channel = targets[
            name
        ]

        org_id = str(
            channel[
                "organizationId"
            ]
        )

        channel_id = str(
            channel[
                "id"
            ]
        )

        data = buffer_call(
            f"""
            query {{
              posts(
                first: 100

                input: {{
                  organizationId:
                    {gql(org_id)}

                  filter: {{
                    status: [sent]
                    channelIds: [
                      {gql(channel_id)}
                    ]
                  }}

                  sort: [
                    {{
                      field: dueAt
                      direction: desc
                    }}
                    {{
                      field: createdAt
                      direction: desc
                    }}
                  ]
                }}
              ) {{
                edges {{
                  node {{
                    id
                    text
                    createdAt
                    dueAt
                    sentAt
                    channelId
                    externalLink
                    metricsUpdatedAt

                    metrics {{
                      type
                      name
                      value
                      unit
                    }}
                  }}
                }}
              }}
            }}
            """
        )

        edges = (
            (
                data.get(
                    "posts"
                )
                or {}
            )
            .get(
                "edges"
            )
            or []
        )

        for edge in edges:

            node = (
                (
                    edge
                    or {}
                )
                .get(
                    "node"
                )
                or {}
            )

            pid = str(
                node.get(
                    "id"
                )
                or ""
            )

            if not pid:
                continue

            due = parse_iso(
                node.get(
                    "sentAt"
                )
                or node.get(
                    "dueAt"
                )
                or node.get(
                    "createdAt"
                )
            )

            if (
                due
                and due
                < cutoff
            ):
                continue

            mapped = (
                post_map.get(
                    pid
                )
                or {}
            )

            con.execute(
                """
                INSERT INTO growth_posts(
                    post_id,
                    channel_id,
                    channel_name,
                    text,
                    due_at,
                    external_link,
                    content_key,
                    content_kind,
                    first_seen,
                    last_seen
                )
                VALUES(
                    ?,?,?,?,?,?,?,?,?,?
                )
                ON CONFLICT(post_id)
                DO UPDATE SET
                    channel_id=
                        excluded.channel_id,
                    channel_name=
                        excluded.channel_name,
                    text=
                        excluded.text,
                    due_at=
                        excluded.due_at,
                    external_link=
                        excluded.external_link,
                    content_key=
                        CASE
                          WHEN excluded.content_key<>''
                          THEN excluded.content_key
                          ELSE growth_posts.content_key
                        END,
                    content_kind=
                        CASE
                          WHEN excluded.content_kind<>''
                          THEN excluded.content_kind
                          ELSE growth_posts.content_kind
                        END,
                    last_seen=
                        excluded.last_seen
                """,
                (
                    pid,
                    channel_id,
                    name,
                    clean(
                        node.get(
                            "text"
                        ),
                        2000,
                    ),
                    str(
                        node.get(
                            "sentAt"
                        )
                        or node.get(
                            "dueAt"
                        )
                        or node.get(
                            "createdAt"
                        )
                        or ""
                    ),
                    str(
                        node.get(
                            "externalLink"
                        )
                        or ""
                    ),
                    str(
                        mapped.get(
                            "content_key"
                        )
                        or ""
                    ),
                    str(
                        mapped.get(
                            "content_kind"
                        )
                        or ""
                    ),
                    now_iso(),
                    now_iso(),
                ),
            )

            counts[
                "posts"
            ] += 1

            event_entity = str(
                mapped.get(
                    "content_key"
                )
                or pid
            )

            event_code = str(
                mapped.get(
                    "content_key"
                )
                or f"buffer:{pid}"
            )

            insert_event(
                con,
                event_key=(
                    f"buffer_post:{pid}"
                ),
                event_type=(
                    "content_published_external"
                ),
                occurred_at=str(
                    node.get(
                        "sentAt"
                    )
                    or node.get(
                        "dueAt"
                    )
                    or node.get(
                        "createdAt"
                    )
                    or now_iso()
                ),
                source=(
                    "buffer"
                ),
                entity_type=(
                    "content"
                ),
                entity_id=(
                    event_entity
                ),
                platform=(
                    "instagram"
                ),
                channel=(
                    name
                ),
                content_code=(
                    event_code
                ),
                payload={
                    "post_id":
                        pid,

                    "external_link":
                        str(
                            node.get(
                                "externalLink"
                            )
                            or ""
                        ),

                    "content_kind":
                        str(
                            mapped.get(
                                "content_kind"
                            )
                            or "manual"
                        ),
                },
            )

            for metric in (
                node.get(
                    "metrics"
                )
                or []
            ):

                mtype = str(
                    metric.get(
                        "type"
                    )
                    or ""
                )

                if not mtype:
                    continue

                con.execute(
                    """
                    INSERT OR REPLACE
                    INTO growth_post_metrics(
                        snapshot_date,
                        post_id,
                        metric_type,
                        metric_name,
                        value,
                        unit,
                        metrics_updated_at
                    )
                    VALUES(
                        ?,?,?,?,?,?,?
                    )
                    """,
                    (
                        snapshot_date,
                        pid,
                        mtype,
                        str(
                            metric.get(
                                "name"
                            )
                            or mtype
                        ),
                        float(
                            metric.get(
                                "value"
                            )
                            or 0.0
                        ),
                        str(
                            metric.get(
                                "unit"
                            )
                            or "count"
                        ),
                        str(
                            node.get(
                                "metricsUpdatedAt"
                            )
                            or ""
                        ),
                    ),
                )

                counts[
                    "metrics"
                ] += 1

        windows = {
            "1d":
                (
                    now_utc()
                    - timedelta(
                        days=1
                    ),
                    now_utc(),
                ),

            "7d":
                (
                    now_utc()
                    - timedelta(
                        days=7
                    ),
                    now_utc(),
                ),

            "30d":
                (
                    now_utc()
                    - timedelta(
                        days=30
                    ),
                    now_utc(),
                ),

            "prev30d":
                (
                    now_utc()
                    - timedelta(
                        days=60
                    ),
                    now_utc()
                    - timedelta(
                        days=30
                    ),
                ),
        }

        for label, (
            start,
            end,
        ) in windows.items():

            metrics_data = buffer_call(
                f"""
                query {{
                  aggregatedPostMetrics(
                    input: {{
                      organizationId:
                        {gql(org_id)}

                      startDateTime:
                        {gql(start.isoformat())}

                      endDateTime:
                        {gql(end.isoformat())}

                      channelIds: [
                        {gql(channel_id)}
                      ]
                    }}
                  ) {{
                    metrics {{
                      type
                      name
                      value
                      unit
                    }}

                    metricsUpdatedAt
                  }}
                }}
                """
            )

            aggregate = (
                metrics_data.get(
                    "aggregatedPostMetrics"
                )
                or {}
            )

            for metric in (
                aggregate.get(
                    "metrics"
                )
                or []
            ):

                mtype = str(
                    metric.get(
                        "type"
                    )
                    or ""
                )

                if not mtype:
                    continue

                con.execute(
                    """
                    INSERT OR REPLACE
                    INTO growth_channel_metrics(
                        snapshot_date,
                        channel_id,
                        channel_name,
                        window_label,
                        metric_type,
                        metric_name,
                        value,
                        unit,
                        metrics_updated_at
                    )
                    VALUES(
                        ?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        snapshot_date,
                        channel_id,
                        name,
                        label,
                        mtype,
                        str(
                            metric.get(
                                "name"
                            )
                            or mtype
                        ),
                        float(
                            metric.get(
                                "value"
                            )
                            or 0.0
                        ),
                        str(
                            metric.get(
                                "unit"
                            )
                            or "count"
                        ),
                        str(
                            aggregate.get(
                                "metricsUpdatedAt"
                            )
                            or ""
                        ),
                    ),
                )

                counts[
                    "aggregates"
                ] += 1

    return counts


def sync_capabilities(
    con: sqlite3.Connection,
) -> dict[
    str,
    Any,
]:

    caps = buffer_capabilities()

    today = (
        now_tehran()
        .date()
        .isoformat()
    )

    for name, payload in (
        caps.get(
            "targets"
        )
        or {}
    ).items():

        if not isinstance(
            payload,
            dict,
        ):
            continue

        con.execute(
            """
            INSERT OR REPLACE INTO growth_capabilities(
                snapshot_date,
                channel_id,
                channel_name,
                payload_json,
                created_at
            )
            VALUES(
                ?,?,?,?,?
            )
            """,
            (
                today,
                str(
                    payload.get(
                        "channel_id"
                    )
                    or name
                ),
                name,
                safe_json(
                    payload
                ),
                now_iso(),
            ),
        )

    con.execute(
        """
        INSERT OR REPLACE
        INTO growth_meta(
            key,
            value
        )
        VALUES(
            'buffer_schema_probe',
            ?
        )
        """,
        (
            safe_json(
                caps.get(
                    "schema"
                )
                or {}
            ),
        ),
    )

    return caps


def sync_all() -> dict[
    str,
    Any,
]:

    result: dict[
        str,
        Any,
    ] = {
        "version":
            VERSION,

        "at":
            now_iso(),

        "warnings":
            [],
    }

    with db_connect() as con:

        try:
            result[
                "market_events_added"
            ] = import_market_events(
                con
            )

        except Exception as exc:

            result[
                "warnings"
            ].append(
                f"market_events: {exc}"
            )

        try:
            result[
                "bank_events_added"
            ] = import_bank_events(
                con
            )

        except Exception as exc:

            result[
                "warnings"
            ].append(
                f"bank_events: {exc}"
            )

        try:
            result[
                "publish_events_added"
            ] = import_publish_events(
                con
            )

        except Exception as exc:

            result[
                "warnings"
            ].append(
                f"publish_events: {exc}"
            )

        try:
            result[
                "model_snapshots"
            ] = snapshot_models(
                con
            )

        except Exception as exc:

            result[
                "warnings"
            ].append(
                f"model_snapshots: {exc}"
            )

        try:
            result[
                "buffer"
            ] = sync_buffer_posts(
                con
            )

        except Exception as exc:

            result[
                "warnings"
            ].append(
                f"buffer_metrics: {exc}"
            )

        try:

            caps = sync_capabilities(
                con
            )

            result[
                "buffer_capabilities"
            ] = (
                caps.get(
                    "targets"
                )
                or {}
            )

            result[
                "buffer_schema"
            ] = (
                caps.get(
                    "schema"
                )
                or {}
            )

        except Exception as exc:

            result[
                "warnings"
            ].append(
                f"buffer_capabilities: {exc}"
            )

        con.commit()

    GROWTH_RUNTIME.mkdir(
        parents=True,
        exist_ok=True,
    )

    DIAGNOSTICS_PATH.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
        )
    )

    return result


def tehran_range(
    day: date,
) -> tuple[
    datetime,
    datetime,
]:

    start_local = datetime.combine(
        day,
        time.min,
        tzinfo=TEHRAN,
    )

    end_local = (
        start_local
        + timedelta(
            days=1
        )
    )

    return (
        start_local.astimezone(
            timezone.utc
        ),
        end_local.astimezone(
            timezone.utc
        ),
    )


def events_between(
    con: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    return [
        dict(
            r
        )
        for r
        in con.execute(
            """
            SELECT *
            FROM growth_events
            WHERE
                julianday(occurred_at)
                    >= julianday(?)
                AND
                julianday(occurred_at)
                    < julianday(?)
            ORDER BY occurred_at
            """,
            (
                start.isoformat(),
                end.isoformat(),
            ),
        )
    ]


def payload(
    row: dict[
        str,
        Any,
    ],
) -> dict[
    str,
    Any,
]:

    try:

        value = json.loads(
            str(
                row.get(
                    "payload_json"
                )
                or "{}"
            )
        )

        return (
            value
            if isinstance(
                value,
                dict,
            )
            else {}
        )

    except Exception:
        return {}


def lead_stats(
    con: sqlite3.Connection,
    start: datetime,
    end: datetime,
) -> dict[
    str,
    Any,
]:

    rows = [
        x
        for x
        in events_between(
            con,
            start,
            end,
        )
        if x.get(
            "event_type"
        )
        == "lead_created"
    ]

    by_type = Counter()
    models = Counter()
    attributed = 0
    content_codes = Counter()

    for row in rows:

        p = payload(
            row
        )

        request_type = str(
            p.get(
                "request_type"
            )
            or "unknown"
        )

        by_type[
            request_type
        ] += 1

        model = clean(
            p.get(
                "model"
            ),
            80,
        )

        if model:
            models[
                model
            ] += 1

        code = str(
            row.get(
                "content_code"
            )
            or ""
        ).strip()

        if code:

            attributed += 1

            content_codes[
                code
            ] += 1

    return {
        "total":
            len(
                rows
            ),

        "by_type":
            dict(
                by_type
            ),

        "models":
            models,

        "attributed":
            attributed,

        "content_codes":
            content_codes,
    }


def count_event(
    con: sqlite3.Connection,
    event_type: str,
    start: datetime,
    end: datetime,
) -> int:

    return int(
        con.execute(
            """
            SELECT COUNT(*)
            FROM growth_events
            WHERE
                event_type=?
                AND
                julianday(occurred_at)
                    >= julianday(?)
                AND
                julianday(occurred_at)
                    < julianday(?)
            """,
            (
                event_type,
                start.isoformat(),
                end.isoformat(),
            ),
        ).fetchone()[
            0
        ]
    )


def distinct_content_count(
    con: sqlite3.Connection,
    start: datetime,
    end: datetime,
    platform: str,
) -> int:

    return int(
        con.execute(
            """
            SELECT
                COUNT(
                    DISTINCT entity_id
                )
            FROM growth_events
            WHERE
                event_type IN (
                    'content_published',
                    'content_published_external'
                )
                AND platform=?
                AND
                julianday(occurred_at)
                    >= julianday(?)
                AND
                julianday(occurred_at)
                    < julianday(?)
            """,
            (
                platform,
                start.isoformat(),
                end.isoformat(),
            ),
        ).fetchone()[
            0
        ]
    )


def latest_channel_metrics(
    con: sqlite3.Connection,
    channel: str,
    label: str,
) -> dict[
    str,
    float,
]:

    row = con.execute(
        """
        SELECT MAX(snapshot_date)
        FROM growth_channel_metrics
        WHERE
            channel_name=?
            AND window_label=?
        """,
        (
            channel,
            label,
        ),
    ).fetchone()

    snap = (
        str(
            row[
                0
            ]
            or ""
        )
        if row
        else ""
    )

    if not snap:
        return {}

    return {
        str(
            r[
                "metric_type"
            ]
        ):
            float(
                r[
                    "value"
                ]
            )
        for r
        in con.execute(
            """
            SELECT
                metric_type,
                value
            FROM growth_channel_metrics
            WHERE
                snapshot_date=?
                AND channel_name=?
                AND window_label=?
            """,
            (
                snap,
                channel,
                label,
            ),
        )
    }


def change_percent(
    current: float,
    previous: float,
) -> str:

    if previous == 0:
        return "—"

    return (
        f"{(current - previous) / abs(previous) * 100:+.0f}٪"
    )


def top_posts(
    con: sqlite3.Connection,
    days: int = 30,
    limit: int = 3,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    snapshot = con.execute(
        """
        SELECT MAX(snapshot_date)
        FROM growth_post_metrics
        """
    ).fetchone()

    snap = (
        str(
            snapshot[
                0
            ]
            or ""
        )
        if snapshot
        else ""
    )

    if not snap:
        return []

    cutoff = (
        now_utc()
        - timedelta(
            days=days
        )
    ).isoformat()

    posts = [
        dict(
            r
        )
        for r
        in con.execute(
            """
            SELECT *
            FROM growth_posts
            WHERE
                due_at IS NULL
                OR
                julianday(due_at)
                    >= julianday(?)
            """,
            (
                cutoff,
            ),
        )
    ]

    results = []

    for post in posts:

        metrics = {
            str(
                r[
                    "metric_type"
                ]
            ):
                float(
                    r[
                        "value"
                    ]
                )
            for r
            in con.execute(
                """
                SELECT
                    metric_type,
                    value
                FROM growth_post_metrics
                WHERE
                    snapshot_date=?
                    AND post_id=?
                """,
                (
                    snap,
                    post[
                        "post_id"
                    ],
                ),
            )
        }

        if not metrics:
            continue

        score = (
            metrics.get(
                "shares",
                0,
            )
            * 5
            + metrics.get(
                "saves",
                0,
            )
            * 5
            + metrics.get(
                "comments",
                0,
            )
            * 3
            + metrics.get(
                "follows",
                0,
            )
            * 10
            + metrics.get(
                "reactions",
                0,
            )
            + metrics.get(
                "views",
                0,
            )
            * 0.01
            + metrics.get(
                "engagementRate",
                0,
            )
            * 10
        )

        results.append(
            {
                **post,
                "metrics":
                    metrics,

                "score":
                    score,
            }
        )

    results.sort(
        key=lambda x:
            x[
                "score"
            ],
        reverse=True,
    )

    return results[
        :limit
    ]


def latest_capabilities(
    con: sqlite3.Connection,
) -> dict[
    str,
    Any,
]:

    snap = con.execute(
        """
        SELECT MAX(snapshot_date)
        FROM growth_capabilities
        """
    ).fetchone()

    day = (
        str(
            snap[
                0
            ]
            or ""
        )
        if snap
        else ""
    )

    out = {}

    if day:

        for row in con.execute(
            """
            SELECT *
            FROM growth_capabilities
            WHERE snapshot_date=?
            """,
            (
                day,
            ),
        ):

            try:

                out[
                    str(
                        row[
                            "channel_name"
                        ]
                    )
                ] = json.loads(
                    str(
                        row[
                            "payload_json"
                        ]
                    )
                )

            except Exception:
                pass

    meta = con.execute(
        """
        SELECT value
        FROM growth_meta
        WHERE key='buffer_schema_probe'
        """
    ).fetchone()

    schema = {}

    if meta:

        try:

            schema = json.loads(
                str(
                    meta[
                        0
                    ]
                )
            )

        except Exception:
            pass

    return {
        "targets":
            out,

        "schema":
            schema,
    }


def trend_candidates(
    con: sqlite3.Connection,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    latest_row = con.execute(
        """
        SELECT MAX(snapshot_date)
        FROM growth_model_snapshots
        """
    ).fetchone()

    latest = (
        str(
            latest_row[
                0
            ]
            or ""
        )
        if latest_row
        else ""
    )

    if not latest:
        return []

    latest_date = date.fromisoformat(
        latest
    )

    cutoff = (
        latest_date
        - timedelta(
            days=18
        )
    ).isoformat()

    latest_rows = [
        dict(
            r
        )
        for r
        in con.execute(
            """
            SELECT *
            FROM growth_model_snapshots
            WHERE
                snapshot_date=?
                AND sample_count>=3
            """,
            (
                latest,
            ),
        )
    ]

    out = []

    for new in latest_rows:

        old = con.execute(
            """
            SELECT *
            FROM growth_model_snapshots
            WHERE
                group_key=?
                AND snapshot_date<=?
                AND sample_count>=3
            ORDER BY
                snapshot_date DESC
            LIMIT 1
            """,
            (
                new[
                    "group_key"
                ],
                cutoff,
            ),
        ).fetchone()

        if not old:
            continue

        old = dict(
            old
        )

        old_price = int(
            old[
                "median_price"
            ]
        )

        new_price = int(
            new[
                "median_price"
            ]
        )

        if old_price <= 0:
            continue

        days = (
            date.fromisoformat(
                new[
                    "snapshot_date"
                ]
            )
            - date.fromisoformat(
                old[
                    "snapshot_date"
                ]
            )
        ).days

        change = (
            (
                new_price
                - old_price
            )
            / old_price
            * 100.0
        )

        out.append(
            {
                "group_key":
                    new[
                        "group_key"
                    ],

                "brand":
                    new[
                        "brand"
                    ],

                "model":
                    new[
                        "model"
                    ],

                "trim":
                    new[
                        "trim"
                    ],

                "model_year":
                    new[
                        "model_year"
                    ],

                "old_date":
                    old[
                        "snapshot_date"
                    ],

                "new_date":
                    new[
                        "snapshot_date"
                    ],

                "days":
                    days,

                "old_price":
                    old_price,

                "new_price":
                    new_price,

                "change_percent":
                    change,

                "old_samples":
                    old[
                        "sample_count"
                    ],

                "new_samples":
                    new[
                        "sample_count"
                    ],
            }
        )

    out.sort(
        key=lambda x:
            abs(
                float(
                    x[
                        "change_percent"
                    ]
                )
            ),
        reverse=True,
    )

    return out


def report_text() -> str:

    today = (
        now_tehran()
        .date()
    )

    start, end = tehran_range(
        today
    )

    start30 = (
        now_utc()
        - timedelta(
            days=30
        )
    )

    with db_connect() as con:

        leads = lead_stats(
            con,
            start,
            end,
        )

        leads30 = lead_stats(
            con,
            start30,
            now_utc(),
        )

        deals = count_event(
            con,
            "deal_detected",
            start,
            end,
        )

        matches = count_event(
            con,
            "match_created",
            start,
            end,
        )

        matches30 = count_event(
            con,
            "match_created",
            start30,
            now_utc(),
        )

        ig_content = (
            distinct_content_count(
                con,
                start,
                end,
                "instagram",
            )
        )

        bale_content = (
            distinct_content_count(
                con,
                start,
                end,
                "bale",
            )
        )

        outcomes = Counter(
            str(
                r[
                    "stage"
                ]
            )
            for r
            in con.execute(
                """
                SELECT stage
                FROM growth_outcomes
                WHERE
                    julianday(occurred_at)
                        >= julianday(?)
                    AND
                    julianday(occurred_at)
                        < julianday(?)
                """,
                (
                    start.isoformat(),
                    end.isoformat(),
                ),
            )
        )

        top = top_posts(
            con
        )

        caps = latest_capabilities(
            con
        )

        trends = trend_candidates(
            con
        )

        lines = [
            (
                "📊 گزارش مدیریتی "
                "Select Carr — "
                f"{today.isoformat()}"
            ),
            "",
            "1) ورودی و معامله",
            (
                "• دیل‌های ثبت‌شده امروز: "
                f"{deals}"
            ),
            (
                "• لید جدید: "
                f"{leads['total']} "
                "| خرید: "
                f"{leads['by_type'].get('buy', 0)} "
                "| فروش: "
                f"{leads['by_type'].get('sell', 0)}"
            ),
            (
                "• لید دارای کد انتساب محتوا: "
                f"{leads['attributed']} "
                f"از {leads['total']}"
            ),
            (
                "• Match جدید: "
                f"{matches}"
            ),
            (
                "• تماس: "
                f"{outcomes.get('contacted', 0)} "
                "| واجد شرایط: "
                f"{outcomes.get('qualified', 0)} "
                "| مذاکره: "
                f"{outcomes.get('negotiating', 0)}"
            ),
            (
                "• معامله موفق: "
                f"{outcomes.get('closed_won', 0)} "
                "| ناموفق: "
                f"{outcomes.get('closed_lost', 0)}"
            ),
            "",
            "2) انتشار محتوا",
            (
                "• محتوای یکتای "
                "اینستاگرام امروز: "
                f"{ig_content}"
            ),
            (
                "• محتوای یکتای "
                "بله امروز: "
                f"{bale_content}"
            ),
        ]

        total_views_30 = 0.0

        for _channel in TARGET_CHANNELS:

            total_views_30 += (
                latest_channel_metrics(
                    con,
                    _channel,
                    "30d",
                ).get(
                    "views",
                    0.0,
                )
            )

        lead_per_10k = (
            leads30[
                "total"
            ]
            / total_views_30
            * 10000.0
            if total_views_30 > 0
            else 0.0
        )

        match_rate_30 = (
            matches30
            / leads30[
                "total"
            ]
            * 100.0
            if leads30[
                "total"
            ]
            > 0
            else 0.0
        )

        lines.append(
            ""
        )

        lines.append(
            "3) قیف ۳۰ روزه"
        )

        lines.append(
            (
                "• لید: "
                f"{leads30['total']} "
                "| لید منتسب به محتوا: "
                f"{leads30['attributed']}"
            )
        )

        lines.append(
            (
                "• Match: "
                f"{matches30} "
                "| نرخ Lead→Match: "
                f"{match_rate_30:.1f}٪"
            )
        )

        if total_views_30 > 0:

            lines.append(
                (
                    "• لید به ازای هر "
                    "۱۰هزار بازدید: "
                    f"{lead_per_10k:.2f}"
                )
            )

        lines.append(
            ""
        )

        lines.append(
            (
                "4) Buffer — "
                "۳۰ روز اخیر "
                "در برابر ۳۰ روز قبل"
            )
        )

        metric_order = (
            "postCount",
            "views",
            "reach",
            "reactions",
            "comments",
            "shares",
            "saves",
            "follows",
            "engagementRate",
            "totalTimeWatched",
            "averageTimeWatched",
        )

        for channel in TARGET_CHANNELS:

            cur = latest_channel_metrics(
                con,
                channel,
                "30d",
            )

            prev = latest_channel_metrics(
                con,
                channel,
                "prev30d",
            )

            lines.append(
                f"• @{channel}"
            )

            if not cur:

                lines.append(
                    (
                        "  داده Buffer "
                        "هنوز وارد نشده."
                    )
                )

                continue

            labels = {
                "postCount":
                    "پست",

                "views":
                    "بازدید",

                "reach":
                    "ریچ",

                "reactions":
                    "واکنش",

                "comments":
                    "کامنت",

                "shares":
                    "اشتراک",

                "saves":
                    "ذخیره",

                "follows":
                    "فالو",

                "engagementRate":
                    "نرخ تعامل",

                "totalTimeWatched":
                    "زمان تماشا",

                "averageTimeWatched":
                    "میانگین تماشا",
            }

            shown = []

            for key in metric_order:

                if key not in cur:
                    continue

                value = cur[
                    key
                ]

                val_text = (
                    f"{value:,.1f}"
                    if not float(
                        value
                    ).is_integer()
                    else f"{int(value):,}"
                )

                if (
                    key
                    == "engagementRate"
                ):

                    val_text += "٪"

                delta = (
                    change_percent(
                        value,
                        prev.get(
                            key,
                            0.0,
                        ),
                    )
                    if key
                    in prev
                    else "—"
                )

                shown.append(
                    (
                        f"{labels[key]} "
                        f"{val_text} "
                        f"({delta})"
                    )
                )

            lines.append(
                "  "
                + " | ".join(
                    shown[
                        :8
                    ]
                )
            )

        lines.append(
            ""
        )

        lines.append(
            "5) محتواهای قوی ۳۰ روز اخیر"
        )

        if not top:

            lines.append(
                (
                    "• هنوز داده کافی "
                    "برای رتبه‌بندی نداریم."
                )
            )

        else:

            for index, item in enumerate(
                top,
                start=1,
            ):

                m = item[
                    "metrics"
                ]

                title = (
                    clean(
                        item.get(
                            "text"
                        )
                        or item.get(
                            "content_key"
                        )
                        or item.get(
                            "external_link"
                        ),
                        55,
                    )
                    or "بدون عنوان"
                )

                lines.append(
                    (
                        f"• {index}. "
                        f"@{item['channel_name']} — "
                        f"{title} "
                        "| بازدید "
                        f"{int(m.get('views', 0)):,} "
                        "| اشتراک "
                        f"{int(m.get('shares', 0)):,} "
                        "| ذخیره "
                        f"{int(m.get('saves', 0)):,} "
                        "| کامنت "
                        f"{int(m.get('comments', 0)):,}"
                    )
                )

        lines.append(
            ""
        )

        lines.append(
            "6) تقاضای ۳۰ روز اخیر"
        )

        if leads30[
            "models"
        ]:

            for model, count in (
                leads30[
                    "models"
                ].most_common(
                    5
                )
            ):

                lines.append(
                    (
                        f"• {model}: "
                        f"{count} درخواست"
                    )
                )

        else:

            lines.append(
                (
                    "• درخواست مدل‌دار "
                    "کافی ثبت نشده."
                )
            )

        lines.append(
            ""
        )

        lines.append(
            "7) تغییرات قیمتی مدل‌ها"
        )

        if trends:

            for item in trends[
                :3
            ]:

                car = " ".join(
                    x
                    for x
                    in (
                        item[
                            "brand"
                        ],
                        item[
                            "model"
                        ],
                        item[
                            "trim"
                        ],
                        str(
                            item[
                                "model_year"
                            ]
                        ),
                    )
                    if str(
                        x
                    ).strip()
                )

                lines.append(
                    (
                        f"• {car}: "
                        "میانه "
                        f"{money(item['old_price'])} "
                        "→ "
                        f"{money(item['new_price'])} "
                        "تومان در "
                        f"{item['days']} روز "
                        "("
                        f"{float(item['change_percent']):+.1f}٪"
                        ")"
                    )
                )

        else:

            days_row = con.execute(
                """
                SELECT
                    COUNT(
                        DISTINCT snapshot_date
                    )
                FROM growth_model_snapshots
                """
            ).fetchone()

            days_count = (
                int(
                    days_row[
                        0
                    ]
                    or 0
                )
                if days_row
                else 0
            )

            lines.append(
                (
                    "• تاریخچه روزانه "
                    "در حال ساخته‌شدن است: "
                    f"{days_count} روز ثبت شده؛ "
                    "برای تحلیل ۲۰روزه "
                    "به زمان بیشتری نیاز داریم."
                )
            )

        lines.append(
            ""
        )

        lines.append(
            (
                "8) وضعیت Buffer "
                "برای Community / DM"
            )
        )

        for channel in TARGET_CHANNELS:

            c = (
                (
                    caps.get(
                        "targets"
                    )
                    or {}
                )
                .get(
                    channel
                )
                or {}
            )

            auth = (
                c.get(
                    "authorization"
                )
                or {}
            )

            eng = (
                c.get(
                    "engagement"
                )
                or {}
            )

            comment_auth = (
                (
                    auth.get(
                        "comment"
                    )
                    or {}
                )
                .get(
                    "status",
                    "نامشخص",
                )
            )

            dm_auth = (
                (
                    auth.get(
                        "directMessage"
                    )
                    or {}
                )
                .get(
                    "status",
                    "نامشخص",
                )
            )

            reply = (
                (
                    eng.get(
                        "comment"
                    )
                    or {}
                )
                .get(
                    "supportsReplying"
                )
            )

            lines.append(
                (
                    f"• @{channel}: "
                    f"کامنت={comment_auth}, "
                    f"Reply={reply}, "
                    f"DM={dm_auth}"
                )
            )

        schema = (
            caps.get(
                "schema"
            )
            or {}
        )

        qf = (
            schema.get(
                "query_fields"
            )
            or []
        )

        mf = (
            schema.get(
                "mutation_fields"
            )
            or []
        )

        lines.append(
            (
                "• ریشه‌های عمومی مرتبط "
                "در API: Query="
                f"{','.join(qf) or 'ندارد'} "
                "| Mutation="
                f"{','.join(mf) or 'ندارد'}"
            )
        )

        lines.append(
            ""
        )

        lines.append(
            (
                "یادداشت: آمار Buffer "
                "معمولاً روزانه تازه می‌شود "
                "و ممکن است تا حدود "
                "۲۴ ساعت تأخیر داشته باشد."
            )
        )

        return "\n".join(
            lines
        )


def telegram_call(
    method: str,
    body: dict[
        str,
        Any,
    ],
) -> dict[
    str,
    Any,
]:

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "Telegram bot token is missing"
        )

    request = urllib.request.Request(
        (
            "https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/"
            f"{method}"
        ),
        data=json.dumps(
            body,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        ),
        headers={
            "Content-Type":
                "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=35,
    ) as response:

        payload = json.loads(
            response
            .read()
            .decode(
                "utf-8"
            )
        )

    if payload.get(
        "ok"
    ) is not True:

        raise RuntimeError(
            "Telegram API error: "
            + json.dumps(
                payload,
                ensure_ascii=False,
            )
        )

    return payload


def send_telegram(
    text: str,
) -> str:

    if len(
        text
    ) > 4000:

        text = (
            text[
                :3990
            ]
            + "…"
        )

    result = telegram_call(
        "sendMessage",
        {
            "chat_id":
                TELEGRAM_CHAT_ID,

            "text":
                text,

            "disable_web_page_preview":
                True,
        },
    )

    return str(
        (
            result.get(
                "result"
            )
            or {}
        ).get(
            "message_id"
        )
        or ""
    )


def run_report(
    send: bool,
) -> int:

    sync_all()

    text = report_text()

    GROWTH_RUNTIME.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_PATH.write_text(
        text,
        encoding="utf-8",
    )

    report_day = (
        now_tehran()
        .date()
        .isoformat()
    )

    with db_connect() as con:

        existing = con.execute(
            """
            SELECT sent_at
            FROM growth_reports
            WHERE report_date=?
            """,
            (
                report_day,
            ),
        ).fetchone()

        if (
            send
            and existing
            and existing[
                0
            ]
        ):

            print(
                json.dumps(
                    {
                        "status":
                            "duplicate_report_skipped",

                        "date":
                            report_day,
                    },
                    ensure_ascii=False,
                )
            )

            return 0

        message_id = ""
        sent_at = None

        if send:

            message_id = (
                send_telegram(
                    text
                )
            )

            sent_at = now_iso()

        con.execute(
            """
            INSERT INTO growth_reports(
                report_date,
                generated_at,
                sent_at,
                telegram_message_id,
                report_text
            )
            VALUES(
                ?,?,?,?,?
            )
            ON CONFLICT(report_date)
            DO UPDATE SET
                generated_at=
                    excluded.generated_at,

                sent_at=
                    COALESCE(
                        excluded.sent_at,
                        growth_reports.sent_at
                    ),

                telegram_message_id=
                    CASE
                      WHEN excluded.telegram_message_id<>''
                      THEN excluded.telegram_message_id
                      ELSE growth_reports.telegram_message_id
                    END,

                report_text=
                    excluded.report_text
            """,
            (
                report_day,
                now_iso(),
                sent_at,
                message_id,
                text,
            ),
        )

        con.commit()

    print(
        text
    )

    print(
        json.dumps(
            {
                "status":
                    (
                        "report_sent"
                        if send
                        else "report_preview"
                    ),

                "date":
                    report_day,
            },
            ensure_ascii=False,
        )
    )

    return 0


def active_bank_rows() -> tuple[
    list[
        dict[
            str,
            Any,
        ]
    ],
    list[
        dict[
            str,
            Any,
        ]
    ],
]:

    buyers: list[
        dict[
            str,
            Any,
        ]
    ] = []

    matches: list[
        dict[
            str,
            Any,
        ]
    ] = []

    if not ENC_BANK.is_file():

        return (
            buyers,
            matches,
        )

    with tempfile.TemporaryDirectory() as td:

        bank = (
            Path(
                td
            )
            / "bank.sqlite3"
        )

        decrypt_bank(
            bank
        )

        con = sqlite3.connect(
            bank
        )

        con.row_factory = (
            sqlite3.Row
        )

        try:

            if table_exists(
                con,
                "sc_intake_submissions",
            ):

                for row in con.execute(
                    """
                    SELECT *
                    FROM sc_intake_submissions
                    WHERE status='active'
                    ORDER BY created_at DESC
                    """
                ):

                    item = redact_dict(
                        dict(
                            row
                        )
                    )

                    expiry = parse_iso(
                        item.get(
                            "expires_at"
                        )
                    )

                    if (
                        expiry
                        and expiry
                        <= now_utc()
                    ):
                        continue

                    buyers.append(
                        item
                    )

            if table_exists(
                con,
                "sc_stage3_matches",
            ):

                for row in con.execute(
                    """
                    SELECT *
                    FROM sc_stage3_matches
                    ORDER BY notified_at DESC
                    LIMIT 100
                    """
                ):

                    matches.append(
                        redact_dict(
                            dict(
                                row
                            )
                        )
                    )

        finally:
            con.close()

    return (
        buyers,
        matches,
    )


def recent_deals(
    limit: int = 50,
) -> list[
    dict[
        str,
        Any,
    ]
]:

    if not MARKET_DB.is_file():
        return []

    con = sqlite3.connect(
        MARKET_DB
    )

    con.row_factory = (
        sqlite3.Row
    )

    try:

        if not table_exists(
            con,
            "market_deals",
        ):
            return []

        return [
            dict(
                r
            )
            for r
            in con.execute(
                """
                SELECT *,
                    COALESCE(
                        sent_at,
                        queued_at,
                        candidate_seen_at
                    ) AS effective_at
                FROM market_deals
                WHERE status IN(
                    'sent',
                    'suppressed_system_b'
                )
                ORDER BY
                    julianday(
                        COALESCE(
                            sent_at,
                            queued_at,
                            candidate_seen_at
                        )
                    ) DESC
                LIMIT ?
                """,
                (
                    limit,
                ),
            )
        ]

    finally:
        con.close()


def market_rates_today() -> dict[
    str,
    Any,
] | None:

    req = urllib.request.Request(
        SOURCE_CHANNEL_URL,
        headers={
            "User-Agent":
                "Mozilla/5.0",

            "Accept-Language":
                "fa-IR,fa;q=0.9,en;q=0.5",
        },
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=35,
        ) as response:

            html = (
                response.read()
                .decode(
                    "utf-8",
                    errors="ignore",
                )
            )

    except Exception:
        return None

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    today = (
        now_tehran()
        .date()
    )

    posts = []

    for node in soup.select(
        "div.tgme_widget_message[data-post]"
    ):

        body = node.select_one(
            ".tgme_widget_message_text"
        )

        tm = node.select_one(
            "time[datetime]"
        )

        if (
            body is None
            or tm is None
        ):
            continue

        dt = parse_iso(
            tm.get(
                "datetime"
            )
        )

        if (
            not dt
            or dt.astimezone(
                TEHRAN
            ).date()
            != today
        ):
            continue

        for br in body.find_all(
            "br"
        ):
            br.replace_with(
                "\n"
            )

        text = fa_to_en(
            body.get_text(
                "",
                strip=False,
            )
        )

        posts.append(
            (
                dt,
                text,
            )
        )

    posts.sort(
        key=lambda x:
            x[
                0
            ],
        reverse=True,
    )

    patterns = {
        "usd":
            r"دلار\s*[:：]\s*([0-9][0-9,.]*)",

        "gold18":
            (
                r"گرم\s*18\s*عیار"
                r"(?:\s*\([^)]*\))?"
                r"\s*[:：]\s*"
                r"([0-9][0-9,.]*)"
            ),

        "coin":
            (
                r"سکه\s*"
                r"(?:جدید|امامی)"
                r"\s*[:：]\s*"
                r"([0-9][0-9,.]*)"
            ),

        "ounce":
            r"اونس\s*[:：]\s*([0-9][0-9,.]*)",
    }

    for dt, text in posts:

        values = {}

        for key, pattern in patterns.items():

            match = re.search(
                pattern,
                text,
                re.I,
            )

            if match:

                token = (
                    match.group(
                        1
                    )
                    .replace(
                        ",",
                        "",
                    )
                )

                try:

                    values[
                        key
                    ] = int(
                        float(
                            token
                        )
                    )

                except ValueError:
                    pass

        if all(
            key
            in values
            for key
            in patterns
        ):

            values[
                "published_at"
            ] = dt.isoformat()

            return values

    return None


def vehicle_title(
    row: dict[
        str,
        Any,
    ],
) -> str:

    parts = [
        clean(
            row.get(
                k
            ),
            40,
        )
        for k
        in (
            "brand",
            "model",
            "trim",
        )
    ]

    if row.get(
        "model_year"
    ):

        parts.append(
            str(
                row[
                    "model_year"
                ]
            )
        )

    return (
        " ".join(
            x
            for x
            in parts
            if x
        )
        or "خودرو"
    )


def make_content_candidates() -> list[
    dict[
        str,
        Any,
    ]
]:

    buyers, matches = (
        active_bank_rows()
    )

    deals = recent_deals()

    rates = (
        market_rates_today()
    )

    candidates: list[
        dict[
            str,
            Any,
        ]
    ] = []

    if matches:

        m = matches[
            0
        ]

        at = (
            parse_iso(
                m.get(
                    "notified_at"
                )
                or m.get(
                    "matched_at"
                )
            )
            or now_utc()
        )

        if (
            at
            >= now_utc()
            - timedelta(
                days=7
            )
        ):

            candidates.append(
                {
                    "key":
                        (
                            "match:"
                            f"{m.get('buyer_submission_id')}:"
                            f"{m.get('deal_source_key')}"
                        ),

                    "kind":
                        "match",

                    "score":
                        (
                            1000
                            + float(
                                m.get(
                                    "discount_percent"
                                )
                                or 0
                            )
                        ),

                    "payload":
                        m,
                }
            )

    active_buy = [
        x
        for x
        in buyers
        if str(
            x.get(
                "request_type"
            )
            or ""
        )
        == "buy"
    ]

    model_counts = Counter(
        clean(
            x.get(
                "model"
            ),
            80,
        )
        for x
        in active_buy
        if clean(
            x.get(
                "model"
            ),
            80,
        )
    )

    if model_counts:

        model, count = (
            model_counts
            .most_common(
                1
            )[
                0
            ]
        )

        candidates.append(
            {
                "key":
                    (
                        "buyer-demand:"
                        f"{now_tehran().date()}:"
                        f"{hashlib.sha1(model.encode()).hexdigest()[:10]}"
                    ),

                "kind":
                    "buyer_demand",

                "score":
                    (
                        850
                        + count
                        * 15
                    ),

                "payload":
                    {
                        "model":
                            model,

                        "count":
                            count,
                    },
            }
        )

    fresh_deals = []

    for d in deals:

        at = parse_iso(
            d.get(
                "effective_at"
            )
        )

        if (
            at
            and at
            >= now_utc()
            - timedelta(
                days=3
            )
        ):

            fresh_deals.append(
                d
            )

    if fresh_deals:

        best = max(
            fresh_deals,
            key=lambda x:
                (
                    float(
                        x.get(
                            "discount_percent"
                        )
                        or 0
                    ),
                    int(
                        x.get(
                            "sample_count"
                        )
                        or 0
                    ),
                ),
        )

        candidates.append(
            {
                "key":
                    (
                        "deal:"
                        f"{best.get('event_key') or best.get('source_key')}"
                    ),

                "kind":
                    "deal",

                "score":
                    (
                        800
                        + float(
                            best.get(
                                "discount_percent"
                            )
                            or 0
                        )
                        * 5
                    ),

                "payload":
                    best,
            }
        )

    with db_connect() as con:

        trends = trend_candidates(
            con
        )

        if trends:

            t = trends[
                0
            ]

            candidates.append(
                {
                    "key":
                        (
                            "trend:"
                            f"{t['group_key']}:"
                            f"{t['old_date']}:"
                            f"{t['new_date']}"
                        ),

                    "kind":
                        "trend20",

                    "score":
                        (
                            780
                            + abs(
                                float(
                                    t[
                                        "change_percent"
                                    ]
                                )
                            )
                            * 5
                        ),

                    "payload":
                        t,
                }
            )

        start14 = (
            now_utc()
            - timedelta(
                days=14
            )
        )

        stats14 = lead_stats(
            con,
            start14,
            now_utc(),
        )

        buy_n = int(
            stats14[
                "by_type"
            ].get(
                "buy",
                0,
            )
        )

        sell_n = int(
            stats14[
                "by_type"
            ].get(
                "sell",
                0,
            )
        )

        target = (
            "seller"
            if buy_n
            > sell_n
            else "buyer"
        )

        imbalance = abs(
            buy_n
            - sell_n
        )

        candidates.append(
            {
                "key":
                    (
                        "form:"
                        f"{now_tehran().date()}:"
                        f"{target}"
                    ),

                "kind":
                    "form_cta",

                "score":
                    (
                        640
                        + imbalance
                        * 5
                    ),

                "payload":
                    {
                        "target":
                            target,

                        "buy_leads":
                            buy_n,

                        "sell_leads":
                            sell_n,
                    },
            }
        )

    if rates:

        candidates.append(
            {
                "key":
                    (
                        "market:"
                        f"{now_tehran().date()}"
                    ),

                "kind":
                    "market30",

                "score":
                    700,

                "payload":
                    rates,
            }
        )

    if model_counts:

        model, count = (
            model_counts
            .most_common(
                1
            )[
                0
            ]
        )

        candidates.append(
            {
                "key":
                    (
                        "mistake:"
                        f"{now_tehran().date()}:"
                        f"{hashlib.sha1(model.encode()).hexdigest()[:10]}"
                    ),

                "kind":
                    "mistake",

                "score":
                    (
                        610
                        + count
                        * 5
                    ),

                "payload":
                    {
                        "model":
                            model,

                        "active_buyers":
                            count,
                    },
            }
        )

    return candidates


def suggestion_text(
    candidate: dict[
        str,
        Any,
    ],
) -> str:

    kind = candidate[
        "kind"
    ]

    p = candidate[
        "payload"
    ]

    reason = (
        "این موضوع بر اساس داده واقعی "
        "فعلی سیستم انتخاب شده است."
    )

    if kind == "match":

        model = (
            clean(
                p.get(
                    "buyer_model"
                ),
                80,
            )
            or "خودرو"
        )

        price = money(
            p.get(
                "deal_price"
            )
        )

        ref = money(
            p.get(
                "reference_price"
            )
        )

        discount = pct(
            p.get(
                "discount_percent"
            )
        )

        hook = (
            f"برای {model} خریدار داشتیم؛ "
            "سیستم هم یک فرصت با قیمت "
            f"{price} تومان پیدا کرد."
        )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۱۰ ثانیه: "
            f"«قیمت فرصت پیدا شده {price} تومان بود.»\n"
            "۱۰–۱۸ ثانیه: "
            f"«قیمت مرجع ثبت‌شده حدود {ref} تومان "
            f"و اختلاف حدود {discount} درصد بود.»\n"
            "۱۸–۲۸ ثانیه: "
            "«هدف سیستم فقط پیدا کردن آگهی نیست؛ "
            "درخواست واقعی خریدار را با فرصت موجود "
            "تطبیق می‌دهیم.»\n"
            "۲۸–۳۸ ثانیه: "
            "«اگر خریدار یا فروشنده‌ای، "
            "فرم لینک بیو را پر کن تا وارد چرخه "
            "تطبیق شوی.»"
        )

        overlay = (
            f"خریدار واقعی: {model}\n"
            f"فرصت موجود: {price}\n"
            f"اختلاف با مرجع: {discount}٪"
        )

        caption = (
            f"برای {model} درخواست خرید فعال داشتیم "
            f"و سیستم یک فرصت {price} تومانی پیدا کرد. "
            "اطلاعات شخصی کاربران منتشر نمی‌شود."
        )

        cover = (
            f"برای {model} خریدار داریم"
        )

        basis = (
            "Match واقعی | "
            f"قیمت {price} | "
            f"مرجع {ref} | "
            f"اختلاف {discount}٪"
        )

        goal = (
            "اعتماد + جذب خریدار و فروشنده"
        )

    elif kind == "buyer_demand":

        model = p[
            "model"
        ]

        count = int(
            p[
                "count"
            ]
        )

        hook = (
            f"اگر {model} برای فروش داری، "
            f"الان {count} درخواست خرید فعال "
            "برای این مدل در سیستم داریم."
        )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۱۰ ثانیه: "
            f"روی تصویر: «{count} درخواست فعال»\n"
            "۱۰–۲۰ ثانیه: "
            "«اطلاعات تماس خریدارها عمومی نمی‌شود؛ "
            "درخواست‌ها فقط برای تطبیق استفاده می‌شوند.»\n"
            "۲۰–۳۰ ثانیه: "
            "«اگر خودروی مشابه داری، "
            "فرم فروش لینک بیو را تکمیل کن.»"
        )

        overlay = (
            f"خریدار واقعی برای {model}\n"
            f"{count} درخواست فعال\n"
            "فروشنده‌ای؟ فرم فروش"
        )

        caption = (
            f"برای {model} در حال حاضر "
            f"{count} درخواست خرید فعال در سیستم داریم. "
            "اگر فروشنده‌ای، فرم فروش را تکمیل کن."
        )

        cover = (
            f"{count} خریدار برای {model}"
        )

        basis = (
            f"{count} درخواست خرید فعال "
            "در بانک Lead Intake"
        )

        goal = (
            "جذب فروشنده"
        )

    elif kind == "deal":

        car = vehicle_title(
            p
        )

        price = money(
            p.get(
                "candidate_price"
            )
        )

        avg = money(
            p.get(
                "average_price"
            )
        )

        discount = pct(
            p.get(
                "discount_percent"
            )
        )

        samples = int(
            p.get(
                "sample_count"
            )
            or 0
        )

        hook = (
            f"این {car} حدود {discount} درصد "
            f"پایین‌تر از میانگین {samples} "
            "نمونه مقایسه‌شده ثبت شده."
        )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۹ ثانیه: "
            f"«قیمت آگهی {price} تومان.»\n"
            "۹–۱۷ ثانیه: "
            "«میانگین نمونه‌های مقایسه‌شده حدود "
            f"{avg} تومان بوده.»\n"
            "۱۷–۲۷ ثانیه: "
            "«قیمت پایین‌تر به معنی تأیید سلامت خودرو نیست؛ "
            "کارشناسی همچنان ضروری است.»\n"
            "۲۷–۳۷ ثانیه: "
            "«برای دریافت فرصت‌های بعدی و ثبت درخواست خرید "
            "یا فروش، فرم لینک بیو را تکمیل کن.»"
        )

        overlay = (
            f"{car}\n"
            f"آگهی: {price}\n"
            f"مرجع: {avg}\n"
            f"اختلاف: {discount}٪"
        )

        caption = (
            f"{car} با قیمت {price} تومان در سیستم ثبت شد؛ "
            f"میانگین {samples} نمونه حدود {avg} تومان بود. "
            "بررسی فنی و بدنه پیش از معامله ضروری است."
        )

        cover = (
            f"{discount}٪ زیر میانگین؟"
        )

        basis = (
            f"دیل واقعی | {samples} نمونه | "
            f"{price} در برابر {avg} تومان"
        )

        goal = (
            "اعتماد + جذب خریدار"
        )

    elif kind == "trend20":

        car = " ".join(
            x
            for x
            in (
                p.get(
                    "brand"
                ),
                p.get(
                    "model"
                ),
                p.get(
                    "trim"
                ),
                str(
                    p.get(
                        "model_year"
                    )
                    or ""
                ),
            )
            if str(
                x
            ).strip()
        )

        oldp = money(
            p[
                "old_price"
            ]
        )

        newp = money(
            p[
                "new_price"
            ]
        )

        ch = float(
            p[
                "change_percent"
            ]
        )

        direction = (
            "بالا"
            if ch > 0
            else "پایین"
        )

        hook = (
            "میانه قیمت ثبت‌شده "
            f"{car} در حدود "
            f"{p['days']} روز، "
            f"{abs(ch):.1f} درصد "
            f"{direction} رفته."
        )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۱۰ ثانیه: "
            f"«ابتدای بازه: حدود {oldp} تومان.»\n"
            "۱۰–۱۷ ثانیه: "
            f"«آخرین ثبت: حدود {newp} تومان.»\n"
            "۱۷–۲۶ ثانیه: "
            "«این مقایسه از اسنپ‌شات‌های روزانه "
            "بانک چندمنبعی ماست؛ "
            f"نمونه قدیم {p['old_samples']} "
            f"و جدید {p['new_samples']} آگهی.»\n"
            "۲۶–۳۶ ثانیه: "
            "«اگر قصد خرید یا فروش همین مدل را داری، "
            "درخواستت را ثبت کن تا بازار روز بررسی شود.»"
        )

        overlay = (
            f"{car}\n"
            f"{oldp} → {newp}\n"
            f"{ch:+.1f}٪ در {p['days']} روز"
        )

        caption = (
            "تغییر میانه قیمت ثبت‌شده "
            f"{car}: {oldp} به {newp} تومان "
            f"در {p['days']} روز "
            f"({ch:+.1f}٪)."
        )

        cover = (
            f"{car} در ۲۰ روز چه کرد؟"
        )

        basis = (
            "اسنپ‌شات‌های روزانه بانک بازار | "
            f"{p['old_date']} تا {p['new_date']}"
        )

        goal = (
            "تحلیل بازار + Save/Share"
        )

    elif kind == "market30":

        hook = (
            "قیمت امروز بازار را "
            "در ۳۰ ثانیه جمع کنیم."
        )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۸ ثانیه: "
            f"«دلار: {money(p['usd'])} تومان.»\n"
            "۸–۱۳ ثانیه: "
            f"«طلای ۱۸ عیار: {money(p['gold18'])} "
            "هزار تومان.»\n"
            "۱۳–۱۸ ثانیه: "
            f"«سکه: {money(p['coin'])} هزار تومان.»\n"
            "۱۸–۲۳ ثانیه: "
            f"«اونس: {money(p['ounce'])} دلار.»\n"
            "۲۳–۳۰ ثانیه: "
            "«برای قیمت روز خودرو و فرصت‌های خرید و فروش، "
            "صفحه را دنبال کن.»"
        )

        overlay = (
            f"دلار {money(p['usd'])}\n"
            f"طلا {money(p['gold18'])}\n"
            f"سکه {money(p['coin'])}\n"
            f"اونس {money(p['ounce'])}"
        )

        caption = (
            "خلاصه قیمت امروز بازار؛ "
            "برای قیمت روز خودرو و فرصت‌های واقعی "
            "خرید و فروش همراه Select Carr باش."
        )

        cover = (
            "بازار امروز در ۳۰ ثانیه"
        )

        basis = (
            "آخرین نرخ همان روز از منبع قیمت "
            "مورد استفاده Stage 4"
        )

        goal = (
            "ریچ روزانه + عادت محتوایی"
        )

    elif kind == "mistake":

        model = p[
            "model"
        ]

        hook = (
            "یکی از اشتباه‌های رایج هنگام خرید "
            f"{model}: تصمیم گرفتن فقط با دیدن قیمت آگهی."
        )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۱۰ ثانیه: "
            "«قیمت پایین می‌تواند جذاب باشد، "
            "اما باید با نمونه‌های مشابه مقایسه شود.»\n"
            "۱۰–۲۰ ثانیه: "
            "«بعدش وضعیت بدنه، شاسی، فنی و مدارک "
            "باید جداگانه بررسی شود.»\n"
            "۲۰–۳۰ ثانیه: "
            "«پس اول قیمت را با بازار بسنج، "
            "بعد کارشناسی؛ نه برعکس.»\n"
            "۳۰–۳۸ ثانیه: "
            "«اگر دنبال این مدل هستی، فرم خرید را پر کن "
            "تا درخواستت وارد سیستم شود.»"
        )

        overlay = (
            f"خرید {model}\n"
            "اشتباه: فقط قیمت را دیدن\n"
            "مقایسه بازار + کارشناسی"
        )

        caption = (
            f"در خرید {model}، قیمت تنها یک سیگنال است. "
            "مقایسه با نمونه‌های مشابه و کارشناسی خودرو "
            "قبل از معامله ضروری است."
        )

        cover = (
            f"اشتباه خرید {model}"
        )

        basis = (
            "موضوع بر اساس وجود "
            f"{p['active_buyers']} درخواست فعال "
            f"برای {model} انتخاب شد؛ "
            "ادعای فنی اختصاصی ساخته نشده."
        )

        goal = (
            "آموزش + جذب خریدار"
        )

    else:

        target = p.get(
            "target"
        )

        if target == "seller":

            hook = (
                "خریدار در بازار داریم؛ "
                "چیزی که کم داریم فروشنده‌ای است "
                "که درخواستش را وارد سیستم کند."
            )

            cta = (
                "اگر خودرو برای فروش داری، "
                "فرم فروش را تکمیل کن."
            )

            goal = (
                "افزایش لید فروشنده"
            )

        else:

            hook = (
                "فرصت‌های بازار پیدا می‌شوند؛ "
                "اما برای تطبیق، باید بدانیم "
                "دقیقاً چه خودرویی می‌خواهی."
            )

            cta = (
                "اگر خریدار هستی، "
                "فرم خرید را تکمیل کن."
            )

            goal = (
                "افزایش لید خریدار"
            )

        script = (
            f"۰–۳ ثانیه: «{hook}»\n"
            "۳–۱۵ ثانیه: "
            "«در ۱۴ روز اخیر "
            f"{p['buy_leads']} درخواست خرید و "
            f"{p['sell_leads']} درخواست فروش ثبت شده.»\n"
            "۱۵–۲۵ ثانیه: "
            "«هرچه اطلاعات درخواست دقیق‌تر باشد، "
            "تطبیق با فرصت واقعی بازار بهتر می‌شود.»\n"
            "۲۵–۳۵ ثانیه: "
            f"«{cta}»"
        )

        overlay = (
            f"خرید: {p['buy_leads']} "
            f"| فروش: {p['sell_leads']}\n"
            f"{cta}"
        )

        caption = (
            "در ۱۴ روز اخیر "
            f"{p['buy_leads']} درخواست خرید "
            f"و {p['sell_leads']} درخواست فروش ثبت شده. "
            f"{cta}"
        )

        cover = (
            "فرم خرید یا فروش؟"
        )

        basis = (
            "تعداد لیدهای واقعی ۱۴ روز اخیر"
        )

    return (
        "🎬 پیشنهاد محتوای داده‌محور Select Carr\n\n"
        f"هدف: {goal}\n"
        f"دلیل انتخاب: {reason}\n\n"
        "🔥 هوک ۲–۳ ثانیه:\n"
        f"{hook}\n\n"
        "🎥 سناریوی ۲۰–۴۰ ثانیه:\n"
        f"{script}\n\n"
        "🖥 متن روی تصویر / پلان‌ها:\n"
        f"{overlay}\n\n"
        "📝 کپشن پیشنهادی:\n"
        f"{caption}\n\n"
        "📣 CTA:\n"
        "برای ثبت درخواست خرید یا فروش، "
        "فرم لینک بیو را تکمیل کن.\n\n"
        "🖼 پیشنهاد کاور:\n"
        f"{cover}\n\n"
        "📌 داده واقعی مبنا:\n"
        f"{basis}\n\n"
        "@select_carr\n"
        "@select_carrr"
    )


def choose_content_candidate() -> dict[
    str,
    Any,
] | None:

    candidates = (
        make_content_candidates()
    )

    if not candidates:
        return None

    with db_connect() as con:

        sent = {
            str(
                r[
                    0
                ]
            )
            for r
            in con.execute(
                """
                SELECT suggestion_key
                FROM growth_content_suggestions
                WHERE sent_at IS NOT NULL
                """
            )
        }

        last = con.execute(
            """
            SELECT kind
            FROM growth_content_suggestions
            WHERE sent_at IS NOT NULL
            ORDER BY sent_at DESC
            LIMIT 1
            """
        ).fetchone()

    available = [
        x
        for x
        in candidates
        if x[
            "key"
        ]
        not in sent
    ]

    if not available:
        return None

    last_kind = (
        str(
            last[
                0
            ]
        )
        if last
        else ""
    )

    for item in available:

        if (
            item[
                "kind"
            ]
            == last_kind
        ):

            item[
                "score"
            ] = (
                float(
                    item[
                        "score"
                    ]
                )
                - 40
            )

    return max(
        available,
        key=lambda x:
            float(
                x[
                    "score"
                ]
            ),
    )


def run_content(
    send: bool,
) -> int:

    sync_all()

    candidate = (
        choose_content_candidate()
    )

    if not candidate:

        print(
            json.dumps(
                {
                    "status":
                        "no_content_candidate"
                },
                ensure_ascii=False,
            )
        )

        return 0

    text = suggestion_text(
        candidate
    )

    GROWTH_RUNTIME.mkdir(
        parents=True,
        exist_ok=True,
    )

    CONTENT_PATH.write_text(
        text,
        encoding="utf-8",
    )

    message_id = ""
    sent_at = None

    if send:

        message_id = (
            send_telegram(
                text
            )
        )

        sent_at = (
            now_iso()
        )

    with db_connect() as con:

        con.execute(
            """
            INSERT INTO growth_content_suggestions(
                suggestion_key,
                kind,
                score,
                created_at,
                payload_json,
                text,
                sent_at,
                telegram_message_id
            )
            VALUES(
                ?,?,?,?,?,?,?,?
            )
            ON CONFLICT(suggestion_key)
            DO UPDATE SET
                score=
                    excluded.score,
                payload_json=
                    excluded.payload_json,
                text=
                    excluded.text,
                sent_at=
                    COALESCE(
                        excluded.sent_at,
                        growth_content_suggestions.sent_at
                    ),
                telegram_message_id=
                    CASE
                      WHEN excluded.telegram_message_id<>''
                      THEN excluded.telegram_message_id
                      ELSE growth_content_suggestions.telegram_message_id
                    END
            """,
            (
                candidate[
                    "key"
                ],
                candidate[
                    "kind"
                ],
                float(
                    candidate[
                        "score"
                    ]
                ),
                now_iso(),
                safe_json(
                    candidate[
                        "payload"
                    ]
                ),
                text,
                sent_at,
                message_id,
            ),
        )

        con.commit()

    print(
        text
    )

    print(
        json.dumps(
            {
                "status":
                    (
                        "content_sent"
                        if send
                        else "content_preview"
                    ),

                "kind":
                    candidate[
                        "kind"
                    ],

                "key":
                    candidate[
                        "key"
                    ],

                "score":
                    candidate[
                        "score"
                    ],
            },
            ensure_ascii=False,
        )
    )

    return 0


def record_outcome(
    entity_id: str,
    stage: str,
    value: int | None,
    note: str,
) -> int:

    if stage not in OUTCOME_STAGES:

        raise RuntimeError(
            "Invalid stage: "
            + stage
        )

    entity_id = clean(
        entity_id,
        200,
    )

    if not entity_id:

        raise RuntimeError(
            "entity_id is required"
        )

    stamp = now_iso()

    digest = hashlib.sha256(
        (
            f"{entity_id}|"
            f"{stage}|"
            f"{stamp}|"
            f"{note}"
        ).encode(
            "utf-8"
        )
    ).hexdigest()[
        :24
    ]

    with db_connect() as con:

        con.execute(
            """
            INSERT INTO growth_outcomes(
                outcome_id,
                entity_id,
                stage,
                occurred_at,
                value,
                note,
                created_at
            )
            VALUES(
                ?,?,?,?,?,?,?
            )
            """,
            (
                digest,
                entity_id,
                stage,
                stamp,
                value,
                clean(
                    note,
                    500,
                ),
                stamp,
            ),
        )

        con.commit()

    print(
        json.dumps(
            {
                "status":
                    "outcome_recorded",

                "entity_id":
                    entity_id,

                "stage":
                    stage,
            },
            ensure_ascii=False,
        )
    )

    return 0


def check() -> int:

    diagnostics: dict[
        str,
        Any,
    ] = {
        "version":
            VERSION,

        "growth_db":
            str(
                GROWTH_DB
            ),

        "market_db_exists":
            MARKET_DB.is_file(),

        "encrypted_bank_exists":
            ENC_BANK.is_file(),

        "stage4_state_exists":
            STAGE4_PUBLISH_STATE.is_file(),

        "bale_state_exists":
            BALE_PUBLISH_STATE.is_file(),

        "telegram_chat_id":
            TELEGRAM_CHAT_ID,
    }

    try:

        _organizations, targets = (
            buffer_context()
        )

        diagnostics[
            "buffer_channels"
        ] = list(
            targets.keys()
        )

        diagnostics[
            "buffer_capabilities"
        ] = (
            buffer_capabilities()
        )

    except Exception as exc:

        diagnostics[
            "buffer_error"
        ] = str(
            exc
        )

    if TELEGRAM_BOT_TOKEN:

        try:

            me = telegram_call(
                "getMe",
                {},
            )

            chat = telegram_call(
                "getChat",
                {
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                },
            )

            diagnostics[
                "telegram_bot"
            ] = (
                (
                    me.get(
                        "result"
                    )
                    or {}
                )
                .get(
                    "username"
                )
            )

            diagnostics[
                "telegram_chat"
            ] = (
                (
                    chat.get(
                        "result"
                    )
                    or {}
                )
                .get(
                    "title"
                )
            )

        except Exception as exc:

            diagnostics[
                "telegram_error"
            ] = str(
                exc
            )

    GROWTH_RUNTIME.mkdir(
        parents=True,
        exist_ok=True,
    )

    DIAGNOSTICS_PATH.write_text(
        json.dumps(
            diagnostics,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            diagnostics,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


def main() -> int:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=[
            "check",
            "sync",
            "report_preview",
            "report_send",
            "content_preview",
            "content_send",
            "outcome",
        ],
    )

    parser.add_argument(
        "--entity-id",
        default="",
    )

    parser.add_argument(
        "--stage",
        default="contacted",
        choices=sorted(
            OUTCOME_STAGES
        ),
    )

    parser.add_argument(
        "--value",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--note",
        default="",
    )

    args = parser.parse_args()

    if args.action == "check":
        return check()

    if args.action == "sync":

        sync_all()
        return 0

    if args.action == "report_preview":

        return run_report(
            False
        )

    if args.action == "report_send":

        return run_report(
            True
        )

    if args.action == "content_preview":

        return run_content(
            False
        )

    if args.action == "content_send":

        return run_content(
            True
        )

    return record_outcome(
        args.entity_id,
        args.stage,
        args.value,
        args.note,
    )


if __name__ == "__main__":

    raise SystemExit(
        main()
    )
