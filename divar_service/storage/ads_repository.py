from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from divar_service.models import VehicleAd
from divar_service.storage.database import Database


class AdsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def exists(self, ad_id: str) -> bool:
        clean_ad_id = ad_id.strip()

        if not clean_ad_id:
            return False

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM divar_ads
                WHERE ad_id = ?
                LIMIT 1
                """,
                (clean_ad_id,),
            ).fetchone()

        return row is not None

    def get_existing_ids(
        self,
        ad_ids: Iterable[str],
    ) -> set[str]:
        clean_ids = tuple(
            dict.fromkeys(
                ad_id.strip()
                for ad_id in ad_ids
                if ad_id and ad_id.strip()
            )
        )

        if not clean_ids:
            return set()

        placeholders = ",".join("?" for _ in clean_ids)

        query = f"""
            SELECT ad_id
            FROM divar_ads
            WHERE ad_id IN ({placeholders})
        """

        with self.database.connect() as connection:
            rows = connection.execute(
                query,
                clean_ids,
            ).fetchall()

        return {
            str(row["ad_id"])
            for row in rows
        }

    def get_by_id(
        self,
        ad_id: str,
    ) -> VehicleAd | None:
        clean_ad_id = ad_id.strip()

        if not clean_ad_id:
            return None

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    ad_id,
                    brand,
                    model,
                    year,
                    price,
                    url,
                    title
                FROM divar_ads
                WHERE ad_id = ?
                LIMIT 1
                """,
                (clean_ad_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_ad(row)

    def upsert(
        self,
        ad: VehicleAd,
    ) -> bool:
        """
        Insert or update an advertisement.

        Returns True when the ad is new.
        Returns False when the ad already existed.
        """
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM divar_ads
                WHERE ad_id = ?
                LIMIT 1
                """,
                (ad.ad_id,),
            ).fetchone()

            is_new = existing is None

            connection.execute(
                """
                INSERT INTO divar_ads (
                    ad_id,
                    brand,
                    model,
                    year,
                    price,
                    url,
                    title,
                    first_seen,
                    last_seen,
                    updated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT(ad_id) DO UPDATE SET
                    brand = excluded.brand,
                    model = excluded.model,
                    year = excluded.year,
                    price = excluded.price,
                    url = excluded.url,
                    title = excluded.title,
                    last_seen = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    ad.ad_id,
                    ad.brand,
                    ad.model,
                    ad.year,
                    ad.price,
                    ad.url,
                    ad.title,
                ),
            )

        return is_new

    def upsert_many(
        self,
        ads: Iterable[VehicleAd],
    ) -> int:
        new_ads_count = 0

        for ad in ads:
            if self.upsert(ad):
                new_ads_count += 1

        return new_ads_count

    def list_recent(
        self,
        retention_days: int = 7,
    ) -> list[VehicleAd]:
        if retention_days < 1:
            raise ValueError(
                "retention_days must be at least 1."
            )

        modifier = f"-{retention_days} days"

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ad_id,
                    brand,
                    model,
                    year,
                    price,
                    url,
                    title
                FROM divar_ads
                WHERE last_seen >= datetime('now', ?)
                ORDER BY last_seen DESC
                """,
                (modifier,),
            ).fetchall()

        return [
            self._row_to_ad(row)
            for row in rows
        ]

    def list_for_vehicle_key(
        self,
        brand: str,
        model: str,
        year: int,
        retention_days: int = 7,
    ) -> list[VehicleAd]:
        if retention_days < 1:
            raise ValueError(
                "retention_days must be at least 1."
            )

        modifier = f"-{retention_days} days"

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ad_id,
                    brand,
                    model,
                    year,
                    price,
                    url,
                    title
                FROM divar_ads
                WHERE brand = ?
                  AND model = ?
                  AND year = ?
                  AND last_seen >= datetime('now', ?)
                ORDER BY price ASC
                """,
                (
                    brand.strip(),
                    model.strip(),
                    year,
                    modifier,
                ),
            ).fetchall()

        return [
            self._row_to_ad(row)
            for row in rows
        ]

    def touch(self, ad_id: str) -> bool:
        clean_ad_id = ad_id.strip()

        if not clean_ad_id:
            return False

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE divar_ads
                SET
                    last_seen = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE ad_id = ?
                """,
                (clean_ad_id,),
            )

        return cursor.rowcount > 0

    @staticmethod
    def _row_to_ad(
        row: sqlite3.Row,
    ) -> VehicleAd:
        return VehicleAd(
            ad_id=str(row["ad_id"]),
            brand=str(row["brand"]),
            model=str(row["model"]),
            year=int(row["year"]),
            price=int(row["price"]),
            url=str(row["url"]),
            title=str(row["title"]),
        )
