from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from divar_service.models import (
    VehicleAd,
)
from divar_service.storage.database import (
    Database,
)


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
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM divar_ads
                WHERE ad_id = ?
                LIMIT 1
                """,
                (ad_id,),
            ).fetchone()

        return row is not None

    def touch(
        self,
        ad_id: str,
    ) -> None:
        now = self._now()

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE divar_ads
                SET
                    last_seen = ?,
                    updated_at = ?
                WHERE ad_id = ?
                """,
                (
                    now,
                    now,
                    ad_id,
                ),
            )

    def upsert(
        self,
        ad: VehicleAd,
    ) -> bool:
        """
        Insert or update one active advertisement.

        Returns:
            True  -> a new advertisement was inserted.
            False -> an existing advertisement was updated.
        """
        now = self._now()

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

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO divar_ads (
                        ad_id,
                        brand,
                        model,
                        trim,
                        year,
                        price,
                        url,
                        title,
                        first_seen,
                        last_seen,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ad.ad_id,
                        ad.brand,
                        ad.model,
                        ad.trim,
                        ad.year,
                        ad.price,
                        ad.url,
                        ad.title,
                        now,
                        now,
                        now,
                    ),
                )

                return True

            connection.execute(
                """
                UPDATE divar_ads
                SET
                    brand = ?,
                    model = ?,
                    trim = ?,
                    year = ?,
                    price = ?,
                    url = ?,
                    title = ?,
                    last_seen = ?,
                    updated_at = ?
                WHERE ad_id = ?
                """,
                (
                    ad.brand,
                    ad.model,
                    ad.trim,
                    ad.year,
                    ad.price,
                    ad.url,
                    ad.title,
                    now,
                    now,
                    ad.ad_id,
                ),
            )

        return False

    def list_recent(
        self,
        retention_days: int,
    ) -> list[VehicleAd]:
        if retention_days < 1:
            raise ValueError(
                "retention_days must be at least 1."
            )

        cutoff = (
            datetime.now(
                timezone.utc
            )
            - timedelta(
                days=retention_days
            )
        ).isoformat()

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    ad_id,
                    brand,
                    model,
                    trim,
                    year,
                    price,
                    url,
                    title
                FROM divar_ads
                WHERE datetime(last_seen)
                    >= datetime(?)
                ORDER BY datetime(first_seen) DESC
                """,
                (cutoff,),
            ).fetchall()

        return [
            self._row_to_vehicle_ad(row)
            for row in rows
        ]

    def get(
        self,
        ad_id: str,
    ) -> VehicleAd | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    ad_id,
                    brand,
                    model,
                    trim,
                    year,
                    price,
                    url,
                    title
                FROM divar_ads
                WHERE ad_id = ?
                LIMIT 1
                """,
                (ad_id,),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_vehicle_ad(
            row
        )

    def count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM divar_ads
                """
            ).fetchone()

        if row is None:
            return 0

        return int(
            row[0]
        )

    @staticmethod
    def _row_to_vehicle_ad(
        row,
    ) -> VehicleAd:
        return VehicleAd(
            ad_id=str(
                row["ad_id"]
            ),
            brand=str(
                row["brand"]
            ),
            model=str(
                row["model"]
            ),
            trim=str(
                row["trim"] or ""
            ),
            year=int(
                row["year"]
            ),
            price=int(
                row["price"]
            ),
            url=str(
                row["url"]
            ),
            title=str(
                row["title"] or ""
            ),
            mileage=None,
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(
            timezone.utc
        ).isoformat()
