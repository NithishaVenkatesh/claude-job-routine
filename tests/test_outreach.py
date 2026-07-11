"""Outreach selection + template fill: freshest-first slots and clean rendering."""
import unittest
from datetime import datetime, timedelta, timezone

from jobhunter.outreach import (_bucket_of, _clean_company, _first_name,
                                _freshest_first, _pretty_company)


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


class TestBuckets(unittest.TestCase):
    def test_bucket_of(self):
        self.assertEqual(_bucket_of(_iso(5)), "24h")
        self.assertEqual(_bucket_of(_iso(30)), "48h")
        self.assertEqual(_bucket_of(_iso(60)), "72h")
        self.assertEqual(_bucket_of(_iso(150)), "7d")
        self.assertEqual(_bucket_of(_iso(200)), "older")
        self.assertEqual(_bucket_of(None), "unknown")
        self.assertEqual(_bucket_of("not-a-date"), "unknown")


class TestFreshestFirst(unittest.TestCase):
    def test_fills_24h_slots_before_older(self):
        rows = [{"id": "old", "posted_at": _iso(150)},
                {"id": "fresh1", "posted_at": _iso(3)},
                {"id": "mid", "posted_at": _iso(30)},
                {"id": "fresh2", "posted_at": _iso(10)}]
        picked = _freshest_first(rows, limit=3)
        self.assertEqual([r["id"] for r in picked], ["fresh1", "fresh2", "mid"])

    def test_never_includes_stale(self):
        rows = [{"id": "stale", "posted_at": _iso(24 * 10)},
                {"id": "fresh", "posted_at": _iso(3)}]
        picked = _freshest_first(rows, limit=5)
        self.assertEqual([r["id"] for r in picked], ["fresh"])

    def test_respects_limit(self):
        rows = [{"id": str(i), "posted_at": _iso(2)} for i in range(10)]
        self.assertEqual(len(_freshest_first(rows, limit=4)), 4)

    def test_undated_leads_fill_last_but_are_never_dropped(self):
        """Speculative/founder-post leads carry no posting date. They must rank after
        every dated-fresh lead but survive selection (they used to be silently dropped)."""
        rows = [{"id": "undated", "posted_at": None},
                {"id": "fresh", "posted_at": _iso(3)},
                {"id": "week", "posted_at": _iso(150)}]
        picked = _freshest_first(rows, limit=5)
        self.assertEqual([r["id"] for r in picked], ["fresh", "week", "undated"])

    def test_undated_leads_still_yield_to_limit(self):
        rows = [{"id": "undated", "posted_at": None},
                {"id": "fresh1", "posted_at": _iso(3)},
                {"id": "fresh2", "posted_at": _iso(5)}]
        picked = _freshest_first(rows, limit=2)
        self.assertEqual([r["id"] for r in picked], ["fresh1", "fresh2"])


class TestTemplateFill(unittest.TestCase):
    def test_brand_casing(self):
        self.assertEqual(_pretty_company("openai"), "OpenAI")
        self.assertEqual(_pretty_company("phonepe"), "PhonePe")
        self.assertEqual(_pretty_company("acme labs"), "Acme Labs")
        self.assertEqual(_pretty_company("Sarvam AI"), "Sarvam AI")  # untouched

    def test_yc_tags_stripped(self):
        self.assertEqual(_clean_company("Netradyne (YC W18)"), "Netradyne")
        self.assertEqual(_clean_company("openai (Series C)"), "OpenAI")
        self.assertEqual(_clean_company("Plain Co"), "Plain Co")

    def test_first_name(self):
        self.assertEqual(_first_name("Deepesh Pawa"), "Deepesh")
        self.assertEqual(_first_name(""), "there")
        self.assertEqual(_first_name("  "), "there")


if __name__ == "__main__":
    unittest.main()
