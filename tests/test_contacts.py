"""Provider waterfall: key rotation on quota exhaustion + hiring-title targeting."""
import unittest
from unittest import mock

from jobhunter import contacts
from jobhunter.contacts import (ApolloRotatingProvider, HunterProvider,
                                _is_ats_host, _pick_by_title, _QuotaExhausted)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class TestAtsHost(unittest.TestCase):
    def test_ats_hosts_detected(self):
        for d in ["boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com", ""]:
            self.assertTrue(_is_ats_host(d), d)

    def test_real_domain_not_ats(self):
        self.assertFalse(_is_ats_host("phonepe.com"))


class TestPickByTitle(unittest.TestCase):
    def test_irrelevant_titles_skipped_entirely(self):
        items = [{"position": "Director of Investments", "value": "a@x.com"},
                 {"position": "Quantitative Analyst", "value": "b@x.com"}]
        self.assertIsNone(_pick_by_title(items, contacts.TARGET_TITLES))

    def test_hiring_relevant_title_picked(self):
        items = [{"position": "Accountant", "value": "a@x.com"},
                 {"position": "Technical Recruiter", "value": "b@x.com"}]
        best = _pick_by_title(items, contacts.TARGET_TITLES)
        self.assertEqual(best["value"], "b@x.com")


class TestHunterRotation(unittest.TestCase):
    def test_rotates_to_next_key_on_quota(self):
        """Key 1 answers 429 -> provider must advance to key 2 and succeed."""
        prov = HunterProvider(["key1", "key2"])
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(params["api_key"])
            if params["api_key"] == "key1":
                return FakeResponse(429)
            if "domain-search" in url:
                return FakeResponse(200, {"data": {"emails": [{
                    "value": "recruiter@acme.com", "first_name": "Jane",
                    "last_name": "Roe", "position": "Technical Recruiter",
                    "confidence": 95}]}})
            return FakeResponse(200, {"data": {"status": "deliverable"}})

        with mock.patch.object(contacts.httpx, "get", side_effect=fake_get):
            c = prov.find("acme.com", contacts.TARGET_TITLES, "Acme")

        self.assertIsNotNone(c)
        self.assertEqual(c["email"], "recruiter@acme.com")
        self.assertEqual(c["verification_status"], "verified")
        self.assertAlmostEqual(c["confidence"], 0.95)
        self.assertIn("key1", calls)          # tried, hit quota
        self.assertEqual(prov.idx, 1)         # rotated permanently

    def test_all_keys_exhausted_returns_none(self):
        prov = HunterProvider(["key1", "key2"])
        with mock.patch.object(contacts.httpx, "get",
                               return_value=FakeResponse(429)):
            self.assertIsNone(prov.find("acme.com", contacts.TARGET_TITLES, "Acme"))

    def test_ats_domain_searches_by_company_name(self):
        prov = HunterProvider(["key1"])
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen.update(params)
            return FakeResponse(200, {"data": {"emails": []}})

        with mock.patch.object(contacts.httpx, "get", side_effect=fake_get):
            prov.find("boards.greenhouse.io", contacts.TARGET_TITLES, "Acme")
        self.assertEqual(seen.get("company"), "Acme")
        self.assertNotIn("domain", seen)


class TestApolloRotation(unittest.TestCase):
    def test_rotates_account_on_quota_exhausted(self):
        prov = ApolloRotatingProvider([{"label": "a1", "api_key": "k1"},
                                       {"label": "a2", "api_key": "k2"}])

        def fake_search(api_key, domain, titles):
            if api_key == "k1":
                raise _QuotaExhausted()
            return [{"id": "p1", "name": "Jane Roe", "title": "Recruiter",
                     "email_status": "verified", "linkedin_url": None}]

        with mock.patch.object(prov, "_search", side_effect=fake_search), \
             mock.patch.object(prov, "_reveal", return_value="jane@acme.com"):
            c = prov.find("acme.com", contacts.TARGET_TITLES)

        self.assertIsNotNone(c)
        self.assertEqual(c["email"], "jane@acme.com")
        self.assertEqual(c["source_provider"], "apollo:a2")

    def test_never_returns_invalid_email(self):
        prov = ApolloRotatingProvider([{"label": "a1", "api_key": "k1"}])
        with mock.patch.object(prov, "_search",
                               return_value=[{"id": "p1", "name": "X"}]), \
             mock.patch.object(prov, "_reveal",
                               return_value="email_not_unlocked@domain.com"):
            self.assertIsNone(prov.find("acme.com", contacts.TARGET_TITLES))

    def test_find_all_returns_every_valid_contact_decision_makers_first(self):
        """All valid people come back, sorted founder/CTO before recruiter — the
        provider's arbitrary order must never decide who gets contacted."""
        prov = ApolloRotatingProvider([{"label": "a1", "api_key": "k1"}])
        people = [{"id": "p1", "name": "Rita Recruiter", "title": "Technical Recruiter",
                   "email_status": "verified", "linkedin_url": None},
                  {"id": "p2", "name": "Fred Founder", "title": "Co-Founder & CEO",
                   "email_status": "verified", "linkedin_url": None}]
        emails = {"p1": "rita@acme.com", "p2": "fred@acme.com"}

        with mock.patch.object(prov, "_search", return_value=people), \
             mock.patch.object(prov, "_reveal", side_effect=lambda k, pid: emails[pid]):
            out = prov.find_all("acme.com", contacts.TARGET_TITLES)

        self.assertEqual([c["email"] for c in out], ["fred@acme.com", "rita@acme.com"])

    def test_quota_mid_list_rotates_and_resumes_without_losing_contacts(self):
        prov = ApolloRotatingProvider([{"label": "a1", "api_key": "k1"},
                                       {"label": "a2", "api_key": "k2"}])
        people = [{"id": "p1", "name": "A", "title": "CTO", "email_status": "verified"},
                  {"id": "p2", "name": "B", "title": "Recruiter", "email_status": "verified"}]

        def fake_reveal(api_key, pid):
            if api_key == "k1" and pid == "p2":
                raise _QuotaExhausted()
            return f"{pid}@acme.com"

        with mock.patch.object(prov, "_search", return_value=list(people)), \
             mock.patch.object(prov, "_reveal", side_effect=fake_reveal):
            out = prov.find_all("acme.com", contacts.TARGET_TITLES)

        self.assertEqual([c["email"] for c in out], ["p1@acme.com", "p2@acme.com"])


class TestTierRank(unittest.TestCase):
    def test_decision_makers_outrank_recruiters(self):
        self.assertLess(contacts.tier_rank("Co-Founder & CEO"),
                        contacts.tier_rank("Technical Recruiter"))
        self.assertLess(contacts.tier_rank("CTO"),
                        contacts.tier_rank("Engineering Manager"))
        self.assertLess(contacts.tier_rank("Engineering Manager"),
                        contacts.tier_rank("Talent Acquisition Partner"))

    def test_unknown_titles_rank_last(self):
        self.assertGreater(contacts.tier_rank("Accountant"),
                           contacts.tier_rank("recruiter"))
        self.assertGreater(contacts.tier_rank(None), contacts.tier_rank("founder"))

    def test_speculative_titles_are_founder_cto_only(self):
        self.assertEqual(contacts.SPECULATIVE_TITLES, ["founder", "co-founder", "cto"])


if __name__ == "__main__":
    unittest.main()
