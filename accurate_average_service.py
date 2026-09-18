#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from collections import Counter
from types import SimpleNamespace
import universal_deal_engine as universal_engine
import logging
import os
import random
import sqlite3
import tempfile

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from logging.handlers import (
    RotatingFileHandler,
)
from pathlib import Path
from typing import (
    Iterable,
    Iterator,
)

import requests

from accurate_average_collectors import (
    BaseCollector,
    CollectorContext,
    CollectorResult,
    NormalizedVehicleListing,
    build_collectors,
    run_self_test as collectors_self_test,
)


LOGGER = logging.getLogger(
    "accurate_average"
)

SYSTEM = "[ACCURATE-SYSTEM]"

WEB_SOURCES = {
    "divar",
    "bama",
    "khodro45",
    "formula",
    "sheypoor",
    "karnameh",
    "hamrah_mechanic",
}

ALL_SOURCES = (
    "telegram",
    "divar",
    "bama",
    "khodro45",
    "formula",
    "sheypoor",
    "karnameh",
    "hamrah_mechanic",
)


def env_bool(
    name: str,
    default: bool,
) -> bool:

    raw = os.getenv(
        name
    )

    if (
        raw is None
        or not raw.strip()
    ):
        return default

    value = (
        raw.strip()
        .lower()
    )

    if value in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if value in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    raise ValueError(
        (
            f"{name} "
            "must be boolean"
        )
    )


def env_int(
    name: str,
    default: int,
    minimum: int = 0,
) -> int:

    raw = os.getenv(
        name
    )

    value = (
        default
        if (
            raw is None
            or not raw.strip()
        )
        else int(
            raw.strip()
        )
    )

    if value < minimum:
        raise ValueError(
            (
                f"{name} "
                f"must be >= "
                f"{minimum}"
            )
        )

    return value


def env_float(
    name: str,
    default: float,
    minimum: float = 0.0,
) -> float:

    raw = os.getenv(
        name
    )

    value = (
        default
        if (
            raw is None
            or not raw.strip()
        )
        else float(
            raw.strip()
        )
    )

    if value < minimum:
        raise ValueError(
            (
                f"{name} "
                f"must be >= "
                f"{minimum}"
            )
        )

    return value


def env_csv(
    name: str,
    default: Iterable[
        str
    ] = (),
) -> tuple[
    str,
    ...,
]:

    raw = os.getenv(
        name
    )

    if (
        raw is None
        or not raw.strip()
    ):
        return tuple(
            default
        )

    return tuple(
        dict.fromkeys(
            item
            .strip()
            .lower()
            for item
            in raw.split(
                ","
            )
            if item.strip()
        )
    )


@dataclass(
    frozen=True,
    slots=True,
)
class Settings:
    runtime_dir: Path
    database_path: Path
    diagnostics_dir: Path
    log_path: Path

    action: str
    live_enabled: bool

    bot_token: str
    chat_id: str

    threshold_percent: float
    max_deal_percent: float

    min_samples: int
    max_samples: int

    max_messages_per_run: int

    timeout_seconds: int

    initial_delay_min: int
    initial_delay_max: int

    block_cooldown_hours: int

    max_web_sources_per_run: int

    enabled_sources: tuple[
        str,
        ...,
    ]

    forced_sources: tuple[
        str,
        ...,
    ]

    source_intervals_hours: dict[
        str,
        int,
    ]


