"""Persistence invariants: dedup-forever, duplicate-email prevention, daily caps."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from jobhunter.db import DB
from jobhunter.models import JobPosting


def _job(title="AI Engineer", company="Acme", location="Remote"):
    return JobPosting(source="t", source_company="t", external_id="1",
                      title=title, company=company, url="https://x/1",
                      location=location, posted_at=datetime.now(timezone.utc))


class DBTest(unittest.TestCase):
    def setUp(self):
        # force SQLite regardless of the shell's DATABASE_URL
        self._env = os.environ.pop("DATABASE_URL", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(sqlite_path=str(Path(self.tmp.name) / "test.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
        if self._env is not None:
            os.environ["DATABASE_URL"] = self._env

    def test_bulk_upsert_dedups_forever(self):
        new, seen = self.db.bulk_upsert_jobs([_job(), _job(title="Backend Engineer")])
        self.assertEqual((len(new), seen), (2, 0))
        # same jobs again (different source/id, same company+title+location) -> all seen
        again = [_job(), _job(title="Backend Engineer")]
        new2, seen2 = self.db.bulk_upsert_jobs(again)
        self.assertEqual((len(new2), seen2), (0, 2))

    def test_batch_internal_dup_collapses(self):
        new, seen = self.db.bulk_upsert_jobs([_job(), _job()])
        self.assertEqual((len(new), seen), (1, 1))

    def test_duplicate_email_insert_returns_none(self):
        cid = self.db.save_contact({"company": "Acme", "full_name": "Jane Roe",
                                    "title": "Recruiter", "linkedin_url": None,
                                    "email": "jane@acme.com", "source_provider": "t",
                                    "confidence": 0.9, "verification_status": "verified"})
        row = {"job_hash": "h1", "contact_id": cid, "company": "Acme",
               "to_email": "jane@acme.com", "template_class": "t",
               "subject": "s", "body": "b", "hook_note": "", "status": "queued"}
        self.assertIsNotNone(self.db.save_email(dict(row)))
        self.assertIsNone(self.db.save_email(dict(row)))  # same contact+job -> blocked
        self.assertTrue(self.db.email_exists(cid, "h1"))

    def test_daily_send_cap_counting(self):
        cid = self.db.save_contact({"company": "Acme", "full_name": "J",
                                    "title": "R", "linkedin_url": None,
                                    "email": "j@acme.com", "source_provider": "t",
                                    "confidence": 0.9, "verification_status": "verified"})
        eid = self.db.save_email({"job_hash": "h1", "contact_id": cid, "company": "Acme",
                                  "to_email": "j@acme.com", "template_class": "t",
                                  "subject": "s", "body": "b", "hook_note": "",
                                  "status": "sent"})
        self.assertEqual(self.db.emails_sent_today(), 0)  # no sent_at yet
        self.db.update_email(eid, sent_at=datetime.now(timezone.utc).isoformat())
        self.assertEqual(self.db.emails_sent_today(), 1)
        self.assertEqual(self.db.company_email_count_today("Acme"), 1)

    def test_suppression(self):
        self.db.suppress("bounce@acme.com", "hard bounce")
        self.assertTrue(self.db.is_suppressed("bounce@acme.com"))
        self.assertFalse(self.db.is_suppressed("ok@acme.com"))

    def test_interview_prob_roundtrip(self):
        from jobhunter.score import Score
        j = _job()
        self.db.bulk_upsert_jobs([j])
        s = Score(content_hash=j.content_hash(), score=88.0, interview_prob=0.81)
        self.db.save_score(s, "shortlisted")
        self.assertAlmostEqual(self.db.get_interview_prob(j.content_hash()), 0.81)
        self.assertIsNone(self.db.get_interview_prob("missing"))


if __name__ == "__main__":
    unittest.main()
