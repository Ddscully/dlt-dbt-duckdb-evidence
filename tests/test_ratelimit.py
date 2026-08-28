"""Unit tests for the weighted sliding-window limiter.

The interesting cases here are not "does the arithmetic work". They are the
three the module was written around, each of which a plausible implementation
gets wrong while looking right:

* the **widest** window is what binds on a long run, not the narrowest;
* a request larger than a whole window's budget must still terminate;
* the charge lands *after* the call, so a 429 must not be charged twice.

Time is injected rather than slept, so a simulated 24-hour backfill runs in
microseconds. A real `time.sleep` here would make the file take a day.
"""

from __future__ import annotations

import pytest

from modern_data_stack.ratelimit import WeightedWindowLimiter

# Open-Meteo's published free-tier budget, and the numbers every case below is
# reasoned against: 600/minute, 5,000/hour, 10,000/day.
OPEN_METEO_LIMITS = ((60.0, 600.0), (3600.0, 5000.0), (86400.0, 10000.0))


class FakeClock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(limits=OPEN_METEO_LIMITS) -> tuple[WeightedWindowLimiter, FakeClock]:
    clock = FakeClock()
    return WeightedWindowLimiter(limits, clock=clock, sleep=clock.sleep), clock


def test_a_spend_that_fits_waits_for_nothing():
    limiter, _ = make()
    limiter.charge(100)
    assert limiter.delay_for(100) == 0.0
    assert limiter.acquire(100) == 0.0


def test_a_spend_over_budget_waits_exactly_until_the_oldest_charge_expires():
    """Not "waits a bit" — the wait is the moment the window frees enough."""
    limiter, clock = make(((60.0, 600.0),))
    limiter.charge(400)  # at t
    clock.advance(10)
    limiter.charge(100)  # at t+10

    # 500 spent, 600 budget. A 200-unit spend needs 100 to age out, and only the
    # first charge can supply it — which happens 60s after it landed, i.e. 50s
    # from now.
    assert limiter.delay_for(200) == pytest.approx(50.0)


def test_spending_exactly_to_the_budget_does_not_wait():
    """The boundary, and it needs a prior charge to be visible at all.

    Against an *empty* window, `spent + units <= budget` and `< budget` both
    return 0.0 — the strict form falls through to the wait branch, finds nothing
    to expire, and returns 0.0 anyway. So an off-by-one here survives every
    obvious test. A standing charge that brings the total to exactly the budget
    is what separates them: 60 seconds against 0.
    """
    limiter, _ = make(((60.0, 600.0),))
    limiter.charge(100)
    assert limiter.delay_for(500) == 0.0


def test_the_widest_window_is_what_binds_on_a_long_run():
    """The minute budget refills sixty times an hour and the hourly one does not.

    A limiter that enforced only the narrowest window would pass this test's
    first assertion and blow the hourly budget on the second.
    """
    limiter, clock = make()
    # Spend the hour's budget in 500-unit chunks spread a minute apart, so the
    # per-minute window is empty the whole way through.
    for _ in range(10):
        limiter.charge(500)
        clock.advance(61)

    assert limiter.spent_within(60.0) == 0.0  # the minute window is clear
    assert limiter.spent_within(3600.0) == 5000.0  # the hour is full
    assert limiter.delay_for(100) > 0.0  # ...and it is the hour that says wait


def test_a_request_larger_than_the_whole_window_drains_it_rather_than_hanging():
    """The case that decides the design.

    A single 86-year, 3-variable Open-Meteo request costs ~673 units against a
    600/minute budget, and the API *serves* it — it charges after responding. So
    this is not an error, but "sleep until it fits" can never terminate for it.
    The policy is to drain the window and let the request overshoot into debt.
    """
    limiter, clock = make(((60.0, 600.0),))
    limiter.charge(50)
    clock.advance(20)

    delay = limiter.delay_for(673)
    assert delay == pytest.approx(40.0)  # the sole charge ages out 60s after it landed

    # It terminates, and it does not raise. Both halves matter: raising would
    # refuse a request the API answers.
    clock.sleep(delay)
    assert limiter.delay_for(673) == 0.0


def test_an_oversized_request_leaves_the_window_in_debt_and_the_next_call_pays():
    limiter, _ = make(((60.0, 600.0),))
    limiter.charge(900)  # served, but 300 over budget
    assert limiter.spent_within(60.0) == 900.0
    # Nothing can be spent until the whole overshoot ages out.
    assert limiter.delay_for(1) == pytest.approx(60.0)


def test_acquire_sleeps_but_does_not_charge():
    """A 429 must not be charged twice — so acquiring is not spending.

    If `acquire` charged, a caller that retried after a rejection would book the
    units once for the attempt and once for the retry, and the limiter would
    throttle itself to half throughput for no reason.
    """
    limiter, _ = make(((60.0, 600.0),))
    limiter.acquire(500)
    assert limiter.spent_within(60.0) == 0.0
    limiter.charge(500)
    assert limiter.spent_within(60.0) == 500.0


def test_expired_charges_stop_counting():
    limiter, clock = make(((60.0, 600.0),))
    limiter.charge(600)
    clock.advance(61)
    assert limiter.spent_within(60.0) == 0.0
    assert limiter.delay_for(600) == 0.0


def test_the_deque_is_bounded_by_the_widest_window_not_by_the_run_length():
    """A month-long backfill must not accumulate a month of history in memory."""
    limiter, clock = make()
    for _ in range(1000):
        limiter.charge(1)
        clock.advance(500)  # 500s apart, so only the day window can hold them
    limiter.charge(1)
    # 86,400s / 500s = 172 entries can be inside the widest window, plus the one
    # just charged. Anything unbounded would be holding all 1,001.
    assert len(limiter._spent) <= 174


@pytest.mark.parametrize(
    "limits",
    [(), ((0.0, 600.0),), ((60.0, 0.0),), ((-60.0, 600.0),)],
    ids=["no-windows", "zero-window", "zero-budget", "negative-window"],
)
def test_a_limiter_that_would_enforce_nothing_refuses_to_exist(limits):
    """An empty or degenerate limit list is the silent-failure shape: the caller
    thinks it is being paced and it is not."""
    with pytest.raises(ValueError):
        WeightedWindowLimiter(limits)


def test_a_paced_backfill_spends_its_budget_and_no_more():
    """The structural assertion: pace 12,195 units of work — the measured cost of
    the 2007- weather seed at 41 locations and 6 variables — and check the
    limiter never let an hour exceed 5,000.

    This pins the *shape* rather than any single delay, which is the thing a
    per-window arithmetic test cannot see: it is the only case here that would
    catch a `max()` over the windows quietly becoming a `min()`.
    """
    limiter, clock = make()
    per_request = 641.0  # one year, 41 locations, 6 daily variables
    requests = 19  # 2007..2025

    peak_hour = 0.0
    for _ in range(requests):
        limiter.acquire(per_request)
        limiter.charge(per_request)
        peak_hour = max(peak_hour, limiter.spent_within(3600.0))

    assert peak_hour <= 5000.0
    # 19 * 641 = 12,179 units against 5,000/hour is at least two full hours of
    # waiting; a limiter ignoring the hour window would finish in ~20 minutes.
    assert clock.now - 1000.0 > 2 * 3600.0