def load_settings() -> Settings:

    runtime_dir = Path(
        os.getenv(
            "ACCURATE_RUNTIME_DIR",
            "accurate_average_runtime",
        )
    )

    enabled = tuple(
        source
        for source
        in ALL_SOURCES
        if env_bool(
            (
                "ACCURATE_SOURCE_"
                f"{source.upper()}_ENABLED"
            ),
            True,
        )
    )

    forced = env_csv(
        "ACCURATE_SOURCES"
    )

    intervals = {
        "telegram": env_int(
            "ACCURATE_INTERVAL_TELEGRAM_HOURS",
            3,
            1,
        ),
        "divar": env_int(
            "ACCURATE_INTERVAL_DIVAR_HOURS",
            12,
            1,
        ),
        "bama": env_int(
            "ACCURATE_INTERVAL_BAMA_HOURS",
            6,
            1,
        ),
        "khodro45": env_int(
            "ACCURATE_INTERVAL_KHODRO45_HOURS",
            12,
            1,
        ),
        "formula": env_int(
            "ACCURATE_INTERVAL_FORMULA_HOURS",
            12,
            1,
        ),
        "sheypoor": env_int(
            "ACCURATE_INTERVAL_SHEYPOOR_HOURS",
            6,
            1,
        ),
        "karnameh": env_int(
            "ACCURATE_INTERVAL_KARNAMEH_HOURS",
            6,
            1,
        ),
        "hamrah_mechanic": env_int(
            (
                "ACCURATE_INTERVAL_"
                "HAMRAH_MECHANIC_HOURS"
            ),
            12,
            1,
        ),
    }

    return Settings(
        runtime_dir=runtime_dir,
        database_path=(
            runtime_dir
            / "accurate_average.db"
        ),
        diagnostics_dir=(
            runtime_dir
            / "diagnostics"
        ),
        log_path=(
            runtime_dir
            / "accurate_average.log"
        ),
        action=os.getenv(
            "ACCURATE_ACTION",
            "scheduled",
        ).strip().lower(),
        live_enabled=env_bool(
            "ACCURATE_LIVE_ENABLED",
            False,
        ),
        bot_token=(
            os.getenv(
                "TELEGRAM_BOT_TOKEN"
            )
            or ""
        ).strip(),
        chat_id=(
            os.getenv(
                "TELEGRAM_CHAT_ID"
            )
            or ""
        ).strip(),
        threshold_percent=env_float(
            (
                "ACCURATE_DEAL_"
                "THRESHOLD_PERCENT"
            ),
            1.0,
            0.0,
        ),
        max_deal_percent=env_float(
            "ACCURATE_MAX_DEAL_PERCENT",
            15.0,
            0.0,
        ),
        min_samples=env_int(
            "ACCURATE_MIN_SAMPLES",
            3,
            1,
        ),
        max_samples=env_int(
            "ACCURATE_MAX_SAMPLES",
            10,
            1,
        ),
        max_messages_per_run=env_int(
            (
                "ACCURATE_MAX_"
                "MESSAGES_PER_RUN"
            ),
            1,
            1,
        ),
        timeout_seconds=env_int(
            (
                "ACCURATE_REQUEST_"
                "TIMEOUT_SECONDS"
            ),
            30,
            5,
        ),
        initial_delay_min=env_int(
            "ACCURATE_INITIAL_DELAY_MIN",
            20,
            0,
        ),
        initial_delay_max=env_int(
            "ACCURATE_INITIAL_DELAY_MAX",
            120,
            0,
        ),
        block_cooldown_hours=env_int(
            (
                "ACCURATE_BLOCK_"
                "COOLDOWN_HOURS"
            ),
            24,
            1,
        ),
        max_web_sources_per_run=env_int(
            (
                "ACCURATE_MAX_WEB_"
                "SOURCES_PER_RUN"
            ),
            3,
            1,
        ),
        enabled_sources=enabled,
        forced_sources=forced,
        source_intervals_hours=intervals,
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS aa_listings (
    source_key TEXT PRIMARY KEY,

    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,

    url TEXT NOT NULL,
    title TEXT NOT NULL,

    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    trim TEXT NOT NULL,
    model_year INTEGER NOT NULL,
    condition TEXT NOT NULL,
    body_condition TEXT NOT NULL DEFAULT 'unknown',

    mileage INTEGER,

    price INTEGER NOT NULL
        CHECK(price > 0),

    comparison_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,

    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_aa_group
ON aa_listings (
    comparison_key,
    first_seen
);

CREATE INDEX IF NOT EXISTS idx_aa_fingerprint
ON aa_listings (
    fingerprint
);


CREATE TABLE IF NOT EXISTS aa_sent_deals (
    source_key TEXT PRIMARY KEY,

    source TEXT NOT NULL,
    source_ad_id TEXT NOT NULL,

    price INTEGER NOT NULL,
    average_price INTEGER NOT NULL,
    discount_percent REAL NOT NULL,

    sent_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS aa_source_state (
    source TEXT PRIMARY KEY,

    last_attempt_at TEXT,
    last_success_at TEXT,
    next_allowed_at TEXT,
    blocked_until TEXT,

    consecutive_failures INTEGER
        NOT NULL DEFAULT 0,

    last_error TEXT
);


CREATE TABLE IF NOT EXISTS aa_execution_history (
    id INTEGER PRIMARY KEY
        AUTOINCREMENT,

    started_at TEXT NOT NULL,
    finished_at TEXT,

    action TEXT NOT NULL,
    sources TEXT,

    fetched INTEGER
        NOT NULL DEFAULT 0,

    accepted INTEGER
        NOT NULL DEFAULT 0,

    rejected INTEGER
        NOT NULL DEFAULT 0,

    duplicates INTEGER
        NOT NULL DEFAULT 0,

    deals_found INTEGER
        NOT NULL DEFAULT 0,

    messages_sent INTEGER
        NOT NULL DEFAULT 0,

    status TEXT
        NOT NULL DEFAULT 'started',

    error_message TEXT
);
"""


class Store:
    def __init__(
        self,
        path: Path,
        min_samples: int,
        max_samples: int,
    ) -> None:

        if (
            min_samples < 1
            or max_samples
            < min_samples
        ):
            raise ValueError(
                (
                    "invalid sample "
                    "limits"
                )
            )

        self.path = path
        self.min_samples = min_samples
        self.max_samples = max_samples

    @contextmanager
    def connect(
        self,
    ) -> Iterator[
        sqlite3.Connection
    ]:

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        connection = (
            sqlite3.connect(
                self.path,
                timeout=30,
            )
        )

        connection.row_factory = (
            sqlite3.Row
        )

        connection.execute(
            "PRAGMA journal_mode=WAL"
        )

        connection.execute(
            (
                "PRAGMA "
                "busy_timeout=30000"
            )
        )

        try:
            yield connection

            connection.commit()

        except Exception:
            connection.rollback()

            raise

        finally:
            connection.close()

    def initialize(
        self,
    ) -> None:

        with self.connect() as connection:
            connection.executescript(
                SCHEMA
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(aa_listings)"
                ).fetchall()
            }
            if "body_condition" not in columns:
                connection.execute(
                    "ALTER TABLE aa_listings "
                    "ADD COLUMN body_condition TEXT NOT NULL "
                    "DEFAULT 'unknown'"
                )

    def start_execution(
        self,
        action: str,
        sources: Iterable[str],
    ) -> int:

        with self.connect() as connection:
            cursor = connection.execute(
                (
                    "INSERT INTO "
                    "aa_execution_history"
                    "(started_at, action, sources) "
                    "VALUES(?,?,?)"
                ),
                (
                    now_iso(),
                    action,
                    ",".join(
                        sources
                    ),
                ),
            )

            return int(
                cursor.lastrowid
            )

    def finish_execution(
        self,
        execution_id: int,
        *,
        fetched: int,
        accepted: int,
        rejected: int,
        duplicates: int,
        deals_found: int,
        messages_sent: int,
        status: str,
        error: str | None = None,
    ) -> None:

        with self.connect() as connection:
            connection.execute(
                """
                UPDATE aa_execution_history
                SET
                    finished_at = ?,
                    fetched = ?,
                    accepted = ?,
                    rejected = ?,
                    duplicates = ?,
                    deals_found = ?,
                    messages_sent = ?,
                    status = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    now_iso(),
                    fetched,
                    accepted,
                    rejected,
                    duplicates,
                    deals_found,
                    messages_sent,
                    status,
                    (
                        error
                        or ""
                    )[:2000]
                    or None,
                    execution_id,
                ),
            )

    def source_state(
        self,
        source: str,
    ) -> sqlite3.Row | None:

        with self.connect() as connection:
            return connection.execute(
                (
                    "SELECT * "
                    "FROM aa_source_state "
                    "WHERE source=?"
                ),
                (
                    source,
                ),
            ).fetchone()

    def mark_source_attempt(
        self,
        source: str,
        success: bool,
        interval_hours: int,
        error: str | None = None,
        blocked_hours: int | None = None,
    ) -> None:

        now = datetime.now(
            timezone.utc
        )

        next_allowed = (
            now
            + timedelta(
                hours=interval_hours
            )
        )

        blocked_until = (
            now
            + timedelta(
                hours=blocked_hours
            )
            if blocked_hours
            else None
        )

        with self.connect() as connection:
            old = connection.execute(
                (
                    "SELECT "
                    "consecutive_failures "
                    "FROM aa_source_state "
                    "WHERE source=?"
                ),
                (
                    source,
                ),
            ).fetchone()

            failures = (
                0
                if success
                else (
                    int(
                        old[0]
                    )
                    + 1
                    if old
                    else 1
                )
            )

            connection.execute(
                """
                INSERT INTO aa_source_state (
                    source,
                    last_attempt_at,
                    last_success_at,
                    next_allowed_at,
                    blocked_until,
                    consecutive_failures,
                    last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(source)
                DO UPDATE SET
                    last_attempt_at =
                        excluded.last_attempt_at,

                    last_success_at =
                        CASE
                            WHEN ?
                            THEN excluded.last_success_at
                            ELSE aa_source_state.last_success_at
                        END,

                    next_allowed_at =
                        excluded.next_allowed_at,

                    blocked_until =
                        excluded.blocked_until,

                    consecutive_failures =
                        excluded.consecutive_failures,

                    last_error =
                        excluded.last_error
                """,
                (
                    source,
                    now.isoformat(),
                    (
                        now.isoformat()
                        if success
                        else None
                    ),
                    next_allowed.isoformat(),
                    (
                        blocked_until
                        .isoformat()
                        if blocked_until
                        else None
                    ),
                    failures,
                    error,
                    (
                        1
                        if success
                        else 0
                    ),
                ),
            )

    def source_due(
        self,
        source: str,
    ) -> bool:

        row = self.source_state(
            source
        )

        if row is None:
            return True

        now = datetime.now(
            timezone.utc
        )

        if row[
            "blocked_until"
        ]:
            blocked_until = (
                datetime.fromisoformat(
                    str(
                        row[
                            "blocked_until"
                        ]
                    )
                )
            )

            if (
                blocked_until
                > now
            ):
                return False

        if row[
            "next_allowed_at"
        ]:
            next_allowed = (
                datetime.fromisoformat(
                    str(
                        row[
                            "next_allowed_at"
                        ]
                    )
                )
            )

            if (
                next_allowed
                > now
            ):
                return False

        return True

    def get_existing(
        self,
        source_key: str,
    ) -> sqlite3.Row | None:

        with self.connect() as connection:
            return connection.execute(
                (
                    "SELECT * "
                    "FROM aa_listings "
                    "WHERE source_key=?"
                ),
                (
                    source_key,
                ),
            ).fetchone()

    def prior_group_rows(
        self,
        comparison_key: str,
        exclude_source_key: str | None = None,
    ) -> list[
        sqlite3.Row
    ]:

        query = (
            "SELECT * "
            "FROM aa_listings "
            "WHERE comparison_key=?"
        )

        params: list[
            object
        ] = [
            comparison_key
        ]

        if exclude_source_key:
            query += (
                " AND source_key<>?"
            )

            params.append(
                exclude_source_key
            )

        query += (
            " ORDER BY "
            "first_seen DESC "
            "LIMIT ?"
        )

        params.append(
            self.max_samples
        )

        with self.connect() as connection:
            return connection.execute(
                query,
                tuple(
                    params
                ),
            ).fetchall()

    def fingerprint_exists(
        self,
        fingerprint: str,
        exclude_source_key: str | None = None,
    ) -> bool:

        query = (
            "SELECT 1 "
            "FROM aa_listings "
            "WHERE fingerprint=?"
        )

        params: list[
            object
        ] = [
            fingerprint
        ]

        if exclude_source_key:
            query += (
                " AND source_key<>?"
            )

            params.append(
                exclude_source_key
            )

        query += " LIMIT 1"

        with self.connect() as connection:
            row = connection.execute(
                query,
                tuple(
                    params
                ),
            ).fetchone()

        return (
            row
            is not None
        )

    def upsert(
        self,
        listing: NormalizedVehicleListing,
    ) -> bool:

        now = now_iso()

        existing = (
            self.get_existing(
                listing.source_key
            )
        )

        with self.connect() as connection:
            if existing:
                connection.execute(
                    """
                    UPDATE aa_listings
                    SET
                        url = ?,
                        title = ?,
                        brand = ?,
                        model = ?,
                        trim = ?,
                        model_year = ?,
                        condition = ?,
                        body_condition = ?,
                        mileage = ?,
                        price = ?,
                        comparison_key = ?,
                        fingerprint = ?,
                        last_seen = ?,
                        updated_at = ?
                    WHERE source_key = ?
                    """,
                    (
                        listing.url,
                        listing.title,
                        listing.brand,
                        listing.model,
                        listing.trim,
                        listing.model_year,
                        listing.condition,
                        listing.body_condition,
                        listing.mileage,
                        listing.price,
                        listing.comparison_key,
                        listing.fingerprint,
                        now,
                        now,
                        listing.source_key,
                    ),
                )

                return False

            connection.execute(
                """
                INSERT INTO aa_listings (
                    source_key,
                    source,
                    source_ad_id,
                    url,
                    title,
                    brand,
                    model,
                    trim,
                    model_year,
                    condition,
                    body_condition,
                    mileage,
                    price,
                    comparison_key,
                    fingerprint,
                    first_seen,
                    last_seen,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    listing.source_key,
                    listing.source,
                    listing.source_ad_id,
                    listing.url,
                    listing.title,
                    listing.brand,
                    listing.model,
                    listing.trim,
                    listing.model_year,
                    listing.condition,
                    listing.body_condition,
                    listing.mileage,
                    listing.price,
                    listing.comparison_key,
                    listing.fingerprint,
                    now,
                    now,
                    now,
                ),
            )

        self.trim_group(
            listing.comparison_key
        )

        return True

    def trim_group(
        self,
        comparison_key: str,
    ) -> None:

        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT source_key
                FROM aa_listings
                WHERE comparison_key = ?
                ORDER BY first_seen DESC
                """,
                (
                    comparison_key,
                ),
            ).fetchall()

            for row in rows[
                self.max_samples:
            ]:
                connection.execute(
                    (
                        "DELETE FROM "
                        "aa_listings "
                        "WHERE source_key=?"
                    ),
                    (
                        row[
                            "source_key"
                        ],
                    ),
                )

    def was_sent(
        self,
        source_key: str,
    ) -> bool:

        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM aa_sent_deals
                WHERE source_key = ?
                LIMIT 1
                """,
                (
                    source_key,
                ),
            ).fetchone()

        return (
            row
            is not None
        )

    def mark_sent(
        self,
        listing: NormalizedVehicleListing,
        average_price: int,
        discount_percent: float,
    ) -> None:

        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE
                INTO aa_sent_deals (
                    source_key,
                    source,
                    source_ad_id,
                    price,
                    average_price,
                    discount_percent,
                    sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    listing.source_key,
                    listing.source,
                    listing.source_ad_id,
                    listing.price,
                    average_price,
                    discount_percent,
                    now_iso(),
                ),
            )

    def group_stats(
        self,
    ) -> tuple[
        int,
        int,
    ]:

        with self.connect() as connection:
            groups = connection.execute(
                """
                SELECT
                    comparison_key,
                    COUNT(*) AS n
                FROM aa_listings
                GROUP BY comparison_key
                """
            ).fetchall()

        ready = sum(
            1
            for row
            in groups
            if int(
                row[
                    "n"
                ]
            ) >= self.min_samples
        )

        return (
            len(
                groups
            ),
            ready,
        )


@dataclass(
    frozen=True,
    slots=True,
)
class Deal:
    listing: NormalizedVehicleListing

    average_price: int

    discount_percent: float

    sample_count: int


def now_iso() -> str:

    return datetime.now(
        timezone.utc
    ).isoformat()


def evaluate_against_history(
    store: Store,
    listing: NormalizedVehicleListing,
    threshold_percent: float,
    max_deal_percent: float,
) -> Deal | None:

    prior = store.prior_group_rows(
        listing.comparison_key,
        exclude_source_key=(
            listing.source_key
        ),
    )

    if (
        len(
            prior
        )
        < store.min_samples
    ):
        return None

    prices = [
        int(
            row[
                "price"
            ]
        )
        for row
        in prior
        if int(
            row[
                "price"
            ]
        ) > 0
    ]

    if (
        len(
            prices
        )
        < store.min_samples
    ):
        return None

    average = (
        sum(
            prices
        )
        / len(
            prices
        )
    )

    if (
        average <= 0
        or listing.price
        >= average
    ):
        return None

    discount = (
        (
            average
            - listing.price
        )
        / average
        * 100
    )

    if (
        discount < threshold_percent
        or discount > max_deal_percent
    ):
        return None

    return Deal(
        listing=listing,
        average_price=round(
            average
        ),
        discount_percent=round(
            discount,
            2,
        ),
        sample_count=len(
            prices
        ),
    )


def history_discount_percent(
    store: Store,
    listing: NormalizedVehicleListing,
) -> float | None:
    prior = store.prior_group_rows(
        listing.comparison_key,
        exclude_source_key=listing.source_key,
    )
    prices = [int(row["price"]) for row in prior if int(row["price"]) > 0]
    if len(prices) < store.min_samples:
        return None
    average = sum(prices) / len(prices)
    if average <= 0 or listing.price >= average:
        return None
    return (average - listing.price) / average * 100


def format_message(
    deal: Deal,
) -> str:

    trim = (
        f" {deal.listing.trim}"
        if deal.listing.trim
        else ""
    )

    condition = (
        "صفر"
        if (
            deal.listing
            .condition
            == "zero"
        )
        else "کارکرده"
    )

    body_labels = {
        "factory": "کارخانه / صفر",
        "clean": "بدون رنگ",
        "minor_paint": "لکه رنگ",
        "painted": "رنگ‌شده",
        "full_paint": "تمام/دور رنگ",
        "replaced": "قطعه تعویضی",
        "accident": "تصادفی/آسیب‌دیده",
        "unknown": "نامشخص",
    }
    body_condition = body_labels.get(
        deal.listing.body_condition,
        "نامشخص",
    )

    mileage = (
        "نامشخص"
        if (
            deal.listing
            .mileage
            is None
        )
        else (
            f"{deal.listing.mileage:,} "
            "کیلومتر"
        )
    )

    return (
        "*🚗 دیل سیستم میانگین دقیق\n\n"

        "🚘 خودرو:\n"
        f"{deal.listing.brand} "
        f"{deal.listing.model}"
        f"{trim}\n\n"

        "📅 مدل:\n"
        f"{deal.listing.model_year}\n\n"

        "🚦 وضعیت:\n"
        f"{condition}\n\n"

        "🎨 وضعیت بدنه:\n"
        f"{body_condition}\n\n"

        "🛣 کارکرد:\n"
        f"{mileage}\n\n"

        "💰 قیمت:\n"
        f"{deal.listing.price:,} "
        "تومان\n\n"

        f"📊 میانگین "
        f"{deal.sample_count} "
        "نمونه مشابه:\n"
        f"{deal.average_price:,} "
        "تومان\n\n"

        "📉 زیر میانگین:\n"
        f"{deal.discount_percent:.2f}٪\n\n"

        "🔎 منبع:\n"
        f"{deal.listing.source}\n\n"

        "🔗 لینک:\n"
        f"{deal.listing.url}"
    )


def send_telegram(
    settings: Settings,
    text: str,
) -> int | None:

    if (
        not settings.bot_token
        or not settings.chat_id
    ):
        raise RuntimeError(
            (
                "Telegram secrets "
                "are missing"
            )
        )

    response = requests.post(
        (
            "https://api.telegram.org/"
            f"bot{settings.bot_token}/"
            "sendMessage"
        ),
        json={
            "chat_id": (
                settings.chat_id
            ),
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=(
            settings
            .timeout_seconds
        ),
    )

    data = (
        response.json()
        if response.content
        else {}
    )

    if (
        response.status_code
        != 200
        or not isinstance(
            data,
            dict,
        )
        or data.get(
            "ok"
        ) is not True
    ):
        raise RuntimeError(
            (
                "Telegram send failed: "
                f"HTTP "
                f"{response.status_code} "
                f"{data}"
            )
        )

    result = data.get(
        "result"
    )

    if (
        isinstance(
            result,
            dict,
        )
        and isinstance(
            result.get(
                "message_id"
            ),
            int,
        )
    ):
        return result[
            "message_id"
        ]

    return None


def choose_sources(
    settings: Settings,
    store: Store,
) -> tuple[
    str,
    ...,
]:

    if settings.forced_sources:
        return tuple(
            source
            for source
            in settings.forced_sources
            if source
            in settings.enabled_sources
        )

    due = [
        source
        for source
        in settings.enabled_sources
        if store.source_due(
            source
        )
    ]

    telegram = [
        source
        for source
        in due
        if source
        == "telegram"
    ]

    web = [
        source
        for source
        in due
        if source
        in WEB_SOURCES
    ]

    random.shuffle(
        web
    )

    return tuple(
        telegram
        + web[
            :settings
            .max_web_sources_per_run
        ]
    )


def process_result(
    settings: Settings,
    store: Store,
    result: CollectorResult,
    *,
    write_state: bool,
    allow_send: bool,
) -> tuple[
    int,
    int,
]:

    deals: list[
        Deal
    ] = []

    for listing in (
        result.listings
    ):

        _trace_decision(settings, store, listing, allow_send)

        existing = (
            store.get_existing(
                listing.source_key
            )
        )

        if (
            existing is None
            and store
            .fingerprint_exists(
                listing.fingerprint
            )
        ):
            LOGGER.info(
                (
                    "%s "
                    "CrossSourceDuplicate "
                    "source_key=%s"
                ),
                SYSTEM,
                listing.source_key,
            )

            continue

        historical_discount = history_discount_percent(
            store,
            listing,
        )
        if (
            historical_discount is not None
            and historical_discount > settings.max_deal_percent
        ):
            LOGGER.warning(
                "%s Reject=suspicious_low_price source_key=%s discount=%.2f max=%.2f",
                SYSTEM,
                listing.source_key,
                historical_discount,
                settings.max_deal_percent,
            )
            continue

        deal = (
            evaluate_against_history(
                store,
                listing,
                settings
                .threshold_percent,
                settings
                .max_deal_percent,
            )
        )

        if (
            deal
            and not store.was_sent(
                listing.source_key
            )
        ):
            deals.append(
                deal
            )

        if write_state:
            store.upsert(
                listing
            )

    deals.sort(
        key=lambda deal: (
            -deal.discount_percent,
            deal.listing.price,
            deal.listing.source_key,
        )
    )

    messages = 0

    for deal in deals[
        :settings
        .max_messages_per_run
    ]:

        LOGGER.info(
            (
                "%s DealFound "
                "group=%s "
                "samples=%s "
                "average=%s "
                "price=%s "
                "discount=%.2f "
                "source=%s"
            ),
            SYSTEM,
            deal.listing
            .comparison_key,
            deal.sample_count,
            deal.average_price,
            deal.listing.price,
            deal.discount_percent,
            deal.listing.source,
        )

        text = format_message(
            deal
        )

        if allow_send:
            message_id = (
                send_telegram(
                    settings,
                    text,
                )
            )

            store.mark_sent(
                deal.listing,
                deal.average_price,
                deal.discount_percent,
            )

            messages += 1

            LOGGER.info(
                (
                    "%s TelegramSent "
                    "source_key=%s "
                    "message_id=%s"
                ),
                SYSTEM,
                deal.listing
                .source_key,
                message_id,
            )

        else:
            print(
                (
                    "\n"
                    "--- ACCURATE "
                    "SYSTEM B DRY RUN ---\n"
                    f"{text}\n"
                    "--- END ---\n"
                )
            )

    return (
        len(
            deals
        ),
        messages,
    )


def configure_logging(
    settings: Settings,
) -> None:

    settings.runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    root_logger = (
        logging.getLogger()
    )

    root_logger.setLevel(
        logging.INFO
    )

    if root_logger.handlers:
        return

    formatter = (
        logging.Formatter(
            (
                "%(asctime)s | "
                "%(levelname)s | "
                "%(name)s | "
                "%(message)s"
            )
        )
    )

    console = (
        logging.StreamHandler()
    )

    console.setFormatter(
        formatter
    )

    root_logger.addHandler(
        console
    )

    file_handler = (
        RotatingFileHandler(
            settings.log_path,
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    )

    file_handler.setFormatter(
        formatter
    )

    root_logger.addHandler(
        file_handler
    )


# Offline audit and shadow bridge. Does not replace the production decision rule.
SERVICE_AUDIT_VERSION = "1.0.0"
AUDIT_LOOKBACK_DAYS = 4
AUDIT_MAX_ROWS = 10000


def _engine_contract() -> None:
    if getattr(universal_engine, "ENGINE_VERSION", None) != "2.0.0":
        raise RuntimeError("This Service audit requires universal_deal_engine version 2.0.0")
    for name in ("from_listing", "evaluate_deal", "DealVehicle"):
        if not callable(getattr(universal_engine, name, None)):
            raise RuntimeError("Universal engine API is missing: " + name)


def _row_listing(row):
    """Adapt stored fields explicitly. Never invent body, mileage, or full price."""
    return SimpleNamespace(**{key: row[key] for key in (
        "source", "source_ad_id", "url", "brand", "model", "trim",
        "model_year", "condition", "body_condition", "mileage", "price",
    )})


def _universal_replay(listing, prior):
    _engine_contract()
    candidate = universal_engine.from_listing(listing)
    references = [universal_engine.from_listing(_row_listing(row)) for row in prior]
    # Do not use legacy fingerprints as trusted cross-source vehicle identifiers.
    return universal_engine.evaluate_deal(candidate, references)


def _legacy_reason(listing, prior, minimum, threshold, maximum):
    """Explain the existing arithmetic-mean rule without changing it."""
    prices = [int(row["price"]) for row in prior if int(row["price"]) > 0]
    if len(prior) < minimum or len(prices) < minimum:
        return "insufficient_samples", None
    average = sum(prices) / len(prices)
    if average <= 0:
        return "invalid_reference", None
    discount = (average - listing.price) / average * 100
    if listing.price >= average:
        return "not_below_reference", discount
    if discount < threshold:
        return "discount_below_minimum", discount
    if discount > maximum:
        return "discount_above_maximum", discount
    return "candidate_within_range", discount


def _public_run_config(settings):
    return {
        "action": settings.action,
        "live_enabled": settings.live_enabled,
        "bot_token_present": bool(settings.bot_token),
        "chat_id_present": bool(settings.chat_id),
        "scheduled_send_permitted": settings.live_enabled,
        "scheduled_send_credentials_present": bool(settings.bot_token and settings.chat_id),
        "legacy_reference_method": "arithmetic_mean",
        "new_engine_reference_method": "median",
        "new_engine_mode": "shadow_only_not_authority_to_send",
        "legacy_min_samples": settings.min_samples,
        "legacy_max_samples": settings.max_samples,
        "legacy_min_discount": settings.threshold_percent,
        "legacy_max_discount": settings.max_deal_percent,
        "engine_min_samples": universal_engine.MIN_SAMPLES,
        "engine_max_samples": universal_engine.MAX_SAMPLES,
        "engine_min_discount": universal_engine.MIN_DEAL_PERCENT,
        "engine_max_discount": universal_engine.MAX_DEAL_PERCENT,
        "legacy_message_limit": settings.max_messages_per_run,
        "legacy_message_limit_scope": "per_process_result_call_not_global",
        "web_sources_per_run": settings.max_web_sources_per_run,
        "forced_sources": list(settings.forced_sources),
    }


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None  # Do not guess a timezone for unlabelled stored dates.
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _source_gate(settings, source, state, now):
    if source not in settings.enabled_sources:
        return "disabled"
    if state:
        for field, reason in (("blocked_until", "cooldown"), ("next_allowed_at", "not_due")):
            if state.get(field):
                dt = _timestamp(state[field])
                if dt is None:
                    return "invalid_state_timestamp"
                if dt > now:
                    return reason
    return "due"


def _log_run_selection(settings, store, selected):
    LOGGER.info("%s RuntimeConfig %s", SYSTEM, json.dumps(_public_run_config(settings), sort_keys=True))
    now = datetime.now(timezone.utc)
    for source in ALL_SOURCES:
        row = store.source_state(source)
        state = dict(row) if row is not None else {}
        gate = _source_gate(settings, source, state, now)
        selection = "selected" if source in selected else (
            "excluded_by_sources_input" if settings.forced_sources else
            "web_source_budget" if gate == "due" and source in WEB_SOURCES else gate
        )
        LOGGER.info(
            "%s SourceSelection source=%s result=%s state_gate=%s interval_hours=%s next_allowed_at=%s blocked_until=%s",
            SYSTEM, source, selection, gate, settings.source_intervals_hours[source],
            state.get("next_allowed_at"), state.get("blocked_until"),
        )


def _trace_decision(settings, store, listing, allow_send):
    """Observe both evaluators; never grant sending or change storage here."""
    try:
        prior = store.prior_group_rows(listing.comparison_key, listing.source_key)
        legacy, discount = _legacy_reason(listing, prior, store.min_samples,
                                          settings.threshold_percent, settings.max_deal_percent)
        decision = _universal_replay(listing, prior)
        sent = store.was_sent(listing.source_key)
        LOGGER.info(
            "%s DecisionAudit source=%s source_key=%s legacy_reason=%s legacy_samples=%s "
            "new_engine_reason=%s new_engine_samples=%s new_engine_details=%s "
            "already_sent=%s allow_send=%s mode=shadow",
            SYSTEM, listing.source, listing.source_key, legacy, len(prior),
            decision.reason, decision.sample_count, ",".join(decision.details) or "none",
            sent, allow_send,
        )
    except Exception as exc:
        # Observability must not silently change the existing production behavior.
        # Log only the exception class: raw error text could contain credentials.
        LOGGER.error("%s DecisionAuditFailed source=%s error_type=%s", SYSTEM, listing.source, type(exc).__name__)


def _emit_audit(settings, report):
    settings.diagnostics_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.diagnostics_dir / "system_b_state_audit.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("%s AuditStatus=%s mode=read_only_no_source_requests engine_mode=shadow", SYSTEM, report["status"])
    LOGGER.info("%s AuditConfig %s", SYSTEM, json.dumps(report["config"], sort_keys=True))
    for source, entry in report.get("sources", {}).items():
        LOGGER.info(
            "%s AuditSource source=%s gate=%s stored=%s sent_last_4_days=%s "
            "last_attempt=%s next_allowed=%s body_unknown=%s mileage_unknown=%s",
            SYSTEM, source, entry["gate"], entry["stored_count"], entry["sent_last_4_days"],
            entry["last_attempt_at"], entry["next_allowed_at"],
            entry["unknown_body_count"], entry["unknown_mileage_count"],
        )
        LOGGER.info(
            "%s AuditReasons source=%s legacy=%s new_engine=%s details=%s",
            SYSTEM, source, json.dumps(entry["legacy_replay_reasons"], sort_keys=True),
            json.dumps(entry["engine_replay_reasons"], sort_keys=True),
            json.dumps(entry["engine_replay_details"], sort_keys=True),
        )
    LOGGER.info("%s AuditSummary %s", SYSTEM, json.dumps(report.get("summary", {}), sort_keys=True))
    LOGGER.info("%s AuditReport=%s", SYSTEM, destination)


def run_state_audit(settings):
    """Read the RESTORED snapshot only. No collectors, send calls, or state writes.

    diagnostic now means this offline audit (unlike the legacy diagnostic crawl).
    Retrospective replay is NOT a reconstruction of historical market conditions.
    """
    _engine_contract()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=AUDIT_LOOKBACK_DAYS)
    report = {
        "audit_version": SERVICE_AUDIT_VERSION,
        "as_of_utc": now.isoformat(), "since_utc": since.isoformat(),
        "status": "state_missing", "config": _public_run_config(settings),
        "source_requests": 0, "send_requests": 0, "persistent_state_writes": 0,
        "sources": {}, "summary": {},
        "limitations": [
            "This is a restored database snapshot, not live advertisements.",
            "Replays use currently retained prices, not the prices present during each old run.",
            "Old collector rejection texts and per-source execution counts may not be stored.",
            "aa_listings does not store full descriptions/full-price evidence; financial/body claims cannot be reverified here.",
            "Current live_enabled does not establish its value during the previous four days.",
            "Unknown freshness, raw text, and source provenance require review before enabling the new sending engine.",
            "Independent Car Price Bot and Divar Deal Service are outside this audit.",
            "Legacy group pruning and row limits can leave insufficient retained references.",
        ],
    }
    if not settings.database_path.is_file():
        _emit_audit(settings, report)
        return 2
    # mode=ro prevents data modification; transaction gives a consistent snapshot.
    try:
        connection = sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    except sqlite3.Error as exc:
        report.update(status="audit_read_error", error_type=type(exc).__name__)
        _emit_audit(settings, report)
        return 2
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"aa_listings", "aa_sent_deals", "aa_source_state", "aa_execution_history"}
        if not required.issubset(tables):
            report.update(status="schema_incomplete", missing_tables=sorted(required - tables))
            _emit_audit(settings, report)
            return 2
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(aa_listings)")}
        needed = {"source_key", "source", "source_ad_id", "url", "brand", "model", "trim", "model_year",
                  "condition", "body_condition", "mileage", "price", "comparison_key", "first_seen", "last_seen"}
        if not needed.issubset(columns):
            report.update(status="schema_incomplete", missing_columns=sorted(needed - columns))
            _emit_audit(settings, report)
            return 2
        full_count = connection.execute("SELECT COUNT(*) FROM aa_listings").fetchone()[0]
        rows = [dict(r) for r in connection.execute(
            "SELECT * FROM aa_listings ORDER BY last_seen DESC, source_key LIMIT ?", (AUDIT_MAX_ROWS,))]
        source_counts = {r["source"]: dict(r) for r in connection.execute(
            "SELECT source, COUNT(*) AS n, "
            "SUM(CASE WHEN body_condition IN ('unknown','') THEN 1 ELSE 0 END) AS unknown_body, "
            "SUM(CASE WHEN mileage IS NULL THEN 1 ELSE 0 END) AS unknown_mileage "
            "FROM aa_listings GROUP BY source")}
        states = {r["source"]: dict(r) for r in connection.execute("SELECT * FROM aa_source_state")}
        sent_keys = {r[0] for r in connection.execute("SELECT source_key FROM aa_sent_deals")}
        sent_counts = {r["source"]: dict(r) for r in connection.execute(
            "SELECT source, COUNT(*) AS n, MAX(sent_at) AS last_sent, "
            "SUM(CASE WHEN julianday(sent_at)>=julianday(?) AND julianday(sent_at)<=julianday(?) THEN 1 ELSE 0 END) AS recent "
            "FROM aa_sent_deals GROUP BY source", (since.isoformat(), now.isoformat()))}
        old_runs_count = connection.execute(
            "SELECT COUNT(*) FROM aa_execution_history WHERE julianday(started_at)>=julianday(?) AND julianday(started_at)<=julianday(?)",
            (since.isoformat(), now.isoformat()),
        ).fetchone()[0]
        # No exception messages: stored HTTP errors can contain secrets or personal data.
        runs = [dict(r) for r in connection.execute(
            "SELECT id, started_at, finished_at, action, sources, fetched, accepted, rejected, "
            "duplicates, deals_found, messages_sent, status FROM aa_execution_history "
            "WHERE julianday(started_at)>=julianday(?) AND julianday(started_at)<=julianday(?) "
            "ORDER BY id DESC LIMIT 1000", (since.isoformat(), now.isoformat()))]
        first_run = connection.execute("SELECT MIN(started_at) FROM aa_execution_history").fetchone()[0]
    except sqlite3.Error as exc:
        report.update(status="audit_read_error", error_type=type(exc).__name__)
        _emit_audit(settings, report)
        return 2
    finally:
        connection.close()

    groups = {}
    for row in rows:
        groups.setdefault(row["comparison_key"], []).append(row)
    for group in groups.values():
        group.sort(key=lambda r: (r["first_seen"], r["source_key"]), reverse=True)

    names = sorted(set(ALL_SOURCES) | set(source_counts) | set(states) | set(sent_counts))
    for name in names:
        state, counts, sent = states.get(name, {}), source_counts.get(name, {}), sent_counts.get(name, {})
        report["sources"][name] = {
            "enabled": name in settings.enabled_sources,
            "interval_hours": settings.source_intervals_hours.get(name),
            "gate": _source_gate(settings, name, state, now),
            "last_attempt_at": state.get("last_attempt_at"),
            "last_success_at": state.get("last_success_at"),
            "next_allowed_at": state.get("next_allowed_at"),
            "blocked_until": state.get("blocked_until"),
            "consecutive_failures": state.get("consecutive_failures", 0),
            "has_last_error": bool(state.get("last_error")),
            "stored_count": counts.get("n", 0),
            "unknown_body_count": counts.get("unknown_body", 0) or 0,
            "unknown_mileage_count": counts.get("unknown_mileage", 0) or 0,
            "sent_last_4_days": sent.get("recent", 0) or 0,
            "last_sent_at": sent.get("last_sent"),
            "replayed_rows": 0, "recently_seen_replayed_rows": 0,
            "legacy_replay_reasons": Counter(), "engine_replay_reasons": Counter(),
            "engine_replay_details": Counter(), "already_sent_rows": 0,
            "legacy_potential_unsent": 0, "engine_potential_unsent": 0,
        }
    for row in rows:
        entry = report["sources"][row["source"]]
        entry["replayed_rows"] += 1
        last_seen = _timestamp(row.get("last_seen"))
        entry["recently_seen_replayed_rows"] += int(last_seen is not None and since <= last_seen <= now)
        prior = [r for r in groups[row["comparison_key"]] if r["source_key"] != row["source_key"]][:settings.max_samples]
        try:
            item = _row_listing(row)
            reason, _ = _legacy_reason(item, prior, settings.min_samples, settings.threshold_percent, settings.max_deal_percent)
            decision = _universal_replay(item, prior)
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            entry["engine_replay_reasons"]["stored_record_invalid"] += 1
            continue
        entry["legacy_replay_reasons"][reason] += 1
        entry["engine_replay_reasons"][decision.reason] += 1
        for detail in decision.details:
            field, separator, count = detail.partition("=")
            entry["engine_replay_details"][field] += int(count) if separator and count.isdigit() else 1
        was_sent = row["source_key"] in sent_keys
        entry["already_sent_rows"] += int(was_sent)
        entry["legacy_potential_unsent"] += int(reason == "candidate_within_range" and not was_sent)
        entry["engine_potential_unsent"] += int(decision.is_deal and not was_sent)
    report["recent_executions"] = runs
    report["status"] = "ok" if full_count <= AUDIT_MAX_ROWS and old_runs_count <= 1000 else "ok_truncated"
    report["summary"] = {
        "stored_rows_total": full_count, "replayed_rows": len(rows),
        "row_limit": AUDIT_MAX_ROWS, "rows_truncated": full_count > AUDIT_MAX_ROWS,
        "recent_executions_total": old_runs_count, "recent_executions_in_report": len(runs),
        "first_retained_execution_at": first_run,
        "recent_actions": dict(Counter(r["action"] for r in runs)),
        "recent_statuses": dict(Counter(r["status"] for r in runs)),
        "recent_deals_recorded": sum(r["deals_found"] or 0 for r in runs),
        "recent_messages_recorded": sum(r["messages_sent"] or 0 for r in runs),
        "legacy_potential_unsent_now": sum(e["legacy_potential_unsent"] for e in report["sources"].values()),
        "new_engine_potential_unsent_now": sum(e["engine_potential_unsent"] for e in report["sources"].values()),
        "interpretation": "replay_of_retained_history_not_proof_of_live_deals_or_actual_past_decisions",
    }
    _emit_audit(settings, report)
    return 0


def _run_service_bridge_self_test():
    """Complete Store -> engine integration and read-only audit regression."""
    from dataclasses import replace
    from unittest.mock import patch
    import hashlib
    _engine_contract()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        settings = replace(load_settings(), runtime_dir=root, database_path=root / "test.db",
                           diagnostics_dir=root / "diagnostics", log_path=root / "test.log",
                           action="diagnostic", bot_token="", chat_id="", min_samples=3,
                           max_samples=10, threshold_percent=1.0, max_deal_percent=15.0,
                           forced_sources=(), enabled_sources=ALL_SOURCES)
        store = Store(settings.database_path, 3, 10)
        store.initialize()
        def listing(index, price, mileage=100000, body="clean"):
            return NormalizedVehicleListing(
                source="telegram", source_ad_id=str(index), url=f"https://t.me/test/{index}",
                title="test", brand="Peugeot", model="206", trim="تیپ 5", model_year=1397,
                condition="used", body_condition=body, mileage=mileage, price=price,
                collected_at=now_iso(), raw_text="")
        for i, p in enumerate((1_000_000_000, 980_000_000, 1_020_000_000), 1):
            store.upsert(listing(i, p))
        candidate = listing(4, 950_000_000)
        prior = store.prior_group_rows(candidate.comparison_key, candidate.source_key)
        result = _universal_replay(candidate, prior)
        if not result.is_deal or result.sample_count != 3 or result.discount_percent != 5.0:
            raise AssertionError("Store -> universal engine bridge failed")
        if _universal_replay(replace(candidate, mileage=None), prior).is_deal:
            raise AssertionError("Unknown mileage was accepted")
        if _universal_replay(replace(candidate, body_condition="unknown"), prior).is_deal:
            raise AssertionError("Unknown body was accepted")
        store.upsert(candidate)
        store.mark_source_attempt("divar", True, 12)
        before = hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
        with patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network forbidden")), \
             patch(__name__ + ".build_collectors", side_effect=AssertionError("collectors forbidden")), \
             patch(__name__ + ".send_telegram", side_effect=AssertionError("sending forbidden")):
            if run_state_audit(settings) != 0:
                raise AssertionError("Offline audit failed")
        after = hashlib.sha256(settings.database_path.read_bytes()).hexdigest()
        if before != after:
            raise AssertionError("Audit changed persistent database")
        report = json.loads((settings.diagnostics_dir / "system_b_state_audit.json").read_text(encoding="utf-8"))
        if report["sources"]["divar"]["gate"] != "not_due":
            raise AssertionError("Due diagnosis failed")
        if report["sources"]["telegram"]["replayed_rows"] != 4:
            raise AssertionError("Stored record audit incomplete")
    print("service universal shadow integration self-test: OK")
    print("service read-only diagnostic self-test: OK")


def run_self_test() -> None:

    collectors_self_test()
    _run_service_bridge_self_test()

    with tempfile.TemporaryDirectory() as temporary:

        store = Store(
            Path(
                temporary
            )
            / "test.db",
            3,
            10,
        )

        store.initialize()

        def listing(
            index: int,
            price: int,
        ) -> NormalizedVehicleListing:

            return NormalizedVehicleListing(
                source="test",
                source_ad_id=str(
                    index
                ),
                url=(
                    "https://example.com/"
                    f"{index}"
                ),
                title=(
                    "پژو 206 تیپ 2 "
                    "مدل 1383"
                ),
                brand="Peugeot",
                model="206",
                trim="تیپ 2",
                model_year=1383,
                condition="used",
                body_condition="clean",
                mileage=(
                    100000
                    + index
                ),
                price=price,
                collected_at=(
                    now_iso()
                ),
                raw_text="",
            )

        prices = (
            900_000_000,
            950_000_000,
            920_000_000,
        )

        for (
            index,
            price,
        ) in enumerate(
            prices,
            1,
        ):
            current = listing(
                index,
                price,
            )

            assert (
                evaluate_against_history(
                    store,
                    current,
                    1.0,
                    15.0,
                )
                is None
            )

            store.upsert(
                current
            )

        candidate = listing(
            5,
            850_000_000,
        )

        deal = (
            evaluate_against_history(
                store,
                candidate,
                1.0,
                15.0,
            )
        )

        assert (
            deal
            is not None
        )

        assert (
            deal.sample_count
            == 3
        )

        assert (
            deal.discount_percent
            >= 1
        )

        too_low = listing(
            99,
            500_000_000,
        )
        assert (
            evaluate_against_history(
                store,
                too_low,
                1.0,
                15.0,
            )
            is None
        )

        store.upsert(
            candidate
        )

        store.upsert(
            listing(
                5,
                840_000_000,
            )
        )

        assert (
            len(
                store.prior_group_rows(
                    candidate
                    .comparison_key
                )
            )
            == 4
        )

        for index in range(
            6,
            12,
        ):
            store.upsert(
                listing(
                    index,
                    (
                        900_000_000
                        + index
                    ),
                )
            )

        assert (
            len(
                store.prior_group_rows(
                    candidate
                    .comparison_key
                )
            )
            == 10
        )


def run() -> int:

    settings = (
        load_settings()
    )

    configure_logging(
        settings
    )

    if (
        settings.action
        == "self_test"
    ):
        run_self_test()

        print(
            (
                "accurate_average_service "
                "self-test: OK"
            )
        )

        return 0

    if (
        settings.action
        == "telegram_test"
    ):
        message_id = (
            send_telegram(
                settings,
                (
                    "*✅ اتصال System B "
                    "به تلگرام با موفقیت "
                    "انجام شد."
                ),
            )
        )

        LOGGER.info(
            (
                "%s TelegramTest "
                "message_id=%s"
            ),
            SYSTEM,
            message_id,
        )

        return 0

    if settings.action == "diagnostic":
        return run_state_audit(settings)

    settings.runtime_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    store = Store(
        settings.database_path,
        settings.min_samples,
        settings.max_samples,
    )

    store.initialize()

    sources = choose_sources(
        settings,
        store,
    )

    _log_run_selection(settings, store, sources)

    if not sources:
        LOGGER.info(
            "%s NoSourceDue",
            SYSTEM,
        )

        return 0

    context = CollectorContext(
        runtime_dir=(
            settings.runtime_dir
        ),
        diagnostics_dir=(
            settings.diagnostics_dir
        ),
        timeout_seconds=(
            settings.timeout_seconds
        ),
        initial_delay_min=(
            settings.initial_delay_min
        ),
        initial_delay_max=(
            settings.initial_delay_max
        ),
    )

    collectors = build_collectors(
        context,
        sources,
    )

    execution_id = (
        store.start_execution(
            settings.action,
            sources,
        )
    )

    total_fetched = 0
    total_accepted = 0
    total_rejected = 0
    total_duplicates = 0
    total_deals = 0
    total_messages = 0

    status = "success"

    errors: list[
        str
    ] = []

    try:
        for source in sources:

            collector: (
                BaseCollector
                | None
            ) = collectors.get(
                source
            )

            if collector is None:
                continue

            LOGGER.info(
                (
                    "%s "
                    "SourceStart=%s"
                ),
                SYSTEM,
                source,
            )

            try:
                result = (
                    collector.collect()
                )

            finally:
                collector.close()

            total_fetched += (
                result.fetched
            )

            total_accepted += (
                result.accepted
            )

            total_rejected += (
                result.rejected
            )

            total_duplicates += (
                result.duplicates
            )

            LOGGER.info(
                (
                    "%s Source=%s "
                    "Fetched=%s "
                    "Accepted=%s "
                    "Rejected=%s "
                    "Duplicates=%s "
                    "Blocked=%s "
                    "Error=%r"
                ),
                SYSTEM,
                source,
                result.fetched,
                result.accepted,
                result.rejected,
                result.duplicates,
                result.blocked,
                result.error,
            )

            if (
                settings.action
                == "diagnostic"
            ):
                write_state = False
                allow_send = False

            else:
                write_state = True

                allow_send = (
                    settings.action
                    == "live"
                    or (
                        settings.action
                        == "scheduled"
                        and settings
                        .live_enabled
                    )
                )

            (
                deals,
                messages,
            ) = process_result(
                settings,
                store,
                result,
                write_state=write_state,
                allow_send=allow_send,
            )

            total_deals += deals
            total_messages += messages

            success = (
                result.error is None
                and not result.blocked
            )

            store.mark_source_attempt(
                source,
                success=success,
                interval_hours=(
                    settings
                    .source_intervals_hours[
                        source
                    ]
                ),
                error=result.error,
                blocked_hours=(
                    settings
                    .block_cooldown_hours
                    if result.blocked
                    else None
                ),
            )

            if result.error:
                errors.append(
                    (
                        f"{source}:"
                        f"{result.error}"
                    )
                )

        (
            groups,
            ready_groups,
        ) = store.group_stats()

        LOGGER.info(
            (
                "%s Summary "
                "groups=%s "
                "ready_groups=%s "
                "deals=%s "
                "messages=%s"
            ),
            SYSTEM,
            groups,
            ready_groups,
            total_deals,
            total_messages,
        )

        if errors:
            status = (
                "partial_success"
            )

        store.finish_execution(
            execution_id,
            fetched=total_fetched,
            accepted=total_accepted,
            rejected=total_rejected,
            duplicates=total_duplicates,
            deals_found=total_deals,
            messages_sent=total_messages,
            status=status,
            error=(
                "; ".join(
                    errors
                )
                or None
            ),
        )

        return 0

    except Exception as exc:
        store.finish_execution(
            execution_id,
            fetched=total_fetched,
            accepted=total_accepted,
            rejected=total_rejected,
            duplicates=total_duplicates,
            deals_found=total_deals,
            messages_sent=total_messages,
            status="failed",
            error=str(
                exc
            ),
        )

        LOGGER.exception(
            "%s FatalError",
            SYSTEM,
        )

        return 1


def main() -> None:

    parser = (
        argparse.ArgumentParser()
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = (
        parser.parse_args()
    )

    if args.self_test:
        run_self_test()

        print(
            (
                "accurate_average_service "
                "self-test: OK"
            )
        )

        return

    raise SystemExit(
        run()
    )


if __name__ == "__main__":
    main()
