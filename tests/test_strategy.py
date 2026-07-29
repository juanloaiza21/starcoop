"""The decision engine: hold, bid, stop, or hand back to a human.

This is where the clock, the latency budget, the legality rules and the floor meet.
It is a pure function of an observed state — no I/O, no timers — so every branch can
be exercised deterministically.

Its default answer is HOLD. Anything the engine is unsure about resolves toward doing
nothing, because in a reverse auction the cost of a missed bid is one lost contract and
the cost of a wrong bid is a contract delivered at a loss.
"""

from __future__ import annotations

import pytest

from centinela.domain.latency import LatencyBudget
from centinela.domain.money import Centavos
from centinela.domain.rules import BidRules
from centinela.strategy.engine import AuctionView, Decision, DecisionKind, Policy, decide

STEP = Centavos(115_670_523)
FLOOR = Centavos(9_000_000_000)
START = Centavos(11_567_052_300)


def rules() -> BidRules:
    return BidRules(minimum_step=STEP, floor=FLOOR, decimal_places=2)


def policy() -> Policy:
    # Fire well before the closing window so the lance lands outside it and the
    # auction can actually end, rather than triggering a 3-minute extension.
    return Policy(fire_at_seconds_before_end=90.0, extension_window_seconds=60.0)


def view(
    *,
    seconds_remaining: float = 300.0,
    our_last_total: Centavos | None = START,
    best_competitor_total: Centavos | None = None,
    auction_running: bool = True,
    link_healthy: bool = True,
    clock_fresh: bool = True,
) -> AuctionView:
    return AuctionView(
        seconds_remaining=seconds_remaining,
        our_last_total=our_last_total,
        best_competitor_total=best_competitor_total,
        auction_running=auction_running,
        link_healthy=link_healthy,
        clock_fresh=clock_fresh,
    )


def run(v: AuctionView, budget: LatencyBudget | None = None) -> Decision:
    return decide(v, rules=rules(), budget=budget or LatencyBudget(), policy=policy())


class TestHoldingByDefault:
    def test_holds_while_the_firing_point_is_still_far_away(self) -> None:
        decision = run(view(seconds_remaining=300.0))
        assert decision.kind is DecisionKind.HOLD

    def test_holds_when_the_link_is_unhealthy(self) -> None:
        # A stale view is not a reason to guess. 36 of 44 companies dropped at least
        # once in the captured auction; acting on what we last saw is how you bid
        # against a price that has already moved.
        decision = run(view(seconds_remaining=60.0, link_healthy=False))
        assert decision.kind is not DecisionKind.BID

    def test_holds_when_the_clock_estimate_has_gone_stale(self) -> None:
        decision = run(view(seconds_remaining=60.0, clock_fresh=False))
        assert decision.kind is not DecisionKind.BID

    def test_holds_when_the_auction_is_not_running(self) -> None:
        decision = run(view(seconds_remaining=60.0, auction_running=False))
        assert decision.kind is not DecisionKind.BID


class TestBidding:
    def test_bids_once_the_firing_point_is_reached(self) -> None:
        decision = run(view(seconds_remaining=89.0))
        assert decision.kind is DecisionKind.BID
        assert decision.total == Centavos(START - STEP)

    def test_the_bid_it_proposes_is_always_legal(self) -> None:
        decision = run(view(seconds_remaining=89.0))
        assert decision.total is not None
        verdict = rules().check(candidate_total=decision.total, previous_own_total=START)
        assert verdict.legal

    def test_never_bids_below_the_floor_however_late_it_is(self) -> None:
        # One step below this would breach the floor, so there is no legal bid left.
        decision = run(view(seconds_remaining=89.0, our_last_total=Centavos(9_050_000_000)))
        assert decision.kind is not DecisionKind.BID

    def test_stops_when_its_room_above_the_floor_is_exhausted(self) -> None:
        decision = run(view(seconds_remaining=89.0, our_last_total=Centavos(9_050_000_000)))
        assert decision.kind is DecisionKind.STOP

    def test_refuses_to_chase_a_probe_bid_below_the_floor(self) -> None:
        # Both real competitors opened at 1 COP and 0 COP.
        decision = run(
            view(seconds_remaining=89.0, best_competitor_total=Centavos(100))
        )
        assert decision.kind is DecisionKind.STOP


class TestTheDeadline:
    def test_refuses_to_start_a_cycle_it_cannot_finish(self) -> None:
        # A cold budget assumes the platform's measured p90 of roughly 14 s for a full
        # validate-then-submit cycle, so 5 s remaining is not enough to start one.
        decision = run(view(seconds_remaining=5.0))
        assert decision.kind is DecisionKind.SKIP_NO_TIME

    def test_a_skipped_cycle_is_loud_rather_than_silent(self) -> None:
        decision = run(view(seconds_remaining=5.0))
        assert decision.alarm is True

    def test_still_bids_late_when_the_platform_is_demonstrably_fast(self) -> None:
        fast = LatencyBudget()
        for _ in range(60):
            fast.record_validate(0.2)
            fast.record_submit(0.2)
        decision = run(view(seconds_remaining=20.0), budget=fast)
        assert decision.kind is DecisionKind.BID


class TestExplainingItself:
    def test_every_decision_carries_a_reason_a_human_can_read(self) -> None:
        for v in (
            view(seconds_remaining=300.0),
            view(seconds_remaining=89.0),
            view(seconds_remaining=5.0),
            view(seconds_remaining=60.0, link_healthy=False),
        ):
            decision = run(v)
            assert decision.reason, f"{decision.kind} carried no reason"

    @pytest.mark.parametrize("remaining", [1000.0, 300.0, 91.0, 89.0, 30.0, 16.0, 5.0, 0.5])
    def test_never_proposes_a_bid_below_the_floor_at_any_point_in_the_auction(
        self, remaining: float
    ) -> None:
        decision = run(view(seconds_remaining=remaining))
        if decision.total is not None:
            assert decision.total >= FLOOR
