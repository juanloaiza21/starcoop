"""The live latency budget (decision D-11).

A bid costs two sequential round trips — ValidateBid then SubmitBid — against a
platform whose measured REST p90 is 6.9 s with a 10 s tail. The bot commits its final
bid while at least the p90 of that budget still remains; acting later is gambling.

The budget is *live* because a platform having a slow day must widen the margin
automatically rather than silently eating into it.
"""

from __future__ import annotations

import pytest

from centinela.domain.latency import LatencyBudget

# Measured from the capture by tools/analyze_timing.py.
MEASURED_REST_P90 = 6.861
MEASURED_REST_MAX = 10.067


class TestColdStart:
    def test_a_budget_with_no_samples_is_pessimistic_not_optimistic(self) -> None:
        # The dangerous failure is a cold budget reporting ~0 s and convincing the bot
        # it has all the time in the world. It must start from the measured priors.
        budget = LatencyBudget()
        assert budget.submit_cycle_seconds(quantile=0.9) >= 2 * MEASURED_REST_P90 * 0.9

    def test_reports_that_it_is_running_on_priors(self) -> None:
        assert LatencyBudget().is_seeded_only is True


class TestObservedLatency:
    def test_reflects_a_consistently_fast_platform(self) -> None:
        budget = LatencyBudget()
        for _ in range(50):
            budget.record_validate(0.4)
            budget.record_submit(0.4)
        # Two legs of ~0.4 s each.
        assert budget.submit_cycle_seconds(quantile=0.9) == pytest.approx(0.8, abs=0.2)
        assert budget.is_seeded_only is False

    def test_widens_when_the_platform_slows_down(self) -> None:
        budget = LatencyBudget()
        for _ in range(50):
            budget.record_validate(0.4)
            budget.record_submit(0.4)
        fast = budget.submit_cycle_seconds(quantile=0.9)
        for _ in range(50):
            budget.record_validate(9.0)
            budget.record_submit(9.0)
        assert budget.submit_cycle_seconds(quantile=0.9) > fast

    def test_the_tail_quantile_exceeds_the_median(self) -> None:
        budget = LatencyBudget()
        for value in [0.2] * 90 + [9.0] * 10:
            budget.record_validate(value)
            budget.record_submit(value)
        assert budget.submit_cycle_seconds(quantile=0.99) > budget.submit_cycle_seconds(
            quantile=0.5
        )


class TestAbortMargin:
    def test_a_fast_platform_never_shrinks_the_margin_below_what_was_configured(self) -> None:
        # A good day is not a reason to start cutting it fine. The configured margin
        # and the hard cutoff are floors; observed speed can only ever widen them.
        budget = LatencyBudget()
        for _ in range(100):
            budget.record_validate(0.05)
            budget.record_submit(0.05)
        margin = budget.abort_margin_seconds(configured=20.0, hard_floor=15.0)
        assert margin == pytest.approx(20.0)

    def test_holds_the_hard_cutoff_when_nothing_larger_is_configured(self) -> None:
        budget = LatencyBudget()
        for _ in range(100):
            budget.record_validate(0.05)
            budget.record_submit(0.05)
        margin = budget.abort_margin_seconds(configured=15.0, hard_floor=15.0)
        assert margin == pytest.approx(15.0)

    def test_exceeds_the_configured_margin_when_the_platform_is_slow(self) -> None:
        budget = LatencyBudget()
        for _ in range(100):
            budget.record_validate(14.0)
            budget.record_submit(14.0)
        assert budget.abort_margin_seconds(configured=20.0, hard_floor=15.0) > 20.0

    def test_rejects_a_configured_margin_below_the_hard_cutoff(self) -> None:
        # 02 §3.1 makes 15 s a hard cutoff. A config asking for less describes a bot
        # that intends to start a cycle it cannot finish.
        with pytest.raises(ValueError, match="hard floor"):
            LatencyBudget().abort_margin_seconds(configured=8.0, hard_floor=15.0)


class TestCycleAdmission:
    def test_admits_a_cycle_with_ample_time(self) -> None:
        budget = LatencyBudget()
        assert budget.can_start_cycle(seconds_remaining=120.0) is True

    def test_refuses_a_cycle_that_would_land_after_the_close(self) -> None:
        budget = LatencyBudget()
        assert budget.can_start_cycle(seconds_remaining=3.0) is False

    def test_refuses_when_remaining_time_has_gone_negative(self) -> None:
        budget = LatencyBudget()
        assert budget.can_start_cycle(seconds_remaining=-1.0) is False
