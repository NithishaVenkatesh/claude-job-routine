"""Send-gate guardrails — every check that stands between a draft and a real send."""
import unittest

from jobhunter.models import JobPosting
from jobhunter.validate import validate


class FakeDB:
    """Minimal stand-in exposing exactly what validate() touches."""

    def __init__(self, suppressed=(), email_dup=False, company_count=0,
                 sent_today=0, interview_prob=None):
        self._suppressed = set(suppressed)
        self._dup = email_dup
        self._company_count = company_count
        self._sent_today = sent_today
        self._prob = interview_prob

    def is_suppressed(self, email):
        return email in self._suppressed

    def email_exists(self, contact_id, job_hash):
        return self._dup

    def company_email_count_today(self, company):
        return self._company_count

    def emails_sent_today(self):
        return self._sent_today

    def get_interview_prob(self, content_hash):
        return self._prob


def _job(location="Remote"):
    # url="" so the liveness probe (network) is skipped in tests
    return JobPosting(source="t", source_company="t", external_id="1",
                      title="AI Engineer", company="Acme", url="", location=location)


def _contact(status="verified", confidence=0.95):
    return {"id": 1, "email": "jane@acme.com", "full_name": "Jane Roe",
            "verification_status": status, "confidence": confidence}


def _draft():
    return {"subject": "Hello", "body": "A real body with no placeholders."}


RULES = {"verified_email_only": True, "allow_high_confidence": True,
         "min_confidence": 0.90, "per_company_cap": 1, "daily_send_cap": 6,
         "exclude_regions": ["EU", "UK", "CA"], "min_interview_prob_to_send": 0.70}


class TestValidate(unittest.TestCase):
    def test_verified_contact_passes(self):
        v = validate(FakeDB(), RULES, _job(), _contact(), _draft())
        self.assertTrue(v.ok, v.reasons)

    def test_unverified_low_confidence_fails(self):
        v = validate(FakeDB(), RULES, _job(),
                     _contact(status="unknown", confidence=0.85), _draft())
        self.assertFalse(v.ok)

    def test_unverified_high_confidence_passes(self):
        v = validate(FakeDB(), RULES, _job(),
                     _contact(status="unknown", confidence=0.93), _draft())
        self.assertTrue(v.ok, v.reasons)

    def test_suppressed_email_fails(self):
        v = validate(FakeDB(suppressed={"jane@acme.com"}), RULES, _job(),
                     _contact(), _draft())
        self.assertFalse(v.ok)

    def test_duplicate_contact_job_fails(self):
        v = validate(FakeDB(email_dup=True), RULES, _job(), _contact(), _draft())
        self.assertFalse(v.ok)
        self.assertTrue(any("already emailed" in r for r in v.reasons))

    def test_per_company_cap_fails(self):
        v = validate(FakeDB(company_count=1), RULES, _job(), _contact(), _draft())
        self.assertFalse(v.ok)

    def test_daily_cap_fails(self):
        v = validate(FakeDB(sent_today=6), RULES, _job(), _contact(), _draft())
        self.assertFalse(v.ok)
        self.assertTrue(any("daily send cap" in r for r in v.reasons))

    def test_excluded_region_fails(self):
        v = validate(FakeDB(), RULES, _job(location="London, UK"), _contact(), _draft())
        self.assertFalse(v.ok)

    def test_unfilled_placeholder_fails(self):
        d = {"subject": "Hi", "body": "Hi [Name], I loved [Company]'s work."}
        v = validate(FakeDB(), RULES, _job(), _contact(), d)
        self.assertFalse(v.ok)
        self.assertTrue(any("placeholder" in r for r in v.reasons))

    def test_empty_body_fails(self):
        v = validate(FakeDB(), RULES, _job(), _contact(),
                     {"subject": "Hi", "body": "   "})
        self.assertFalse(v.ok)

    def test_low_interview_prob_fails(self):
        v = validate(FakeDB(interview_prob=0.40), RULES, _job(), _contact(), _draft())
        self.assertFalse(v.ok)
        self.assertTrue(any("probability" in r for r in v.reasons))

    def test_prob_floor_skipped_when_unscored(self):
        v = validate(FakeDB(interview_prob=None), RULES, _job(), _contact(), _draft())
        self.assertTrue(v.ok, v.reasons)

    def test_blocklisted_domain_fails(self):
        rules = {**RULES, "blocklist_domains": ["acme.com"]}
        v = validate(FakeDB(), rules, _job(), _contact(), _draft())
        self.assertFalse(v.ok)


if __name__ == "__main__":
    unittest.main()
