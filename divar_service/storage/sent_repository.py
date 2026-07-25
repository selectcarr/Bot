from __future__ import annotations

from collections.abc import Iterable

from divar_service.models import DealCandidate
from divar_service.storage.database import Database


class SentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def was_sent(self, ad_id: str) -> bool:
        clean_ad_id = ad_id.strip()

        if not clean_ad_id:
            return False

        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM divar_sent_ads
                WHERE ad_id = ?
                LIMIT 1
                """,
                (clean_ad_id,),
            ).fetchone()

        return row is not None

    def get_sent_ids(
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

        placeholders = ",".join(
            "?" for _ in clean_ids
        )

        query = f"""
            SELECT ad_id
            FROM divar_sent_ads
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

    def mark_sent(
        self,
        deal: DealCandidate,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO divar_sent_ads (
                    ad_id,
                    price,
                    market_average,
                    diff_percent,
                    sent_at
                )
                VALUES (
                    ?, ?, ?, ?, CURRENT_TIMESTAMP
                )
                ON CONFLICT(ad_id) DO UPDATE SET
                    price = excluded.price,
                    market_average = excluded.market_average,
                    diff_percent = excluded.diff_percent,
                    sent_at = CURRENT_TIMESTAMP
                """,
                (
                    deal.ad.ad_id,
                    deal.ad.price,
                    deal.market_average,
                    deal.diff_percent,
                ),
            )

    def remove(self, ad_id: str) -> bool:
        clean_ad_id = ad_id.strip()

        if not clean_ad_id:
            return False

        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM divar_sent_ads
                WHERE ad_id = ?
                """,
                (clean_ad_id,),
            )

        return cursor.rowcount > 0
