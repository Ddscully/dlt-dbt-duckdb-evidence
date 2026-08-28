"""A sliding-window budget for an API that charges by *volume*, not by call.

Most rate limiters count requests. This one counts **units**, because the API it
was written for (Open-Meteo's archive) prices a request as
``(variables / 10) * (days / 14) * locations`` — so one request can cost 600
units and the next one 0.4, and a limiter that counted calls would be measuring
the wrong thing entirely.

Three properties follow from that, and each of them is a decision rather than an
implementation detail:

* **Several windows are enforced at once.** The limits are a list of
  ``(seconds, units)`` pairs — 60/600, 3600/5000, 86400/10000 for the caller
  this was built for. A request has to fit in *all* of them, and it is nearly
  always the widest window that binds on a long backfill: the per-minute budget
  refills 60 times an hour and the hourly one does not.
* **The charge lands after the call, not before.** That is how the upstream API
  behaves — an oversized request succeeds and *then* empties the bucket — so
  `wait_for` reserves nothing and `charge` is a separate call the caller makes
  once it knows what the response actually cost. Reserving up front would be
  safer against concurrency, and this deliberately isn't concurrent: the caller
  is one sequential loop, because a thread pool is exactly what a shared budget
  cannot absorb.
* **A request may legitimately exceed a whole window's budget**, which is the
  case that breaks the usual token-bucket shape — "sleep until it fits" never
  terminates. See `delay_for`.

Nothing here knows what a weather variable is; the limits arrive as arguments.
`clock` and `sleep` are injectable so the tests can run a 24-hour backfill in
microseconds rather than sleeping through it.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence

__all__ = ["WeightedWindowLimiter"]

# Slack on the "has enough drained out yet?" comparison in `_window_delay`.
#
# `spent_within` sums the window's charges oldest-first and `_window_delay`
# subtracts them back off in the same order, so a fully drained window should
# leave exactly zero — and in binary floating point it does not: `(0.1 + 0.2)
# - 0.1 - 0.2` is 4.16e-17, not 0.0. Without slack the final subtraction misses
# `outstanding <= target`, the loop falls off its end and returns 0.0, which
# means "spend it now" — the one answer that branch exists to avoid giving.
#
# Sized to be far below any unit this can be asked about (the smallest real
# charge for the caller it was built for is ~0.4) and far above the residual of
# summing a window's worth of them.
_DRAIN_TOLERANCE = 1e-9


class WeightedWindowLimiter:
    """Enforce several ``(window_seconds, max_units)`` budgets simultaneously."""

    def __init__(
        self,
        limits: Sequence[tuple[float, float]],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not limits:
            # A limiter with no windows enforces nothing while looking like it
            # does, which is the failure mode this whole module exists to avoid.
            raise ValueError("WeightedWindowLimiter needs at least one (seconds, units) limit")
        for window, budget in limits:
            if window <= 0 or budget <= 0:
                raise ValueError(f"limit ({window}, {budget}) must be positive in both terms")
        self._limits = tuple(limits)
        self._clock = clock
        self._sleep = sleep
        # (charged_at, units), oldest first. Trimmed against the widest window,
        # so it stays bounded by the number of calls in that window rather than
        # by the length of the run.
        self._spent: deque[tuple[float, float]] = deque()

    @property
    def limits(self) -> tuple[tuple[float, float], ...]:
        return self._limits

    def _trim(self, now: float) -> None:
        widest = max(window for window, _ in self._limits)
        while self._spent and now - self._spent[0][0] >= widest:
            self._spent.popleft()

    def spent_within(self, window: float, now: float | None = None) -> float:
        """Units charged in the last `window` seconds."""
        at = self._clock() if now is None else now
        return sum(units for when, units in self._spent if at - when < window)

    def delay_for(self, units: float, now: float | None = None) -> float:
        """Seconds to wait before spending `units`. 0.0 when it already fits.

        For each window this asks the same question: if I spend `units` now, does
        the window's total go over budget — and if so, how long until enough of
        the already-charged units age out of it? The answer is the largest such
        wait across the windows.

        **The hard case is a request bigger than a whole window's budget.** The
        upstream API allows exactly that (it charges after serving, so an
        oversized request succeeds and leaves the bucket in debt), and no amount
        of waiting will ever make it "fit" — so the obvious loop, sleep until it
        fits, spins forever on a request the API would have happily answered.
        """
        at = self._clock() if now is None else now
        self._trim(at)
        waits = [self._window_delay(window, budget, units, at) for window, budget in self._limits]
        return max([0.0, *waits])

    def _window_delay(self, window: float, budget: float, units: float, now: float) -> float:
        """How long one window says to wait before `units` may be spent.

        An oversized request — one costing more than the window's entire budget —
        waits for the window to **drain completely** and then overshoots into
        debt, which the later calls repay by waiting longer. The two alternatives
        were both worse here. Raising would refuse a request the API demonstrably
        serves, and would put the burden of splitting on every caller, including
        the ones whose smallest indivisible unit is already too big. Treating it
        as fitting would spend against an already-full bucket and earn the 429
        that this class exists to avoid. Draining first is the only one of the
        three that both terminates and gives the request its best chance of being
        served, because it starts from an empty window.
        """
        spent = self.spent_within(window, now)
        if spent + units <= budget:
            return 0.0

        # How much may still be outstanding in this window for `units` to fit.
        # Negative would be meaningless, so an oversized request asks for empty.
        target = max(0.0, budget - units)

        # Walk oldest-first: each entry releases its units at `when + window`, so
        # the first expiry that brings the outstanding total down to `target` is
        # the moment the spend becomes legal.
        outstanding = spent
        for when, charged in self._spent:
            if now - when >= window:
                # Older than *this* window, so it never counted towards `spent`.
                # `_trim` only drops entries older than the widest window, so the
                # deque legitimately holds these.
                continue
            outstanding -= charged
            if outstanding <= target + _DRAIN_TOLERANCE:
                return max(0.0, when + window - now)
        # Only reachable if the deque held no entry inside this window, in which
        # case `spent` was 0 and the caller already returned above. The tolerance
        # is what makes that true: without it, a drained window that did not
        # subtract back to exactly zero arrived here and answered "no wait".
        return 0.0

    def charge(self, units: float, now: float | None = None) -> None:
        """Record `units` as spent. Called *after* the request, as the API does."""
        at = self._clock() if now is None else now
        self._trim(at)
        self._spent.append((at, units))

    def acquire(self, units: float) -> float:
        """Block until `units` may be spent. Returns the seconds actually slept.

        Does **not** charge — the caller does that afterwards, because only the
        caller knows whether the request was served. A 429 is not a spend this
        limiter should record twice.
        """
        delay = self.delay_for(units)
        if delay > 0:
            self._sleep(delay)
        return delay
