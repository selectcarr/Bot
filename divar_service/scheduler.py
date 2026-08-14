from __future__ import annotations

import random
from datetime import (
    date,
    datetime,
    time,
    timedelta,
)
from zoneinfo import ZoneInfo

from divar_service.config.settings import (
    Settings,
)
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
        Times from 00:00 through 00:30 belong to the
        previous day's Divar schedule.
        """
        local_now = self._to_local(
            now
        )

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
        """
        Ensure one complete four-run schedule exists.

        A partial schedule is discarded and regenerated. The
        generated time-of-day signature must differ from the
        previous schedule date.
        """
        existing_runs = self.repository.list_for_date(
            schedule_date
        )

        if len(existing_runs) != self.settings.runs_per_day:
            if existing_runs:
                self.repository.delete_for_date(
                    schedule_date
                )

            run_times = self._generate_distinct_run_times(
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
        Return at most one due scheduled run.

        Missed runs older than the grace window are closed and
        are never executed back-to-back as compensation.
        """
        if grace_minutes < 1:
            raise ValueError(
                "grace_minutes must be positive."
            )

        local_now = self._to_local(
            now
        )

        primary_schedule_date = self.get_schedule_date(
            local_now
        )

        schedule_dates = [
            primary_schedule_date
        ]

        end_time = self._parse_time(
            self.settings.schedule_end
        )

        spillover_end = datetime.combine(
            local_now.date(),
            end_time,
            tzinfo=self.timezone,
        ) + timedelta(
            minutes=grace_minutes,
        )

        previous_date = (
            local_now.date()
            - timedelta(days=1)
        )

        if (
            local_now <= spillover_end
            and previous_date not in schedule_dates
        ):
            schedule_dates.insert(
                0,
                previous_date,
            )

        oldest_allowed_time = (
            local_now
            - timedelta(
                minutes=grace_minutes
            )
        )

        for schedule_date in schedule_dates:
            self.ensure_daily_schedule(
                schedule_date
            )

            self.repository.mark_stale_as_executed(
                schedule_date=schedule_date,
                before_time=oldest_allowed_time,
                executed_at=local_now,
            )

            scheduled_runs = self.repository.list_for_date(
                schedule_date
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
            executed_at=self._to_local(
                now
            ),
        )

    def close_remaining_runs(
        self,
        schedule_date: date,
        now: datetime | None = None,
    ) -> int:
        """
        Close every remaining run for the schedule date.
        """
        self.ensure_daily_schedule(
            schedule_date
        )

        return self.repository.mark_remaining_as_executed(
            schedule_date=schedule_date,
            executed_at=self._to_local(
                now
            ),
        )

    def _generate_distinct_run_times(
        self,
        schedule_date: date,
    ) -> list[datetime]:
        previous_runs = self.repository.list_for_date(
            schedule_date
            - timedelta(days=1)
        )

        previous_signature = self._schedule_signature(
            run.scheduled_for
            for run in previous_runs
        )

        for _ in range(500):
            run_times = self._generate_run_times(
                schedule_date
            )

            if (
                not previous_signature
                or self._schedule_signature(run_times)
                != previous_signature
            ):
                return run_times

        raise RuntimeError(
            "Could not generate a schedule different "
            "from the previous day."
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

        if total_minutes <= 0:
            raise ValueError(
                "The scheduling window is invalid."
            )

        required_span = (
            (
                self.settings.runs_per_day
                - 1
            )
            * self.settings.minimum_gap_minutes
        )

        if required_span > total_minutes:
            raise ValueError(
                "The scheduling window is too small "
                "for the requested number of runs "
                "and minimum gap."
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
    def _schedule_signature(
        run_times,
    ) -> tuple[str, ...]:
        return tuple(
            sorted(
                run_time.strftime(
                    "%H:%M"
                )
                for run_time in run_times
            )
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

            return time(
                hour=int(hour_text),
                minute=int(minute_text),
            )

        except (
            ValueError,
            TypeError,
        ) as exc:
            raise ValueError(
                f"Invalid time value: {value}"
            ) from exc
