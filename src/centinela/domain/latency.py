"""Live latency budget for the submit path (decision D-11).

Placing a bid costs two sequential round trips — ``ValidateBid`` then ``SubmitBid`` —
against a platform whose measured REST latency is p50 941 ms, p90 6.9 s, max 10.1 s.
A full cycle therefore has a p90 near 14 s and a tail past 20 s.

The bot commits its final bid while at least that budget still remains. Two design
choices matter more than the statistics:

**A cold budget is pessimistic.** Before any live sample arrives, the estimate starts
from the priors measured in the capture. An empty budget reporting "0 seconds needed"
would tell the bot it has all the time in the world at exactly the moment it knows
least, which is the failure this class exists to prevent.

**The margin is re-derived, not trusted as a constant.** A platform having a slow day
widens the abort margin automatically. The configured value and the 15 s hard cutoff
from the mechanics chapter are floors, never ceilings.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

__all__ = ["LatencyBudget"]

# Priors from tools/analyze_timing.py over the captured session. Used until enough live
# samples accumulate, and blended in afterwards so one lucky fast patch cannot collapse
# the margin.
PRIOR_REST_SECONDS: tuple[float, ...] = (
    0.137, 0.4, 0.6, 0.8, 0.941, 1.2, 2.0, 3.5, 5.0, 6.861, 8.0, 10.067,
)

# Below this many live samples per leg, the priors still dominate.
MIN_LIVE_SAMPLES = 20

DEFAULT_CLOCK_UNCERTAINTY_SECONDS = 2.0
DEFAULT_SAFETY_SECONDS = 5.0


def _quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile. Small sample sizes make interpolation false precision."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(q * (len(ordered) - 1))))
    return ordered[index]


class LatencyBudget:
    def __init__(self, *, window: int = 200) -> None:
        self._validate: deque[float] = deque(maxlen=window)
        self._submit: deque[float] = deque(maxlen=window)

    # -- ingest ------------------------------------------------------------------

    def record_validate(self, seconds: float) -> None:
        self._validate.append(seconds)

    def record_submit(self, seconds: float) -> None:
        self._submit.append(seconds)

    # -- state -------------------------------------------------------------------

    @property
    def is_seeded_only(self) -> bool:
        """Whether the estimate still rests on priors rather than live observation."""
        return len(self._validate) < MIN_LIVE_SAMPLES or len(self._submit) < MIN_LIVE_SAMPLES

    # -- queries -----------------------------------------------------------------

    def submit_cycle_seconds(self, *, quantile: float = 0.9) -> float:
        """Estimated duration of a full validate-then-submit cycle."""
        return self._leg_seconds(self._validate, quantile) + self._leg_seconds(
            self._submit, quantile
        )

    def abort_margin_seconds(
        self,
        *,
        configured: float,
        hard_floor: float = 15.0,
        clock_uncertainty: float = DEFAULT_CLOCK_UNCERTAINTY_SECONDS,
        safety: float = DEFAULT_SAFETY_SECONDS,
    ) -> float:
        """How long before close the bot stops starting new submit cycles.

        Raises:
            ValueError: if ``configured`` is below ``hard_floor``. The mechanics
                chapter makes 15 s a hard cutoff; a configuration asking for less
                describes a bot that intends to start a cycle it cannot finish.
        """
        if configured < hard_floor:
            raise ValueError(
                f"configured abort margin {configured}s is below the hard floor {hard_floor}s"
            )
        live = self.submit_cycle_seconds(quantile=0.99) + clock_uncertainty + safety
        return max(live, configured, hard_floor)

    def can_start_cycle(
        self, *, seconds_remaining: float, quantile: float = 0.9, hard_floor: float = 15.0
    ) -> bool:
        """Whether there is time to start a cycle that can still land before close."""
        if seconds_remaining <= 0:
            return False
        needed = self.submit_cycle_seconds(quantile=quantile)
        return seconds_remaining >= max(needed, hard_floor)

    # -- internals ---------------------------------------------------------------

    def _leg_seconds(self, observed: Iterable[float], quantile: float) -> float:
        samples = list(observed)
        if len(samples) < MIN_LIVE_SAMPLES:
            # Blend priors in so a handful of fast replies cannot collapse the margin.
            samples = samples + list(PRIOR_REST_SECONDS)
        return _quantile(samples, quantile)
