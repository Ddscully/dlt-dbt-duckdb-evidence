"""A sliding-window budget for an API that charges by *volume*, not by call.

This counts **units** rather than requests, because the API it was written for
(Open-Meteo's archive) prices a request as ``(variables / 10) * (days / 14) *
locations`` — one request can cost 600 units and the next 0.4.

* **Several windows at once.** The limits are ``(seconds, units)`` pairs (60/600,
  3600/5000, 86400/10000 for that API), and a request must fit all of them.
* **Charged after the call.** The API serves an oversized request and then
  empties the bucket, so `acquire` reserves nothing and the caller `charge`s
  once the request is made. Not safe for concurrent callers, and not meant to
  be: a shared budget wants one sequential loop.
* **A request may exceed a whole window's budget**, where "sleep until it fits"
  never terminates. See `_window_delay`.

The limits arrive as arguments; `clock` and `sleep` are injectable so tests can
run a day of pacing in microseconds.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence

__all__ = ["WeightedWindowLimiter"]

# Slack on `_window_delay`'s "drained enough?" comparison. Summing charges and
# subtracting them back in floating point need not reach zero (`(0.1 + 0.2) -
# 0.1 - 0.2` is 4.16e-17), and a miss there falls through to "spend it now".
# Far below any real charge (~0.4 at the smallest), far above the residual.
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
            # A limiter with no windows would enforce nothing while looking like one.
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
        """Seconds to wait before spending `units`; 0.0 when it already fits.

        The largest wait any window asks for: how long until enough of its
        charged units age out for `units` to fit.
        """
        at = self._clock() if now is None else now
        self._trim(at)
        waits = [self._window_delay(window, budget, units, at) for window, budget in self._limits]
        return max([0.0, *waits])

    def _window_delay(self, window: float, budget: float, units: float, now: float) -> float:
        """How long one window says to wait before `units` may be spent.

        A request costing more than the window's whole budget waits for the
        window to drain completely, then overshoots into debt that later calls
        repay by waiting. Raising would refuse a request the API serves; treating
        it as fitting would spend against a full bucket and earn a 429.
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
                # Outside this (narrower) window; `_trim` keeps it for the widest.
                continue
            outstanding -= charged
            if outstanding <= target + _DRAIN_TOLERANCE:
                return max(0.0, when + window - now)
        # Unreachable given the tolerance: an empty window returned above.
        return 0.0

    def charge(self, units: float, now: float | None = None) -> None:
        """Record `units` as spent. Called *after* the request, as the API does."""
        at = self._clock() if now is None else now
        self._trim(at)
        self._spent.append((at, units))

    def acquire(self, units: float) -> float:
        """Block until `units` may be spent. Returns the seconds actually slept.

        Does not charge; the caller does, once the request has been made.
        """
        delay = self.delay_for(units)
        if delay > 0:
            self._sleep(delay)
        return delay
