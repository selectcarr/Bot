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
import time
import site_detail_reader
import vehicle_evidence_normalizer

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
    raw_text TEXT NOT NULL DEFAULT '',

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

    def _backup_before_text_migration(self):
        """Snapshot an existing DB once before the additive raw_text migration."""
        if not self.path.is_file():
            return
        source = destination = None
        temporary = None
        try:
            source = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
            columns = {row[1] for row in source.execute("PRAGMA table_info(aa_listings)")}
            if not columns or "raw_text" in columns:
                return
            folder = self.path.parent / "state_backups"
            folder.mkdir(parents=True, exist_ok=True)
            final = folder / "before_raw_text_v1.sqlite3"
            if final.exists():
                return  # Keep the first pre-migration snapshot; never overwrite it.
            handle, name = tempfile.mkstemp(prefix="before_raw_text_", suffix=".tmp", dir=folder)
            os.close(handle)
            temporary = Path(name)
            destination = sqlite3.connect(temporary)
            source.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Evidence migration backup check failed")
            destination.close()
            destination = None
            os.replace(temporary, final)
            temporary = None
            LOGGER.info("%s EvidenceMigration backup_created=true scope=encrypted_runtime_state", SYSTEM)
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)


    def initialize(
        self,
    ) -> None:

        self._backup_before_text_migration()

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
            if "raw_text" not in columns:
                connection.execute("ALTER TABLE aa_listings ADD COLUMN raw_text TEXT NOT NULL DEFAULT ''")
            # Old unknown fields remain unknown. No guesses or group-key rewrites.

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
        received_text = str(getattr(listing, "raw_text", "") or "")[:16000]

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
                        raw_text = ?,
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
                        received_text,
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
                    raw_text,
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
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    received_text,
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


# The receiving policy and the mean/median engines remain separate. This stage
# makes the reference set independent of the order in which sources finish.
SHARED_BATCH_VERSION = "1.0.0"


