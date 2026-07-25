from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DATABASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS divar_ads (
    ad_id TEXT PRIMARY KEY,

    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    trim TEXT NOT NULL DEFAULT '',
    year INTEGER NOT NULL,

    price INTEGER NOT NULL CHECK (price > 0),
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',

    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_divar_ads_vehicle_key
ON divar_ads (
    brand,
    model,
    trim,
    year
);

CREATE INDEX IF NOT EXISTS idx_divar_ads_last_seen
ON divar_ads (
    last_seen
);


CREATE TABLE IF NOT EXISTS divar_sent_ads (
    ad_id TEXT PRIMARY KEY,

    price INTEGER NOT NULL,
    market_average INTEGER NOT NULL,
    diff_percent REAL NOT NULL,

    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_divar_sent_ads_sent_at
ON divar_sent_ads (
    sent_at
);


CREATE TABLE IF NOT EXISTS divar_daily_schedule (
    schedule_date TEXT NOT NULL,
    run_time TEXT NOT NULL,

    executed INTEGER NOT NULL DEFAULT 0,
    executed_at TEXT,

    PRIMARY KEY (
        schedule_date,
        run_time
    )
);


CREATE TABLE IF NOT EXISTS divar_execution_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    scheduled_for TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,

    status TEXT NOT NULL DEFAULT 'started',

    ads_seen INTEGER NOT NULL DEFAULT 0,
    ads_saved INTEGER NOT NULL DEFAULT 0,
    deals_found INTEGER NOT NULL DEFAULT 0,

    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_divar_execution_started_at
ON divar_execution_history (
    started_at
);
"""


class Database:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )

        connection.row_factory = sqlite3.Row

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA journal_mode = WAL"
        )
        connection.execute(
            "PRAGMA synchronous = NORMAL"
        )
        connection.execute(
            "PRAGMA busy_timeout = 30000"
        )

        try:
            yield connection
            connection.commit()

        except Exception:
            connection.rollback()
            raise

        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                DATABASE_SCHEMA
            )

    def cleanup_old_data(
        self,
        retention_days: int,
    ) -> None:
        if retention_days < 1:
            raise ValueError(
                "retention_days must be at least 1."
            )

        date_modifier = f"-{retention_days} days"

        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM divar_ads
                WHERE last_seen < datetime('now', ?)
                """,
                (date_modifier,),
            )

            connection.execute(
                """
                DELETE FROM divar_sent_ads
                WHERE sent_at < datetime('now', ?)
                """,
                (date_modifier,),
            )

            connection.execute(
                """
                DELETE FROM divar_daily_schedule
                WHERE schedule_date < date('now', ?)
                """,
                (date_modifier,),
            )

            connection.execute(
                """
                DELETE FROM divar_execution_history
                WHERE started_at < datetime('now', ?)
                """,
                (date_modifier,),
            )

    def health_check(self) -> bool:
        try:
            with self.connect() as connection:
                result = connection.execute(
                    "SELECT 1"
                ).fetchone()

            return result is not None and result[0] == 1

        except sqlite3.Error:
            return False
