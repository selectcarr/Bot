from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from divar_service.storage.database import Database


@dataclass(frozen=True, slots=True)
class ScheduledRun:
    schedule_date: date
    scheduled_for: datetime
    executed: bool
    executed_at: datetime | None = None


class ScheduleRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def has_schedule(
        self,
        schedule_date: date,
    ) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM divar_daily_schedule
                WHERE schedule_date = ?
                LIMIT 1
                """,
                (schedule_date.isoformat(),),
            ).fetchone()

        return row is not None

    def save_schedule(
        self,
        schedule_date: date,
        run_times: Iterable[datetime],
    ) -> None:
        unique_times = sorted(
            {
                run_time.isoformat(
                    timespec="minutes"
                )
                for run_time in run_times
            }
        )

        if not unique_times:
            raise ValueError(
                "run_times cannot be empty."
            )

        with self.database.connect() as connection:
            for run_time in unique_times:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO divar_daily_schedule (
                        schedule_date,
                        run_time,
                        executed,
                        executed_at
                    )
                    VALUES (?, ?, 0, NULL)
                    """,
                    (
                        schedule_date.isoformat(),
                        run_time,
                    ),
                )

    def list_for_date(
        self,
        schedule_date: date,
    ) -> list[ScheduledRun]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    schedule_date,
                    run_time,
                    executed,
                    executed_at
                FROM divar_daily_schedule
                WHERE schedule_date = ?
                ORDER BY run_time ASC
                """,
                (schedule_date.isoformat(),),
            ).fetchall()

        results: list[ScheduledRun] = []

        for row in rows:
            executed_at: datetime | None = None

            if row["executed_at"]:
                executed_at = datetime.fromisoformat(
                    str(row["executed_at"])
                )

            results.append(
                ScheduledRun(
                    schedule_date=date.fromisoformat(
                        str(row["schedule_date"])
                    ),
                    scheduled_for=datetime.fromisoformat(
                        str(row["run_time"])
                    ),
                    executed=bool(
                        row["executed"]
                    ),
                    executed_at=executed_at,
                )
            )

        return results

    def mark_executed(
        self,
        scheduled_run: ScheduledRun,
        executed_at: datetime,
    ) -> bool:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE divar_daily_schedule
                SET
                    executed = 1,
                    executed_at = ?
                WHERE schedule_date = ?
                  AND run_time = ?
                  AND executed = 0
                """,
                (
                    executed_at.isoformat(
                        timespec="seconds"
                    ),
                    scheduled_run.schedule_date.isoformat(),
                    scheduled_run.scheduled_for.isoformat(
                        timespec="minutes"
                    ),
                ),
            )

        return cursor.rowcount > 0

    def mark_stale_as_executed(
        self,
        schedule_date: date,
        before_time: datetime,
    ) -> int:
        """
        اجرای ازدست‌رفته را بسته‌شده علامت می‌زند
        تا بعداً پشت‌سرهم جبران نشود.
        """
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE divar_daily_schedule
                SET
                    executed = 1,
                    executed_at = ?
                WHERE schedule_date = ?
                  AND executed = 0
                  AND run_time < ?
                """,
                (
                    before_time.isoformat(
                        timespec="seconds"
                    ),
                    schedule_date.isoformat(),
                    before_time.isoformat(
                        timespec="minutes"
                    ),
                ),
            )

        return cursor.rowcount