def _batch_ad_url(value: object) -> str:
    """A local identity token; keep query parameters which identify an ad."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
        netloc = f"[{host}]" if ":" in host else host
        if port is not None and (parsed.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            netloc += f":{port}"
        query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"})
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/") or "/", urlencode(query), ""))
    except (ValueError, TypeError):
        return ""


def _batch_row(listing: NormalizedVehicleListing, first_seen: str) -> dict:
    return {
        "source_key": listing.source_key, "source": listing.source,
        "source_ad_id": listing.source_ad_id, "url": listing.url,
        "title": listing.title, "brand": listing.brand, "model": listing.model,
        "trim": listing.trim, "model_year": listing.model_year,
        "condition": listing.condition, "body_condition": listing.body_condition,
        "mileage": listing.mileage, "price": listing.price,
        "comparison_key": listing.comparison_key, "fingerprint": listing.fingerprint,
        "raw_text": listing.raw_text, "first_seen": first_seen,
        "last_seen": listing.collected_at, "updated_at": listing.collected_at,
    }


def _batch_tokens(row: dict) -> set[tuple[str, str]]:
    # The fingerprint is the EXISTING heuristic, not proof of physical identity.
    tokens = {("key", str(row["source_key"]))}
    if row.get("fingerprint"):
        tokens.add(("legacy_fingerprint", str(row["fingerprint"])))
    url = _batch_ad_url(row.get("url"))
    if url:
        tokens.add(("url", url))
    return tokens


def _batch_order(row: dict) -> tuple[float, str]:
    from hashlib import sha256
    timestamp = _timestamp(row.get("first_seen"))
    # A hash tie-break has no lexical preference for a particular site name.
    tie = sha256(str(row["source_key"]).encode("utf-8")).hexdigest()
    return (-(timestamp.timestamp() if timestamp is not None else 0.0), tie)


class _SharedReferenceView:
    """Immutable-for-the-batch view over history + this run's accepted records.

    No source filter, state writes, requests, or pruning are performed here.
    Existing exact comparison keys are retained. Labels and historical freshness
    are not inferred by this class. Legacy policy uses its current LIMIT; the
    shadow engine receives the full matching group before its own validation.
    """
    def __init__(self, rows: Iterable[dict], minimum: int, maximum: int):
        self.min_samples = minimum
        self.max_samples = maximum
        self.rows = {str(row["source_key"]): dict(row) for row in rows}
        self.groups: dict[str, list[dict]] = {}
        for row in self.rows.values():
            self.groups.setdefault(row["comparison_key"], []).append(row)
        for group in self.groups.values():
            group.sort(key=_batch_order)

    def all_group_rows(self, comparison_key: str, exclude_source_key: str | None = None) -> list[dict]:
        excluded = self.rows.get(exclude_source_key or "")
        seen = _batch_tokens(excluded) if excluded is not None else set()
        if exclude_source_key:
            seen.add(("key", exclude_source_key))
        selected = []
        for row in self.groups.get(comparison_key, ()):
            tokens = _batch_tokens(row)
            if tokens & seen:
                continue
            selected.append(row)
            seen.update(tokens)
        return selected

    def prior_group_rows(self, comparison_key: str, exclude_source_key: str | None = None) -> list[dict]:
        return self.all_group_rows(comparison_key, exclude_source_key)[:self.max_samples]


def _prepare_shared_batch(settings: Settings, store: Store, results: Iterable[CollectorResult]):
    # One frozen history snapshot: evaluating one candidate cannot change the
    # references seen by the next one. The durable database is not staged here.
    with store.connect() as connection:
        historical = {row["source_key"]: dict(row) for row in connection.execute("SELECT * FROM aa_listings")}
    original = _SharedReferenceView(historical.values(), store.min_samples, store.max_samples)
    incoming: dict[str, NormalizedVehicleListing] = {}
    conflicts: set[str] = set()
    timestamp = now_iso()
    input_count = 0
    for result in results:
        for listing in result.listings:
            input_count += 1
            if not isinstance(listing, NormalizedVehicleListing) or listing.source != result.source:
                raise ValueError("CollectorResult listing/source contract mismatch")
            key = listing.source_key
            if key in conflicts:
                continue
            if key in incoming:
                previous = incoming[key]
                # Conflicting observations of the SAME ID are never resolved by
                # choosing the price that makes a deal look more attractive.
                if (previous.fingerprint != listing.fingerprint
                        or _batch_ad_url(previous.url) != _batch_ad_url(listing.url)):
                    conflicts.add(key)
                    incoming.pop(key)
                    continue
                if (listing.collected_at, listing.raw_text) > (previous.collected_at, previous.raw_text):
                    incoming[key] = listing
            else:
                incoming[key] = listing

    # A shared canonical ad URL with conflicting facts is not resolved by an
    # arbitrary source/price choice. Exclude it for this batch, keep disk history.
    observed_rows = {key: row for key, row in historical.items() if key not in incoming and key not in conflicts}
    observed_rows.update({key: _batch_row(item, timestamp) for key, item in incoming.items()})
    by_url = {}
    for row in observed_rows.values():
        url = _batch_ad_url(row.get("url"))
        if url:
            by_url.setdefault(url, []).append(row)
    for aliases in by_url.values():
        signatures = {tuple(row.get(field) for field in
                            ("brand", "model", "trim", "model_year", "condition", "body_condition", "mileage", "price"))
                      for row in aliases}
        if len(signatures) > 1:
            conflicts.update(row["source_key"] for row in aliases)
    incoming = {key: item for key, item in incoming.items() if key not in conflicts}

    # Supersede old versions for every currently observed key, including conflicts.
    union = {key: row for key, row in historical.items() if key not in incoming and key not in conflicts}
    seen_tokens = set()
    for row in union.values():
        seen_tokens.update(_batch_tokens(row))
    admitted: list[NormalizedVehicleListing] = []
    duplicate_count = suspicious_count = 0
    # Deterministic admission: retained observations take precedence over a new
    # copy; within each class source order and collection order have no effect.
    ordered = sorted(incoming.values(), key=lambda item: (
        item.source_key not in historical,
        _batch_order(_batch_row(item, timestamp)),
    ))
    for listing in ordered:
        first_seen = historical.get(listing.source_key, {}).get("first_seen") or timestamp
        row = _batch_row(listing, first_seen)
        tokens = _batch_tokens(row)
        if tokens & seen_tokens:
            duplicate_count += 1
            LOGGER.info("%s SharedBatchReject source=%s reason=duplicate_observation", SYSTEM, listing.source)
            continue
        # Retain the legacy suspicious-low-price guard, against PRE-RUN history.
        # Do not use other new batch rows to decide whether to store this record.
        discount = history_discount_percent(original, listing)
        if discount is not None and discount > settings.max_deal_percent:
            suspicious_count += 1
            LOGGER.info("%s SharedBatchReject source=%s reason=suspicious_low_price", SYSTEM, listing.source)
            continue
        admitted.append(listing)
        union[listing.source_key] = row
        seen_tokens.update(tokens)
    view = _SharedReferenceView(union.values(), store.min_samples, store.max_samples)
    LOGGER.info(
        "%s SharedBatch phase=compare scope=all_system_b_sources version=%s "
        "input=%s admitted=%s duplicate=%s conflict=%s suspicious=%s history_rows=%s "
        "reference_pool=history_plus_current_batch production_reference=arithmetic_mean engine_mode=shadow",
        SYSTEM, SHARED_BATCH_VERSION, input_count, len(admitted), duplicate_count,
        len(conflicts), suspicious_count, len(historical),
    )
    return admitted, view


def _trace_shared_decision(settings: Settings, store: Store, view: _SharedReferenceView,
                           listing: NormalizedVehicleListing, allow_send: bool) -> None:
    try:
        legacy_rows = view.prior_group_rows(listing.comparison_key, listing.source_key)
        all_rows = view.all_group_rows(listing.comparison_key, listing.source_key)
        legacy, _ = _legacy_reason(listing, legacy_rows, store.min_samples,
                                   settings.threshold_percent, settings.max_deal_percent)
        decision = _universal_replay(listing, all_rows)
        distribution = dict(sorted(Counter(row["source"] for row in legacy_rows).items()))
        LOGGER.info(
            "%s SharedDecision source=%s source_key=%s legacy_reason=%s legacy_samples=%s "
            "reference_sources=%s shadow_group_rows=%s new_engine_reason=%s new_engine_samples=%s "
            "new_engine_details=%s already_sent=%s allow_send=%s engine_mode=shadow",
            SYSTEM, listing.source, listing.source_key, legacy, len(legacy_rows),
            json.dumps(distribution, sort_keys=True, separators=(",", ":")), len(all_rows),
            decision.reason, decision.sample_count, ",".join(decision.details) or "none",
            store.was_sent(listing.source_key), allow_send,
        )
    except Exception as exc:
        LOGGER.error("%s SharedDecisionAuditFailed source=%s error_type=%s", SYSTEM, listing.source, type(exc).__name__)


def process_batch(settings: Settings, store: Store, results: Iterable[CollectorResult], *,
                  write_state: bool, allow_send: bool) -> tuple[int, int]:
    """Evaluate only this run's candidates against a shared, frozen reference set.

    The production price rule remains the EXISTING arithmetic mean. The new
    median engine stays shadow-only; no claims about missing body/details are
    invented and no old rows are replayed as candidates for mass sending.
    """
    if allow_send and not write_state:
        raise ValueError("Sending requires durable sent-message memory")
    admitted, view = _prepare_shared_batch(settings, store, tuple(results))
    deals: list[Deal] = []
    for listing in admitted:
        _trace_shared_decision(settings, store, view, listing, allow_send)
        deal = evaluate_against_history(view, listing, settings.threshold_percent, settings.max_deal_percent)
        if deal is not None and not store.was_sent(listing.source_key):
            deals.append(deal)

    # All decisions are made before these writes/pruning. Keep the established
    # storage and retention mechanism, including raw text and sent-memory schema.
    if write_state:
        for listing in sorted(admitted, key=lambda item: item.source_key):
            store.upsert(listing)

    deals.sort(key=lambda deal: (-deal.discount_percent, deal.listing.price, deal.listing.source_key))
    messages = 0
    for deal in deals[:settings.max_messages_per_run]:
        LOGGER.info("%s DealFound group=%s samples=%s average=%s price=%s discount=%.2f source=%s scope=shared_batch",
                    SYSTEM, deal.listing.comparison_key, deal.sample_count, deal.average_price,
                    deal.listing.price, deal.discount_percent, deal.listing.source)
        text = format_message(deal)
        if allow_send:
            # Preserve the existing send function and mark only after it returns.
            message_id = send_telegram(settings, text)
            store.mark_sent(deal.listing, deal.average_price, deal.discount_percent)
            messages += 1
            LOGGER.info("%s TelegramSent source_key=%s message_id=%s", SYSTEM, deal.listing.source_key, message_id)
        else:
            print("\n--- ACCURATE SYSTEM B DRY RUN ---\n" + text + "\n--- END ---\n")
    LOGGER.info("%s SharedBatchComplete candidates=%s deals=%s messages=%s "
                "message_limit=%s message_limit_scope=whole_run", SYSTEM, len(admitted), len(deals),
                messages, settings.max_messages_per_run)
    return len(deals), messages


def process_result(settings: Settings, store: Store, result: CollectorResult, *,
                   write_state: bool, allow_send: bool) -> tuple[int, int]:
    """Compatibility entry point; the live run now calls process_batch ONCE."""
    return process_batch(settings, store, (result,), write_state=write_state, allow_send=allow_send)


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
SERVICE_AUDIT_VERSION = "1.1.0"
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
        "legacy_message_limit_scope": "whole_run_all_selected_sources",
        "processing_mode": "collect_all_then_compare",
        "shared_batch_version": SHARED_BATCH_VERSION,
        "comparison_scope": "all_system_b_sources",
        "web_sources_per_run": settings.max_web_sources_per_run,
        "forced_sources": list(settings.forced_sources),
        "evidence_capture_version": "1.0.0",
        "production_engine_unchanged": True,
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



def _retained_observation_counts(settings):
    path = settings.runtime_dir / "listing_observations.sqlite3"
    if not path.is_file():
        return {"status": "not_recorded_yet", "sources": {}}
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        entries = {}
        for row in connection.execute("""
            SELECT source,COUNT(*) AS stored,
                SUM(outcome='accepted') AS accepted, SUM(outcome='rejected') AS rejected,
                SUM(claimed_body_in_text='unknown') AS body_not_established_in_received_text,
                SUM(text_truncated) AS truncated
            FROM listing_observations GROUP BY source
        """):
            entries[row["source"]] = {k: int(row[k] or 0) for k in row.keys() if k != "source"}
        for name in entries:
            entries[name]["reject_reasons"] = {r["reason"]: int(r["n"]) for r in connection.execute(
                "SELECT reason,COUNT(*) AS n FROM listing_observations WHERE source=? AND outcome='rejected' GROUP BY reason", (name,))}
        return {"status": "ok", "scope": "retained_observations_not_this_run", "sources": entries}
    except sqlite3.Error as exc:
        return {"status": "read_error", "error_type": type(exc).__name__, "sources": {}}
    finally:
        if connection is not None:
            connection.close()

def _emit_audit(settings, report):
    report["observations"] = _retained_observation_counts(settings)
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
    LOGGER.info("%s AuditEvidence %s", SYSTEM, json.dumps(report["observations"], sort_keys=True))
    try:
        normalized_summary = vehicle_evidence_normalizer.read_summary(settings.runtime_dir)
        LOGGER.info("%s AuditNormalizedEvidence %s", SYSTEM, json.dumps(normalized_summary, sort_keys=True))
    except (sqlite3.Error, ValueError, OSError) as exc:
        LOGGER.warning("%s AuditNormalizedEvidenceUnavailable error_type=%s", SYSTEM, type(exc).__name__)

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
            "raw_text is only the text received by the collector, not guaranteed full detail or independently verified full-price evidence; old rows may be empty.",
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
        raw_counts = ({r["source"]: int(r["n"]) for r in connection.execute(
            "SELECT source, COUNT(*) AS n FROM aa_listings WHERE trim(raw_text)<>'' GROUP BY source")}
            if "raw_text" in columns else {})
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
            "stored_received_text_count": raw_counts.get(name, 0),
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



def _run_evidence_storage_self_test():
    import hashlib
    from dataclasses import replace
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "test.db"
        # Reproduce an existing state without received-text persistence.
        connection = sqlite3.connect(path)
        connection.executescript(SCHEMA.replace("    raw_text TEXT NOT NULL DEFAULT '',\n", ""))
        connection.close()
        store = Store(path, 3, 10)
        store.initialize()
        backup = root / "state_backups" / "before_raw_text_v1.sqlite3"
        if not backup.is_file():
            raise AssertionError("Migration must preserve a pre-change snapshot")
        before = hashlib.sha256(backup.read_bytes()).hexdigest()
        store.initialize()
        if hashlib.sha256(backup.read_bytes()).hexdigest() != before:
            raise AssertionError("Migration must be idempotent")
        item = NormalizedVehicleListing(source="test", source_ad_id="evidence", url="https://example.com/1",
            title="sample", brand="Peugeot", model="206", trim="تیپ 5", model_year=1397,
            condition="used", body_condition="clean", mileage=100000, price=1000000000,
            collected_at=now_iso(), raw_text="بدون رنگ\nقیمت 1000000000 تومان")
        store.upsert(item)
        if store.get_existing(item.source_key)["raw_text"] != item.raw_text:
            raise AssertionError("Received text must survive storage")
        store.mark_sent(item, 1100000000, 9.09)
        store.upsert(replace(item, body_condition="unknown", raw_text="وضعیت نامشخص"))
        row = store.get_existing(item.source_key)
        if row["raw_text"] != "وضعیت نامشخص" or row["body_condition"] != "unknown":
            raise AssertionError("Do not reuse obsolete body evidence")
        if not store.was_sent(item.source_key):
            raise AssertionError("Sent-message memory must not change")
    print("service evidence storage self-test: OK")

def _run_shared_batch_self_test() -> None:
    """Regression tests of the complete shared-reference batch path; no HTTP."""
    import io
    import itertools
    import unittest
    from dataclasses import replace
    from contextlib import redirect_stdout
    from unittest.mock import patch

    class SharedBatchTests(unittest.TestCase):
        def setUp(self):
            self.directory = tempfile.TemporaryDirectory()
            root = Path(self.directory.name)
            self.settings = replace(load_settings(), runtime_dir=root, database_path=root / "test.db",
                                    diagnostics_dir=root / "diagnostics", log_path=root / "test.log",
                                    action="dry_run", bot_token="", chat_id="", min_samples=3, max_samples=10,
                                    threshold_percent=1.0, max_deal_percent=15.0, max_messages_per_run=1)
            self.store = Store(self.settings.database_path, 3, 10)
            self.store.initialize()
            self.stamp = "2026-01-01T00:00:00+00:00"

        def tearDown(self):
            self.directory.cleanup()

        def item(self, source, key, price=1_000_000_000, **changes):
            arguments = dict(source=source, source_ad_id=key, url=f"https://example.test/{source}/{key}",
                             title=f"fixture {key}", brand="Peugeot", model="206", trim="تیپ 5",
                             model_year=1397, condition="used", body_condition="clean", mileage=100000,
                             price=price, collected_at=self.stamp, raw_text="")
            arguments.update(changes)
            return NormalizedVehicleListing(**arguments)

        def results(self, items):
            grouped = {}
            for item in items:
                grouped.setdefault(item.source, []).append(item)
            return tuple(CollectorResult(source, len(rows), len(rows), 0, 0, False, None, tuple(rows))
                         for source, rows in grouped.items())

        def refs(self):
            return [self.item(source, f"ref{i}") for i, source in enumerate(("divar", "bama", "karnameh"))]

        def plan(self, items):
            return _prepare_shared_batch(self.settings, self.store, self.results(items))

        def evaluate(self, view, candidate):
            return evaluate_against_history(view, candidate, 1.0, 15.0)

        def test_01_later_sources_are_available_to_first_candidate(self):
            candidate = self.item("telegram", "target", 950_000_000)
            admitted, view = self.plan([candidate] + self.refs())
            self.assertEqual(len(admitted), 4)
            result = self.evaluate(view, candidate)
            self.assertEqual((result.sample_count, result.average_price, result.discount_percent), (3, 1_000_000_000, 5.0))
            self.assertEqual({r["source"] for r in view.prior_group_rows(candidate.comparison_key, candidate.source_key)},
                             {"divar", "bama", "karnameh"})

        def test_02_all_24_source_orders_give_same_result(self):
            candidate = self.item("telegram", "target", 950_000_000)
            signatures = set()
            for permutation in itertools.permutations([candidate] + self.refs()):
                _, view = self.plan(permutation)
                result = self.evaluate(view, candidate)
                signatures.add((result.average_price, result.discount_percent, result.sample_count))
            self.assertEqual(signatures, {(1_000_000_000, 5.0, 3)})

        def test_03_any_of_eight_sources_can_be_candidate(self):
            for source in ALL_SOURCES:
                candidate = self.item(source, "target", 950_000_000)
                references = [self.item(other, f"ref{i}") for i, other in enumerate(s for s in ALL_SOURCES if s != source)][:3]
                _, view = self.plan([candidate] + references)
                self.assertEqual(self.evaluate(view, candidate).sample_count, 3)

        def test_04_history_from_a_not_fetched_source_remains_usable(self):
            for item in self.refs():
                self.store.upsert(item)
            candidate = self.item("telegram", "target", 950_000_000)
            _, view = self.plan([candidate])
            self.assertEqual(self.evaluate(view, candidate).sample_count, 3)

        def test_05_multiple_telegram_channels_share_references(self):
            candidate = self.item("divar", "target", 950_000_000)
            rows = [self.item("telegram", "channel_a:1"), self.item("telegram", "channel_b:1"), self.item("bama", "ref")]
            _, view = self.plan([candidate] + rows)
            self.assertEqual(self.evaluate(view, candidate).sample_count, 3)

        def test_06_ten_references_plus_self_exclusion(self):
            candidate = self.item("telegram", "target", 950_000_000)
            _, view = self.plan([candidate] + [self.item("bama", f"ref{i}") for i in range(15)])
            rows = view.prior_group_rows(candidate.comparison_key, candidate.source_key)
            self.assertEqual(len(rows), 10)
            self.assertNotIn(candidate.source_key, {r["source_key"] for r in rows})
            self.assertEqual(len(view.all_group_rows(candidate.comparison_key, candidate.source_key)), 15)

        def test_07_repeated_same_id_is_one_record(self):
            candidate = self.item("telegram", "target", 950_000_000)
            refs = self.refs()[:2]
            admitted, view = self.plan([candidate] + refs + [refs[0]] * 3)
            self.assertEqual(len(admitted), 3)
            self.assertIsNone(self.evaluate(view, candidate))

        def test_08_conflicting_same_id_is_not_price_selected(self):
            candidate = self.item("telegram", "target", 950_000_000)
            refs = self.refs()
            conflict = replace(refs[0], price=1_500_000_000)
            admitted, view = self.plan([candidate] + refs + [conflict])
            self.assertNotIn(refs[0].source_key, {x.source_key for x in admitted})
            self.assertIsNone(self.evaluate(view, candidate))

        def test_09_repeated_url_does_not_make_three_samples(self):
            candidate = self.item("telegram", "target", 950_000_000)
            first, second = self.refs()[:2]
            third = self.item("karnameh", "copy", url=first.url + "?utm_source=copy")
            _, view = self.plan([candidate, first, second, third])
            self.assertIsNone(self.evaluate(view, candidate))

        def test_10_different_body_is_not_merged(self):
            candidate = self.item("telegram", "target", 950_000_000)
            refs = [replace(item, body_condition="minor_paint") for item in self.refs()]
            _, view = self.plan([candidate] + refs)
            self.assertEqual(view.prior_group_rows(candidate.comparison_key, candidate.source_key), [])

        def test_11_two_references_are_insufficient(self):
            candidate = self.item("telegram", "target", 950_000_000)
            _, view = self.plan([candidate] + self.refs()[:2])
            self.assertIsNone(self.evaluate(view, candidate))

        def test_12_nonwriting_batch_does_not_insert_or_mark_sent(self):
            items = [self.item("telegram", "target", 950_000_000)] + self.refs()
            with redirect_stdout(io.StringIO()), patch(__name__ + ".send_telegram", side_effect=AssertionError("send forbidden")):
                process_batch(self.settings, self.store, self.results(items), write_state=False, allow_send=False)
            with self.store.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM aa_listings").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM aa_sent_deals").fetchone()[0], 0)

        def test_13_message_cap_is_global_and_history_saved(self):
            items = [self.item("telegram", "target", 950_000_000)] + self.refs()
            items += [replace(self.item("divar", "target2", 950_000_000), model="207")]
            items += [replace(item, model="207", source_ad_id=item.source_ad_id + "other", url=item.url + "other") for item in self.refs()]
            with patch(__name__ + ".send_telegram", return_value=1) as sender:
                deals, messages = process_batch(self.settings, self.store, self.results(items), write_state=True, allow_send=True)
                self.assertEqual((deals, messages, sender.call_count), (2, 1, 1))
            with self.store.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM aa_listings").fetchone()[0], 8)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM aa_sent_deals").fetchone()[0], 1)

        def test_14_already_sent_candidate_is_not_sent_again(self):
            candidate = self.item("telegram", "target", 950_000_000)
            self.store.mark_sent(candidate, 1_000_000_000, 5.0)
            with patch(__name__ + ".send_telegram", side_effect=AssertionError("send forbidden")):
                deals, sent = process_batch(self.settings, self.store, self.results([candidate] + self.refs()), write_state=True, allow_send=True)
            self.assertEqual((deals, sent), (0, 0))

        def test_15_mean_policy_is_not_silently_replaced_by_median(self):
            candidate = self.item("telegram", "target", 950_000_000)
            refs = self.refs()
            refs[-1] = replace(refs[-1], price=1_300_000_000)
            _, view = self.plan([candidate] + refs)
            self.assertEqual(self.evaluate(view, candidate).average_price, 1_100_000_000)
            self.assertEqual(_universal_replay(candidate, view.all_group_rows(candidate.comparison_key, candidate.source_key)).market_average, 1_000_000_000)

        def test_16_shadow_validates_before_ten_sample_cap(self):
            candidate = self.item("telegram", "target", 950_000_000)
            items = [candidate] + self.refs() + [self.item("bama", f"unknown{i}", mileage=None) for i in range(12)]
            _, view = self.plan(items)
            decision = _universal_replay(candidate, view.all_group_rows(candidate.comparison_key, candidate.source_key))
            self.assertTrue(decision.is_deal)
            self.assertEqual(decision.sample_count, 3)

        def test_17_result_source_contract_is_checked(self):
            result = CollectorResult("bama", 1, 1, 0, 0, False, None, (self.item("telegram", "bad"),))
            with self.assertRaises(ValueError):
                _prepare_shared_batch(self.settings, self.store, (result,))

        def test_18_updated_ad_supersedes_its_old_version(self):
            item = self.refs()[0]
            self.store.upsert(replace(item, price=900_000_000))
            candidate = self.item("telegram", "target", 950_000_000)
            _, view = self.plan([candidate] + self.refs())
            self.assertEqual(self.evaluate(view, candidate).average_price, 1_000_000_000)
            self.assertEqual(sum(r["source_key"] == item.source_key for r in view.all_group_rows(candidate.comparison_key)), 1)

        def test_19_previous_history_low_price_guard_is_retained(self):
            for item in self.refs():
                self.store.upsert(item)
            candidate = self.item("telegram", "too_cheap", 500_000_000)
            with redirect_stdout(io.StringIO()):
                deals, sent = process_batch(self.settings, self.store, self.results([candidate]), write_state=True, allow_send=False)
            self.assertEqual((deals, sent), (0, 0))
            self.assertIsNone(self.store.get_existing(candidate.source_key))

        def test_20_new_audit_never_prints_received_raw_text(self):
            candidate = self.item("telegram", "target", 950_000_000, raw_text="private-test-body-abcdef")
            _, view = self.plan([candidate] + self.refs())
            with patch.object(LOGGER, "info") as captured:
                _trace_shared_decision(self.settings, self.store, view, candidate, False)
            emitted = repr(captured.call_args_list)
            self.assertNotIn(candidate.raw_text, emitted)
            self.assertIn("reference_sources=", emitted)

        def test_21_inclusive_price_boundaries(self):
            for price, expected in [(990_000_000, True), (850_000_000, True), (995_000_000, False), (840_000_000, False)]:
                candidate = self.item("telegram", "boundary", price)
                _, view = self.plan([candidate] + self.refs())
                self.assertEqual(self.evaluate(view, candidate) is not None, expected)

        def test_22_no_replay_sending_of_history_only(self):
            for item in [self.item("telegram", "old", 950_000_000)] + self.refs():
                self.store.upsert(item)
            with patch(__name__ + ".send_telegram", side_effect=AssertionError("old replay send forbidden")):
                self.assertEqual(process_batch(self.settings, self.store, (), write_state=True, allow_send=True), (0, 0))

        def test_23_conflicting_alias_url_is_not_price_selected(self):
            candidate = self.item("telegram", "target", 950_000_000)
            refs = self.refs()
            alias = self.item("formula", "alias", price=1_500_000_000, url=refs[0].url)
            admitted, view = self.plan([candidate] + refs + [alias])
            self.assertNotIn(refs[0].source_key, {item.source_key for item in admitted})
            self.assertNotIn(alias.source_key, {item.source_key for item in admitted})
            self.assertIsNone(self.evaluate(view, candidate))

    output = io.StringIO()
    with patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network forbidden")), redirect_stdout(io.StringIO()):
        outcome = unittest.TextTestRunner(stream=output, verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(SharedBatchTests))
    if not outcome.wasSuccessful():
        raise AssertionError("Shared batch self-test failed:\n" + output.getvalue())
    print("service shared-source batch self-test: OK")
    print(f"service shared-source batch version={SHARED_BATCH_VERSION} tests={outcome.testsRun}")




DETAIL_STAGE_VERSION = "1.0.0"


def _record_detail_blocks(store, blocks):
    """Persist a detail block for the shared source without shortening a wait.

    Successful search state is otherwise untouched. No market/sent rows change.
    This does not control the separate Divar Deal Service's independent state.
    """
    for source, until in blocks.items():
        new_until = datetime.fromtimestamp(until, timezone.utc)
        with store.connect() as connection:
            row = connection.execute("SELECT * FROM aa_source_state WHERE source=?", (source,)).fetchone()
            if row is None:
                raise RuntimeError("Missing search state for blocked detail source")
            old_until = _timestamp(row["blocked_until"])
            if old_until is not None and old_until > new_until:
                new_until = old_until
            old_next = _timestamp(row["next_allowed_at"])
            next_at = max(new_until, old_next) if old_next is not None else new_until
            connection.execute("UPDATE aa_source_state SET blocked_until=?, next_allowed_at=?, "
                               "consecutive_failures=consecutive_failures+1, last_error=? WHERE source=?",
                               (new_until.isoformat(), next_at.isoformat(),
                                "detail_rate_limit_or_human_verification", source))


def _run_detail_stage(settings, store, results, since, remaining_seconds):
    """Stage 1 writes description evidence, never promotes it into pricing yet."""
    if settings.action not in {"scheduled", "live", "dry_run"}:
        return None
    if any(result.blocked for result in results):
        LOGGER.warning("%s DetailStageSkipped reason=search_blocked no_detail_requests=true", SYSTEM)
        return None
    selected = [result.source for result in results
                if result.source in WEB_SOURCES and result.error is None and not result.blocked]
    if not selected:
        LOGGER.info("%s DetailStageSkipped reason=no_successful_web_source", SYSTEM)
        return None
    try:
        policy = site_detail_reader.Policy.from_environment(settings.block_cooldown_hours)
        external_blocks = {}
        for source in selected:
            row = store.source_state(source)
            if row is not None:
                moment = _timestamp(row["blocked_until"])
                if moment is not None:
                    external_blocks[source] = moment.timestamp()
        report = site_detail_reader.capture_selected_details(
            runtime=settings.runtime_dir, selected_sources=selected, since=since,
            source_intervals=settings.source_intervals_hours,
            external_blocks=external_blocks, policy=policy,
            max_seconds=max(0, remaining_seconds),
        )
        _record_detail_blocks(store, report.blocks)
        return "detail_source_blocked" if report.blocks else None
    except Exception as exc:
        # Keep successfully collected observations; do not retry failed detail I/O.
        LOGGER.error("%s DetailStageError error_type=%s no_retry=true", SYSTEM, type(exc).__name__)
        return "detail_stage_error:" + type(exc).__name__


def _run_detail_stage_self_test():
    from dataclasses import replace
    from unittest.mock import patch
    site_detail_reader.run_self_test()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        settings = replace(load_settings(), runtime_dir=root, database_path=root / "stage_test.db",
                           diagnostics_dir=root / "diagnostics", log_path=root / "test.log",
                           action="dry_run", bot_token="", chat_id="")
        store = Store(settings.database_path, settings.min_samples, settings.max_samples)
        store.initialize()
        store.mark_source_attempt("divar", True, 12)
        results = [CollectorResult("divar", 1, 0, 1, 0, False, None, ())]
        with patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network forbidden")), \
             patch.object(site_detail_reader, "capture_selected_details", return_value=site_detail_reader.StageReport()) as spy:
            if _run_detail_stage(settings, store, results, now_iso(), 120) is not None or spy.call_count != 1:
                raise AssertionError("Detail stage must be connected to selected web sources")
            if list(spy.call_args.kwargs["selected_sources"]) != ["divar"]:
                raise AssertionError("Detail stage selected an unrequested source")
            if _run_detail_stage(replace(settings, action="diagnostic"), store, results, now_iso(), 120) is not None or spy.call_count != 1:
                raise AssertionError("Diagnostic must not request details")
        until = (datetime.now(timezone.utc) + timedelta(hours=36)).timestamp()
        with patch.object(site_detail_reader, "capture_selected_details", return_value=site_detail_reader.StageReport(blocks={"divar": until})):
            if _run_detail_stage(settings, store, results, now_iso(), 120) != "detail_source_blocked":
                raise AssertionError("Missing propagated detail block")
        if store.source_due("divar"):
            raise AssertionError("Detail block must prevent later ordinary source collection")
        _record_detail_blocks(store, {"divar": until - 10000})
        if _timestamp(store.source_state("divar")["blocked_until"]).timestamp() < until:
            raise AssertionError("Do not shorten source cooldown")
    print("service detail stage integration self-test: OK")
    print("service detail stage version=1.0.0 pricing_unchanged=true")


NORMALIZATION_STAGE_VERSION = "1.0.0"


def _run_normalization_stage(settings):
    """Offline Stage 2: retained messages/cards + matching Stage 1 details.

    Does not promote parsed records into the legacy reference pool or authorize
    sending. Count/chassis fields require the later bank/engine contract; do not
    squeeze them into the old coarse body_condition strings.
    """
    if settings.action not in {"scheduled", "live", "dry_run"}:
        return None
    try:
        vehicle_evidence_normalizer.normalize_retained(settings.runtime_dir)
        return None
    except Exception as exc:
        LOGGER.error("%s NormalizationStageError error_type=%s source_requests=0 pricing_unchanged=true",
                     SYSTEM, type(exc).__name__)
        return "normalization_stage_error:" + type(exc).__name__


def _run_normalization_stage_self_test():
    from dataclasses import replace
    from unittest.mock import patch
    vehicle_evidence_normalizer.run_self_test()
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        settings = replace(load_settings(), runtime_dir=root, action="dry_run", bot_token="", chat_id="")
        with patch.object(vehicle_evidence_normalizer, "normalize_retained", return_value={"processed": 0}) as spy:
            if _run_normalization_stage(settings) is not None or spy.call_count != 1:
                raise AssertionError("Stage 2 must execute for a real receiving run")
            for action in ("self_test", "diagnostic", "telegram_test"):
                if _run_normalization_stage(replace(settings, action=action)) is not None or spy.call_count != 1:
                    raise AssertionError("Stage 2 writes must not run in non-receiving modes")
    print("service vehicle normalization integration self-test: OK")
    print("service normalization stage version=1.0.0 source_requests=0 pricing_unchanged=true")


def run_self_test() -> None:

    _run_normalization_stage_self_test()

    _run_detail_stage_self_test()

    _run_evidence_storage_self_test()

    collectors_self_test()
    _run_service_bridge_self_test()
    _run_shared_batch_self_test()

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

    detail_run_started = time.monotonic()
    detail_observed_since = now_iso()

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

    batch_results: list[CollectorResult] = []

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
                result = collector.collect()
            except Exception as exc:
                # Do not retry. A failing source must not discard other results.
                from accurate_average_collectors import SourceBlockedError
                result = CollectorResult(
                    source=source, fetched=0, accepted=0, rejected=0, duplicates=0,
                    blocked=isinstance(exc, SourceBlockedError),
                    error="collector_exception:" + type(exc).__name__, listings=(),
                )
            finally:
                try:
                    collector.close()
                except Exception as exc:
                    LOGGER.warning("%s CollectorCloseFailed source=%s error_type=%s",
                                   SYSTEM, source, type(exc).__name__)

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

            batch_results.append(result)

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

            if result.error or result.blocked:
                errors.append(f"{source}:" + (result.error or "source_blocked"))

        # Bounded additional detail evidence only, after selected sources finish.
        # Leave time for shared evaluation and encrypted state persistence.
        detail_error = _run_detail_stage(
            settings, store, batch_results, detail_observed_since,
            remaining_seconds=1100 - (time.monotonic() - detail_run_started),
        )
        if detail_error:
            errors.append(detail_error)

        # Stage 2 consumes only the already-received message/detail evidence.
        normalization_error = _run_normalization_stage(settings)
        if normalization_error:
            errors.append(normalization_error)

        # Two phases: no candidate evaluation/sending until selected sources finish.
        total_deals, total_messages = process_batch(
            settings, store, batch_results, write_state=True,
            allow_send=(settings.action == "live" or
                        (settings.action == "scheduled" and settings.live_enabled)),
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
