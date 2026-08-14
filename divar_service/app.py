from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from divar_service.analyzer import DealAnalyzer
from divar_service.collector import (
    CollectionResult,
    IncrementalCollector,
)
from divar_service.config.settings import (
    Settings,
    load_settings,
)
from divar_service.divar_client import (
    DivarBlockedError,
    DivarClient,
)
from divar_service.execution_lock import (
    AlreadyRunningError,
    ExecutionLock,
)
from divar_service.scheduler import DailyScheduler
from divar_service.storage.ads_repository import (
    AdsRepository,
)
from divar_service.storage.database import Database
from divar_service.storage.execution_repository import (
    ExecutionRepository,
)
from divar_service.storage.schedule_repository import (
    ScheduleRepository,
    ScheduledRun,
)
from divar_service.storage.sent_repository import (
    SentRepository,
)
from divar_service.telegram_sender import (
    TelegramSender,
)


LOGGER = logging.getLogger(
    "divar_service"
)

VALID_ACTIONS = {
    "scheduled",
    "force",
    "telegram_test",
}


def main() -> None:
    settings = load_settings()

    configure_logging(
        settings
    )

    database = Database(
        settings.database_path
    )

    database.initialize()

    database.cleanup_old_data(
        settings.retention_days
    )

    action = os.getenv(
        "DIVAR_ACTION",
        "scheduled",
    ).strip().lower()

    if action not in VALID_ACTIONS:
        raise ValueError(
            f"Invalid DIVAR_ACTION: {action}"
        )

    lock_path = (
        settings.database_path.parent
        / "execution.lock"
    )

    try:
        with ExecutionLock(lock_path):
            run_service(
                settings=settings,
                database=database,
                action=action,
            )

    except AlreadyRunningError:
        LOGGER.warning(
            "Another Divar execution is active. "
            "This run will stop."
        )


