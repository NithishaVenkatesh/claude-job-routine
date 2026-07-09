"""Scorer hard rules: salary parsing, rejects, and the recency/seniority discounts."""
import unittest
from datetime import datetime, timedelta, timezone

from jobhunter.models import JobPosting
from jobhunter.profile import Profile
from jobhunter.score import heuristic_score, parse_salary_lpa


def _profile():
    return Profile(cfg={
        "name": "Test Candidate",
        "target_roles": ["ai engineer", "backend engineer", "software engineer"],
        "reject_role_keywords": ["sales", "marketing"],
        "too_senior_keywords": ["staff", "principal"],
        "skills_core": ["python", "fastapi", "rag"],
        "skills_secondary": ["docker", "postgres"],
        "locations": ["bangalore", "remote", "india"],
        "dealbreakers": {"max_years_required": 4},
        "salary": {"min_lpa": 6, "max_lpa": 16},
    })


def _job(title="AI Engineer", desc="", hours_old=2, location="Remote", remote=True):
    return JobPosting(source="t", source_company="t", external_id="1",
                      title=title, company="Acme", url="https://x/1",
                      location=location, remote=remote, description=desc,
                      posted_at=datetime.now(timezone.utc) - timedelta(hours=hours_old))


class TestSalaryParser(unittest.TestCase):
    def test_range(self):
        self.assertEqual(parse_salary_lpa("CTC 6-12 LPA"), (6.0, 12.0))

    def test_single_value(self):
        self.assertEqual(parse_salary_lpa("pays ₹8 lakhs"), (8.0, 8.0))

    def test_lacs_spelling(self):
        self.assertEqual(parse_salary_lpa("10 lacs per annum"), (10.0, 10.0))

    def test_reversed_range_swaps(self):
        self.assertEqual(parse_salary_lpa("12 to 6 LPA"), (6.0, 12.0))

    def test_unstated_or_usd_is_none(self):
        self.assertIsNone(parse_salary_lpa("competitive salary, $120k-$150k"))

    def test_absurd_parse_distrusted(self):
        self.assertIsNone(parse_salary_lpa("founded 120 lakhs ago"))


class TestHardRejects(unittest.TestCase):
    def setUp(self):
        self.p = _profile()

    def test_over_seniority_rejected(self):
        s = heuristic_score(_job(title="Principal Engineer"), self.p)
        self.assertTrue(s.rejected)
        self.assertIn("over-seniority", s.reject_reason)

    def test_non_target_role_rejected(self):
        s = heuristic_score(_job(title="Sales Engineer"), self.p)
        self.assertTrue(s.rejected)

    def test_too_many_years_rejected(self):
        s = heuristic_score(_job(desc="requires 8+ years of experience"), self.p)
        self.assertTrue(s.rejected)
        self.assertIn("yrs", s.reject_reason)

    def test_salary_below_minimum_rejected(self):
        s = heuristic_score(_job(desc="CTC 4-5 LPA, python fastapi"), self.p)
        self.assertTrue(s.rejected)
        self.assertIn("below", s.reject_reason)

    def test_salary_above_range_not_rejected(self):
        s = heuristic_score(_job(desc="python fastapi rag; pays 30-40 LPA"), self.p)
        self.assertFalse(s.rejected)

    def test_stale_mediocre_rejected(self):
        s = heuristic_score(_job(hours_old=24 * 10), self.p)  # >7 days, weak skills
        self.assertTrue(s.rejected)
        self.assertIn("stale", s.reject_reason)

    def test_generic_title_without_domain_rejected(self):
        s = heuristic_score(_job(title="Solutions Engineer",
                                 desc="python fastapi rag docker"), self.p)
        self.assertTrue(s.rejected)


class TestPositiveScoring(unittest.TestCase):
    def setUp(self):
        self.p = _profile()

    def test_fresh_matching_job_shortlists(self):
        s = heuristic_score(
            _job(desc="We use python, fastapi, rag pipelines, docker and postgres."), self.p)
        self.assertFalse(s.rejected)
        self.assertGreater(s.interview_prob, 0.5)
        self.assertEqual(s.dimensions["recency"], "24h")

    def test_senior_title_discounted_not_rejected(self):
        fresh = heuristic_score(_job(desc="python fastapi rag"), self.p)
        senior = heuristic_score(_job(title="Senior AI Engineer",
                                      desc="python fastapi rag"), self.p)
        self.assertFalse(senior.rejected)
        self.assertLess(senior.interview_prob, fresh.interview_prob)

    def test_fresher_post_outranks_older_identical(self):
        fresh = heuristic_score(_job(hours_old=2, desc="python fastapi rag"), self.p)
        old = heuristic_score(_job(hours_old=100, desc="python fastapi rag"), self.p)
        self.assertGreater(fresh.interview_prob, old.interview_prob)


if __name__ == "__main__":
    unittest.main()
