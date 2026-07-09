"""Human-like sending: quiet hours, randomized intervals, circuit breaker."""
import random
import unittest
from datetime import datetime

from jobhunter.pacing import CircuitBreaker, send_delay_seconds, within_quiet_hours


def _at(hour):
    return datetime(2026, 7, 9, hour, 30)


class TestQuietHours(unittest.TestCase):
    def test_wrapping_window(self):
        rules = {"quiet_hours": [21, 8]}
        self.assertTrue(within_quiet_hours(rules, _at(22)))
        self.assertTrue(within_quiet_hours(rules, _at(3)))
        self.assertFalse(within_quiet_hours(rules, _at(12)))
        self.assertFalse(within_quiet_hours(rules, _at(8)))   # end is exclusive

    def test_daytime_window(self):
        rules = {"quiet_hours": [9, 18]}
        self.assertTrue(within_quiet_hours(rules, _at(10)))
        self.assertFalse(within_quiet_hours(rules, _at(20)))

    def test_disabled_when_missing_or_equal(self):
        self.assertFalse(within_quiet_hours({}, _at(23)))
        self.assertFalse(within_quiet_hours({"quiet_hours": [8, 8]}, _at(23)))


class TestSendDelay(unittest.TestCase):
    def test_within_configured_bounds(self):
        rules = {"send_interval_seconds": [90, 300]}
        rng = random.Random(42)
        for _ in range(500):
            d = send_delay_seconds(rules, rng)
            self.assertGreaterEqual(d, 90)
            self.assertLessEqual(d, 300)

    def test_defaults_when_unconfigured(self):
        rng = random.Random(7)
        for _ in range(100):
            d = send_delay_seconds({}, rng)
            self.assertGreaterEqual(d, 90)
            self.assertLessEqual(d, 300)

    def test_bad_config_falls_back(self):
        d = send_delay_seconds({"send_interval_seconds": ["x"]}, random.Random(1))
        self.assertGreaterEqual(d, 90)
        self.assertLessEqual(d, 300)


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold(self):
        b = CircuitBreaker(3)
        for _ in range(2):
            b.record(False)
        self.assertFalse(b.open)
        b.record(False)
        self.assertTrue(b.open)

    def test_success_resets_count(self):
        b = CircuitBreaker(3)
        b.record(False)
        b.record(False)
        b.record(True)
        b.record(False)
        b.record(False)
        self.assertFalse(b.open)


if __name__ == "__main__":
    unittest.main()
