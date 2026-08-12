from __future__ import annotations

from datetime import datetime, timedelta, timezone

from divar_service.models import VehicleAd
from divar_service.storage.database import Database


class AdsRepository:
    def __init__(
        self,
        database: Database,
    ) -> None:
        self.database = database

    def exists(
        self,
        ad_id: str,
    ) -> bool:
        row = self.database.fetchone(
            """
            SELECT 1
            FROM ads
            WHERE ad_id = ?
            LIMIT 1
            """,
            (ad_id,),
        )

        return row is not None

    def touch(
        self,
        ad_id: str,
    ) -> None:
        self.database.execute(
            """
            UPDATE ads
            SET last_seen_at = ?
            WHERE ad_id = ?
            """,
            (
                self._now(),
                ad_id,
            ),
        )

    def upsert(
        self,
        ad: VehicleAd,
    ) -> bool:
        """
        Insert a new advertisement.

        Returns:
            True  -> a new row was inserted
            False -> advertisement already existed
        """

        if self.exists(ad.ad_id):
            self.touch(ad.ad_id)
            return False

        now = self._now()

        self.database.execute(
            """
            INSERT INTO ads (
                ad_id,
                brand,
                model,
                trim,
                year,
                mileage,
                price,
                url,
                title,
                first_seen_at,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ad.ad_id,
                ad.brand,
                ad.model,
                ad.trim,
                ad.year,
                ad.mileage,
                ad.price,
                ad.url,
                ad.title,
                now,
                now,
            ),
        )

        return True

    def list_recent(
        self,
        retention_days: int,
    ) -> list[VehicleAd]:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=retention_days)
        ).isoformat()

        rows = self.database.fetchall(
            """
            SELECT
                ad_id,
                brand,
                model,
                trim,
                year,
                mileage,
                price,
                url,
                title,
                first_seen_at,
                last_seen_at
            FROM ads
            WHERE last_seen_at >= ?
            ORDER BY first_seen_at DESC
            """,
            (cutoff,),
        )

        return [
            self._row_to_vehicle_ad(row)
            for row in rows
        ]

    def get(
        self,
        ad_id: str,
    ) -> VehicleAd | None:
        row = self.database.fetchone(
            """
            SELECT
                ad_id,
                brand,
                model,
                trim,
                year,
                mileage,
                price,
                url,
                title,
                first_seen_at,
                last_seen_at
            FROM ads
            WHERE ad_id = ?
            LIMIT 1
            """,
            (ad_id,),
        )

        if row is None:
            return None

        return self._row_to_vehicle_ad(row)

    def count(self) -> int:
        row = self.database.fetchone(
            """
            SELECT COUNT(*)
            FROM ads
            """
        )

        if row is None:
            return 0

        return int(row[0])

    def delete_older_than(
        self,
        retention_days: int,
    ) -> int:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=retention_days)
        ).isoformat()

        cursor = self.database.execute(
            """
            DELETE FROM ads
            WHERE last_seen_at < ?
            """,
            (cutoff,),
        )

        return int(cursor.rowcount)

    @staticmethod
    def _row_to_vehicle_ad(
        row,
    ) -> VehicleAd:
        return VehicleAd(
            ad_id=str(row["ad_id"]),
            brand=str(row["brand"]),
            model=str(row["model"]),
            trim=str(row["trim"] or ""),
            year=int(row["year"]),
            mileage=(
                int(row["mileage"])
                if row["mileage"] is not None
                else None
            ),
            price=int(row["price"]),
            url=str(row["url"]),
            title=str(row["title"]),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(
            timezone.utc
        ).isoformat()
