"""Human-like send pacing (AGENT_SPEC "Email Sending" + BLUEPRINT §15).

Three protections, all enforced in CODE at commit-time:
  1. Randomized interval between consecutive sends (default 90-300s, uniform + jitter)
     so a batch never looks machine-gunned to Gmail's abuse heuristics.
  2. Quiet hours: nothing transmits during the configured local window (e.g. 21:00-08:00);
     drafts queue instead and go out on the next daytime run.
  3. Circuit breaker: N consecutive transport failures (auth, connection, 5xx) open the
     breaker and the rest of the batch queues — a broken sender must not burn the batch.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Optional


def within_quiet_hours(rules: dict, now: Optional[datetime] = None) -> bool:
    """True if local time falls inside quiet_hours: [start_hour, end_hour).
    A window like [21, 8] wraps midnight; [9, 18] does not; equal bounds disable."""
    qh = rules.get("quiet_hours") or []
    if len(qh) != 2:
        return False
    start, end = int(qh[0]), int(qh[1])
    if start == end:
        return False
    h = (now or datetime.now()).hour
    if start < end:
        return start <= h < end
    return h >= start or h < end   # wraps past midnight


def send_delay_seconds(rules: dict, rng: Optional[random.Random] = None) -> float:
    """Random human-like gap between two sends: uniform in [min,max] plus small jitter."""
    lo, hi = _interval_bounds(rules)
    r = rng or random
    base = r.uniform(lo, hi)
    jitter = r.uniform(0, max(1.0, 0.05 * (hi - lo)))
    return min(base + jitter, hi)


def _interval_bounds(rules: dict) -> tuple:
    iv = rules.get("send_interval_seconds") or [90, 300]
    try:
        lo, hi = float(iv[0]), float(iv[1])
    except (TypeError, ValueError, IndexError):
        lo, hi = 90.0, 300.0
    if lo < 0:
        lo = 0.0
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; any success resets the count."""

    def __init__(self, threshold: int = 3):
        self.threshold = max(1, int(threshold))
        self.consecutive_failures = 0
        self.open = False

    def record(self, ok: bool):
        if ok:
            self.consecutive_failures = 0
            return
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.threshold:
            self.open = True
