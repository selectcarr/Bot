from __future__ import annotations

from datetime import datetime

from divar_service.storage.database import Database


class ExecutionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        scheduled_for: datetime | None = None,
    ) -> int:
        scheduled_value = (
            scheduled_for.isoformat(
                timespec="seconds"
            )
            if scheduled_for is not None
            else None
        )

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO divar_execution_history (
                    scheduled_for,
                    started_at,
                    status,
                    ads_seen,
                    ads_saved,
                    deals_found
                )
                VALUES (
                    ?,
                    CURRENT_TIMESTAMP,
                    'started',
                    0,
                    0,
                    0
                )
                """,
                (scheduled_value,),
            )

            execution_id = cursor.lastrowid

        if execution_id is None:
            raise RuntimeError(
                "Could not create execution history."
            )

        return int(execution_id)

    def finish(
        self,
        execution_id: int,
        status: str,
        ads_seen: int = 0,
        ads_saved: int = 0,
        deals_found: int = 0,
        error_message: str | None = None,
    ) -> None:
        clean_status = status.strip()

        if not clean_status:
            raise ValueError(
                "status cannot be empty."
            )

        safe_error = None

        if error_message:
            safe_error = str(
                error_message
            )[:2000]

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE divar_execution_history
                SET
                    finished_at = CURRENT_TIMESTAMP,
                    status = ?,
                    ads_seen = ?,
                    ads_saved = ?,
                    deals_found = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    clean_status,
                    max(0, ads_seen),
                    max(0, ads_saved),
                    max(0, deals_found),
                    safe_error,
                    execution_id,
                ),
            )
