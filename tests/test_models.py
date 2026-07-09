"""Dedup hash + recency buckets — the duplicate-prevention foundation."""
import unittest
from datetime import datetime, timedelta, timezone

from jobhunter.models import JobPosting


def _job(**kw):
    base = dict(source="greenhouse", source_company="acme", external_id="1",
                title="AI Engineer", company="Acme", url="https://x/1")
    base.update(kw)
    return JobPosting(**base)


class TestContentHash(unittest.TestCase):
    def test_stable_across_case_and_whitespace(self):
        a = _job(company="Acme", title="AI Engineer", location="Remote")
        b = _job(company="  acme ", title="ai  engineer", location="remote",
                 source="lever", external_id="999", url="https://y/2")
        self.assertEqual(a.content_hash(), b.content_hash())

    def test_differs_when_location_differs(self):
        a = _job(location="Remote")
        b = _job(location="Bangalore")
        self.assertNotEqual(a.content_hash(), b.content_hash())


class TestRecencyBucket(unittest.TestCase):
    def test_buckets(self):
        now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
        cases = [(12, "24h"), (36, "48h"), (60, "72h"), (100, "7d"), (200, "older")]
        for hours, want in cases:
            j = _job(posted_at=now - timedelta(hours=hours))
            self.assertEqual(j.recency_bucket(now), want, f"{hours}h")
        self.assertEqual(_job(posted_at=None).recency_bucket(now), "unknown")


if __name__ == "__main__":
    unittest.main()
