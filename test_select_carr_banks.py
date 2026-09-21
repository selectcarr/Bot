from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from select_carr_banks import BuyerRequestInput, DealSnapshot, SelectCarrBanks


class BanksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "business.db"
        self.db = SelectCarrBanks(self.path)
        self.db.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def deal(self, **changes):
        d = DealSnapshot(
            source_key="divar:abc", source="divar", source_ad_id="abc",
            url="https://example.test/a", title="Peugeot 207",
            brand="Peugeot", model="207", trim="automatic", model_year=1402,
            condition="used", body_condition="clean", mileage=12000,
            price=1_000_000_000, average_price=1_100_000_000,
            discount_percent=9.09, sample_count=5, availability="active",
        )
        return replace(d, **changes)

    def test_deal_history_is_append_only(self):
        did, state = self.db.record_deal(self.deal())
        self.assertEqual(state, "inserted")
        _, state = self.db.record_deal(self.deal(price=980_000_000, discount_percent=10.91))
        self.assertEqual(state, "changed")
        with self.db.connect() as con:
            hist = con.execute("SELECT * FROM sc_deal_history WHERE deal_id=? ORDER BY id", (did,)).fetchall()
            self.assertEqual([r["event_type"] for r in hist], ["detected","changed"])
            self.assertEqual(hist[0]["price"], 1_000_000_000)
            self.assertEqual(hist[1]["price"], 980_000_000)

    def test_same_deal_seen_does_not_spam_history(self):
        did, _ = self.db.record_deal(self.deal())
        self.db.record_deal(self.deal())
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sc_deal_history WHERE deal_id=?", (did,)).fetchone()[0], 1)

    def test_published_deal_remains_in_history(self):
        did, _ = self.db.record_deal(self.deal())
        self.db.mark_deal_published(did, "telegram", 123)
        self.db.mark_deal_lifecycle(did, "sold", reason="manual")
        with self.db.connect() as con:
            row = con.execute("SELECT * FROM sc_deals WHERE deal_id=?", (did,)).fetchone()
            self.assertEqual(row["lifecycle_status"], "sold")
            self.assertEqual(row["telegram_message_id"], 123)
            self.assertGreaterEqual(con.execute("SELECT COUNT(*) FROM sc_deal_history WHERE deal_id=?", (did,)).fetchone()[0], 3)

    def test_buyer_default_30_day_expiry(self):
        rid, tracking, bid = self.db.create_buyer_request(BuyerRequestInput(
            phone="09121234567", city="Tehran", desired_models=["207", "Tara"], max_budget=2_000_000_000,
            source="instagram", campaign_code="story-01", content_code="deal-207"
        ))
        with self.db.connect() as con:
            row = con.execute("SELECT * FROM sc_buyer_requests WHERE request_id=?", (rid,)).fetchone()
            delta = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
            self.assertTrue(29.9 <= delta.total_seconds()/86400 <= 30.1)
            self.assertTrue(tracking.startswith("REQ-"))
            self.assertTrue(bid.startswith("BU-"))

    def test_one_buyer_multiple_models_is_one_request(self):
        rid, _, _ = self.db.create_buyer_request(BuyerRequestInput(
            phone="09121234567", city="Tehran", desired_models=["207", "Tara", "207"]
        ))
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sc_buyers").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sc_buyer_requests").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sc_buyer_request_models WHERE request_id=?", (rid,)).fetchone()[0], 2)

    def test_same_phone_can_have_new_request_without_duplicate_buyer(self):
        self.db.create_buyer_request(BuyerRequestInput(phone="09121234567", city="Tehran", desired_models=["207"]))
        self.db.create_buyer_request(BuyerRequestInput(phone="+989121234567", city="Karaj", desired_models=["Dena"]))
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sc_buyers").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM sc_buyer_requests").fetchone()[0], 2)

    def test_expire_old_request(self):
        rid, _, _ = self.db.create_buyer_request(BuyerRequestInput(phone="09121234567", city="Tehran", desired_models=["207"]))
        future = (datetime.now(timezone.utc) + timedelta(days=31)).isoformat()
        self.assertEqual(self.db.expire_old_requests(future), 1)
        with self.db.connect() as con:
            self.assertEqual(con.execute("SELECT status FROM sc_buyer_requests WHERE request_id=?", (rid,)).fetchone()[0], "expired")

    def test_source_attribution_is_retained(self):
        self.db.create_buyer_request(BuyerRequestInput(
            phone="09121234567", city="Tehran", desired_models=["207"],
            source="instagram", campaign_code="reel-A", content_code="hook-3"
        ))
        summary = self.db.attribution_summary()
        self.assertEqual(summary[0]["source"], "instagram")
        self.assertEqual(summary[0]["campaign_code"], "reel-A")
        self.assertEqual(summary[0]["content_code"], "hook-3")

    def test_invalid_phone_rejected(self):
        with self.assertRaises(ValueError):
            self.db.create_buyer_request(BuyerRequestInput(phone="123", city="Tehran", desired_models=["207"]))

    def test_installment_fields_are_stored(self):
        rid, _, _ = self.db.create_buyer_request(BuyerRequestInput(
            phone="09121234567", city="Tehran", desired_models=["207"], payment_type="installment",
            down_payment=500_000_000, max_monthly_payment=50_000_000
        ))
        with self.db.connect() as con:
            row = con.execute("SELECT * FROM sc_buyer_requests WHERE request_id=?", (rid,)).fetchone()
            self.assertEqual(row["payment_type"], "installment")
            self.assertEqual(row["down_payment"], 500_000_000)


if __name__ == "__main__":
    unittest.main()
