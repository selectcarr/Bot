from __future__ import annotations

import random
from datetime import (
    date,
    datetime,
    time,
    timedelta,
)
from zoneinfo import ZoneInfo

from divar_service.config.settings import Settings
from divar_service.storage.schedule_repository import (
    ScheduledRun,
    ScheduleRepository,
)


class DailyScheduler:
    def __init__(
        self,
        settings: Settings,
        repository: ScheduleRepository,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.timezone = ZoneInfo(
            settings.timezone
        )
        self.random = random.SystemRandom()

    def get_local_now(self) -> datetime:
        return datetime.now(
            self.timezone
        )

    def get_schedule_date(
        self,
        now: datetime | None = None,
    ) -> date:
        """
        زمان‌های ۰۰:۰۰ تا ۰۰:۳۰ متعلق به برنامه
        روز قبل محسوب می‌شوند.
        """
        local_now = self._to_local(now)
        end_time = self._parse_time(
            self.settings.schedule_end
        )

        if local_now.time() <= end_time:
            return (
                local_now.date()
                - timedelta(days=1)
            )

        return local_now.date()

    def ensure_daily_schedule(
        self,
        schedule_date: date,
    ) -> list[ScheduledRun]:
        if not self.repository.has_schedule(
            schedule_date
        ):
            run_times = self._generate_run_times(
                schedule_date
            )

            self.repository.save_schedule(
                schedule_date=schedule_date,
                run_times=run_times,
            )

        return self.repository.list_for_date(
            schedule_date
        )

    def get_due_run(
        self,
        now: datetime | None = None,
        grace_minutes: int = 35,
    ) -> ScheduledRun | None:
        """
        اگر یکی از زمان‌های امروز رسیده باشد،
        همان اجرا را برمی‌گرداند.

        مهلت ۳۵ دقیقه‌ای برای تأخیر احتمالی
        GitHub Actions در نظر گرفته شده است.
        """
        if grace_minutes < 1:
            raise ValueError(
                "grace_minutes must be positive."
            )

        local_now = self._to_local(now)
        schedule_date = self.get_schedule_date(
            local_now
        )

        scheduled_runs = (
            self.ensure_daily_schedule(
                schedule_date
            )
        )

        oldest_allowed_time = (
            local_now
            - timedelta(
                minutes=grace_minutes
            )
        )

        self.repository.mark_stale_as_executed(
            schedule_date=schedule_date,
            before_time=oldest_allowed_time,
        )

        scheduled_runs = (
            self.repository.list_for_date(
                schedule_date
            )
        )

        for scheduled_run in scheduled_runs:
            if scheduled_run.executed:
                continue

            if (
                oldest_allowed_time
                <= scheduled_run.scheduled_for
                <= local_now
            ):
                return scheduled_run

        return None

    def mark_executed(
        self,
        scheduled_run: ScheduledRun,
        now: datetime | None = None,
    ) -> bool:
        return self.repository.mark_executed(
            scheduled_run=scheduled_run,
            executed_at=self._to_local(now),
        )

    def _generate_run_times(
        self,
        schedule_date: date,
    ) -> list[datetime]:
        start_time = self._parse_time(
            self.settings.schedule_start
        )
        end_time = self._parse_time(
            self.settings.schedule_end
        )

        start_datetime = datetime.combine(
            schedule_date,
            start_time,
            tzinfo=self.timezone,
        )

        end_date = schedule_date

        if end_time <= start_time:
            end_date = (
                schedule_date
                + timedelta(days=1)
            )

        end_datetime = datetime.combine(
            end_date,
            end_time,
            tzinfo=self.timezone,
        )

        total_minutes = int(
            (
                end_datetime
                - start_datetime
            ).total_seconds()
            // 60
        )

        selected_offsets: list[int] = []
        attempts = 0

        while (
            len(selected_offsets)
            < self.settings.runs_per_day
        ):
            attempts += 1

            if attempts > 20_000:
                raise RuntimeError(
                    "Could not generate a valid "
                    "daily schedule."
                )

            candidate = self.random.randint(
                0,
                total_minutes,
            )

            has_enough_gap = all(
                abs(
                    candidate
                    - existing_offset
                )
                >= self.settings.minimum_gap_minutes
                for existing_offset
                in selected_offsets
            )

            if not has_enough_gap:
                continue

            selected_offsets.append(
                candidate
            )

        selected_offsets.sort()

        return [
            start_datetime
            + timedelta(
                minutes=offset
            )
            for offset in selected_offsets
        ]

    def _to_local(
        self,
        value: datetime | None,
    ) -> datetime:
        if value is None:
            return self.get_local_now()

        if value.tzinfo is None:
            return value.replace(
                tzinfo=self.timezone
            )

        return value.astimezone(
            self.timezone
        )

    @staticmethod
    def _parse_time(
        value: str,
    ) -> time:
        try:
            hour_text, minute_text = (
                value.strip().split(
                    ":",
                    maxsplit=1,
                )
            )

            hour = int(hour_text)
            minute = int(minute_text)

            return time(
                hour=hour,
                minute=minute,
            )

        except (
            ValueError,
            TypeError,
        ) as exc:
            raise ValueError(
                f"Invalid time value: {value}"
            ) from exc