def run_service(
    settings: Settings,
    database: Database,
    action: str,
) -> None:
    ads_repository = AdsRepository(
        database
    )

    sent_repository = SentRepository(
        database
    )

    schedule_repository = ScheduleRepository(
        database
    )

    execution_repository = ExecutionRepository(
        database
    )

    scheduler = DailyScheduler(
        settings=settings,
        repository=schedule_repository,
    )

    if action == "telegram_test":
        telegram_sender = TelegramSender(
            settings
        )

        result = telegram_sender.send_test_message()

        LOGGER.info(
            "Telegram test finished. "
            "sent=%s dry_run=%s message_id=%s",
            result.sent,
            result.dry_run,
            result.message_id,
        )

        return

    scheduled_run: ScheduledRun | None = None

    if action == "scheduled":
        scheduled_run = scheduler.get_due_run()

        if scheduled_run is None:
            LOGGER.info(
                "No scheduled Divar run is due."
            )
            return

        claimed = scheduler.mark_executed(
            scheduled_run
        )

        if not claimed:
            LOGGER.info(
                "Scheduled run was already executed."
            )
            return

        LOGGER.info(
            "Scheduled run claimed: %s",
            scheduled_run.scheduled_for,
        )

    execution_id = execution_repository.start(
        scheduled_for=(
            scheduled_run.scheduled_for
            if scheduled_run is not None
            else None
        )
    )

    collection_result: CollectionResult | None = None
    deals_found = 0

    try:
        with DivarClient(settings) as divar_client:
            collector = IncrementalCollector(
                settings=settings,
                client=divar_client,
                ads_repository=ads_repository,
            )

            collection_result = collector.collect()

        LOGGER.info(
            (
                "Collection finished: "
                "pages=%s seen=%s saved=%s "
                "rejected=%s duplicate=%s "
                "blocked=%s stop_reason=%s"
            ),
            collection_result.pages_requested,
            collection_result.ads_seen,
            collection_result.ads_saved,
            collection_result.ads_rejected,
            collection_result.duplicate_found,
            collection_result.blocked,
            collection_result.stop_reason,
        )

        if collection_result.blocked:
            closed_runs = _close_schedule_after_block(
                scheduler=scheduler,
                scheduled_run=scheduled_run,
            )

            execution_repository.finish(
                execution_id=execution_id,
                status="blocked",
                ads_seen=collection_result.ads_seen,
                ads_saved=collection_result.ads_saved,
                deals_found=0,
                error_message=(
                    collection_result.error_message
                ),
            )

            LOGGER.warning(
                (
                    "Divar blocking response detected. "
                    "Run stopped and remaining daily "
                    "runs were closed | closed_runs=%s | "
                    "error=%s"
                ),
                closed_runs,
                collection_result.error_message,
            )

            return

        recent_ads = ads_repository.list_recent(
            retention_days=(
                settings.retention_days
            )
        )

        analyzer = DealAnalyzer(
            settings=settings,
            sent_repository=sent_repository,
        )

        candidates = analyzer.analyze(
            ads=recent_ads,
            current_ad_ids=set(
                collection_result.current_ad_ids
            ),
        )

        deals_found = len(
            candidates
        )

        LOGGER.info(
            "Deal candidates found: %s",
            deals_found,
        )

        best_deal = analyzer.select_best(
            candidates
        )

        final_status = (
            "dry_run"
            if settings.dry_run
            else "success"
        )

        if best_deal is None:
            LOGGER.info(
                "No valid deal was found."
            )

        else:
            telegram_sender = TelegramSender(
                settings
            )

            send_result = telegram_sender.send_deal(
                best_deal
            )

            LOGGER.info(
                (
                    "Telegram result: "
                    "sent=%s dry_run=%s "
                    "message_id=%s"
                ),
                send_result.sent,
                send_result.dry_run,
                send_result.message_id,
            )

            if send_result.sent:
                sent_repository.mark_sent(
                    best_deal
                )

        execution_repository.finish(
            execution_id=execution_id,
            status=final_status,
            ads_seen=collection_result.ads_seen,
            ads_saved=collection_result.ads_saved,
            deals_found=deals_found,
        )

    except DivarBlockedError as exc:
        closed_runs = _close_schedule_after_block(
            scheduler=scheduler,
            scheduled_run=scheduled_run,
        )

        execution_repository.finish(
            execution_id=execution_id,
            status="blocked",
            ads_seen=(
                collection_result.ads_seen
                if collection_result
                else 0
            ),
            ads_saved=(
                collection_result.ads_saved
                if collection_result
                else 0
            ),
            deals_found=deals_found,
            error_message=str(exc),
        )

        LOGGER.warning(
            (
                "Divar blocked or verification response "
                "detected. Run stopped | closed_runs=%s | "
                "error=%s"
            ),
            closed_runs,
            exc,
        )

    except Exception as exc:
        execution_repository.finish(
            execution_id=execution_id,
            status="failed",
            ads_seen=(
                collection_result.ads_seen
                if collection_result
                else 0
            ),
            ads_saved=(
                collection_result.ads_saved
                if collection_result
                else 0
            ),
            deals_found=deals_found,
            error_message=str(exc),
        )

        LOGGER.exception(
            "Divar service failed."
        )

        raise


def _close_schedule_after_block(
    scheduler: DailyScheduler,
    scheduled_run: ScheduledRun | None,
) -> int:
    schedule_date = (
        scheduled_run.schedule_date
        if scheduled_run is not None
        else scheduler.get_schedule_date()
    )

    return scheduler.close_remaining_runs(
        schedule_date
    )


def configure_logging(
    settings: Settings,
) -> None:
    settings.log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(
        logging.INFO
    )

    if root_logger.handlers:
        return

    formatter = logging.Formatter(
        (
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        )
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        formatter
    )

    file_handler = RotatingFileHandler(
        settings.log_path,
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )

    file_handler.setFormatter(
        formatter
    )

    root_logger.addHandler(
        console_handler
    )

    root_logger.addHandler(
        file_handler
    )


if __name__ == "__main__":
    main()
