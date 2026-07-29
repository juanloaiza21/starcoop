"""End-to-end replay of the real captured auction.

This is the test that matters most, because it is the only one driven by an actual
auction rather than by our beliefs about one. It replays all 363 captured events —
clock ticks, bid-book snapshots, competitor connects and drops, our own two rejected
validations — through the domain and asserts on what the bot would have done.

The headline assertion is the safety one: across the entire real auction, in which two
competitors bid 1 COP and then 0 COP, the engine never proposes a bid below the floor.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from centinela.domain.money import Centavos
from centinela.domain.replay import ReplayResult, replay_auction

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "auction-sasi-001-2026.json"

# 90,000,000 COP — a plausible floor for a 115,670,523 COP contract.
FLOOR = Centavos(9_000_000_000)


def fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    return data


def run() -> ReplayResult:
    return replay_auction(fixture(), floor=FLOOR)


class TestTheReplayItself:
    def test_consumes_every_captured_event(self) -> None:
        result = run()
        assert result.events_applied == len(fixture()["events"])

    def test_synchronises_the_clock_from_the_captured_ticks(self) -> None:
        assert run().clock_synchronised is True

    def test_tracks_the_competitors_that_appeared(self) -> None:
        # 44 distinct companies were seen in the room.
        assert run().distinct_participants == 44

    def test_observes_the_connection_churn(self) -> None:
        result = run()
        assert result.disconnect_events == 100
        assert result.reconnect_events == 199


class TestWhatTheBotWouldHaveDone:
    def test_makes_no_proposal_at_all_because_none_could_clear_the_floor(self) -> None:
        # Stated explicitly rather than as "no proposal was below the floor", which
        # would be vacuously true of an empty list and would prove nothing. In this
        # auction a rival sat at 0 COP from the first snapshot onward, so there was
        # never a legal bid above our floor and the engine correctly proposed nothing.
        result = run()
        assert result.proposals == []
        assert result.proposals_below_floor == 0

    def test_holds_instead_of_chasing_the_zero_peso_bid(self) -> None:
        # Once a rival sits at 0 COP no legal bid clears our floor, so the correct
        # behaviour is to stop. An engine that kept proposing here would be walking
        # itself to zero.
        result = run()
        assert result.best_competitor_total == Centavos(0)
        assert result.final_decision_was_hold is True

    def test_records_the_platform_rejections_we_actually_received(self) -> None:
        result = run()
        assert result.observed_rejections == 2

    def test_would_have_refused_both_rejected_bids_locally_first(self) -> None:
        # Both bids the platform rejected are bids our own guard would have refused
        # before spending an audit-visible validation attempt on them.
        result = run()
        assert result.rejections_our_guard_would_have_prevented == 2


class TestTheProposalPathUnderPressure:
    """The real auction never let the engine propose, so exercise that path directly.

    A descending ladder against the captured auction's own ruleset: bid, get outbid by
    exactly one step, bid again, until the floor stops us. This is where the floor
    guarantee is actually proved.
    """

    def test_descends_by_exactly_one_step_and_stops_at_the_floor(self) -> None:
        from centinela.domain.rules import BidRules

        step = Centavos(115_670_523)
        rules = BidRules(minimum_step=step, floor=FLOOR, decimal_places=2)

        current = Centavos(11_567_052_300)  # the start price
        proposals: list[Centavos] = []

        while True:
            proposal = rules.next_legal_bid(
                previous_own_total=current, best_competitor_total=None
            )
            if proposal is None:
                break
            assert proposal == Centavos(current - step), "each rung is exactly one step"
            proposals.append(proposal)
            current = proposal

        assert len(proposals) > 20, "the ladder should run for many rungs before the floor"
        assert all(p >= FLOOR for p in proposals), "no rung may breach the floor"
        assert proposals[-1] - FLOOR < step, "it should stop as close to the floor as legal"

    def test_a_ladder_that_starts_below_the_floor_yields_nothing(self) -> None:
        from centinela.domain.rules import BidRules

        rules = BidRules(
            minimum_step=Centavos(115_670_523), floor=FLOOR, decimal_places=2
        )
        assert (
            rules.next_legal_bid(
                previous_own_total=Centavos(8_000_000_000), best_competitor_total=None
            )
            is None
        )
