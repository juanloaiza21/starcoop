"""Replaying the real auction through the decision engine.

This is the closest we can get to "what would the bot have done on 2 March" without
touching the live platform. It drives the actual engine over the actual captured
events and produces a decision timeline a human can read.

The point is not to prove the bot would have won — with a rival sitting at 0 COP,
nobody profitable wins that auction. The point is to prove the bot behaves sanely, and
in particular that it never proposes a bid below the floor no matter what the room does.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from centinela.domain.money import Centavos
from centinela.simulation import simulate
from centinela.strategy.engine import DecisionKind, Policy

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "auction-sasi-001-2026.json"
FLOOR = Centavos(9_000_000_000)


def fixture() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    return data


class TestTheSimulation:
    def test_produces_a_decision_for_every_state_change(self) -> None:
        report = simulate(fixture(), floor=FLOOR)
        assert report.decisions, "the simulation produced no decisions at all"

    def test_never_decides_to_bid_below_the_floor(self) -> None:
        report = simulate(fixture(), floor=FLOOR)
        for entry in report.decisions:
            if entry.decision.total is not None:
                assert entry.decision.total >= FLOOR

    def test_ends_by_stopping_rather_than_chasing_the_zero_peso_rival(self) -> None:
        report = simulate(fixture(), floor=FLOOR)
        assert report.decisions[-1].decision.kind is DecisionKind.STOP

    def test_reports_the_reason_it_stopped_in_words(self) -> None:
        report = simulate(fixture(), floor=FLOOR)
        assert "floor" in report.decisions[-1].decision.reason.lower()

    def test_counts_what_it_would_have_submitted(self) -> None:
        # With a rival at 0 COP there is no profitable bid, so the honest answer is
        # zero submissions rather than a flurry of doomed ones.
        report = simulate(fixture(), floor=FLOOR)
        assert report.bids_that_would_have_been_submitted == 0

    def test_a_lower_floor_does_not_make_it_chase_a_zero_bid(self) -> None:
        # Even with the floor dropped to 1 COP, one full step below a 0 COP rival is
        # negative, so there is still nothing legal to bid.
        report = simulate(fixture(), floor=Centavos(100))
        assert report.bids_that_would_have_been_submitted == 0


class TestAgainstAWinnableAuction:
    """The captured auction was unwinnable. Check the engine can also say yes."""

    def test_bids_when_a_rival_leaves_room_above_the_floor(self) -> None:
        data = fixture()
        # Replace the probe bids with a serious rival price that still leaves us room.
        for event in data["events"]:
            if event["type"] == "bidbook.snapshot":
                for bid in event["bids"]:
                    if bid.get("company") != "SELF":
                        bid["totalValue"] = Decimal("100000000.00")

        report = simulate(data, floor=FLOOR, policy=Policy(fire_at_seconds_before_end=1e9))
        assert report.bids_that_would_have_been_submitted > 0

    def test_those_bids_still_respect_the_floor(self) -> None:
        data = fixture()
        for event in data["events"]:
            if event["type"] == "bidbook.snapshot":
                for bid in event["bids"]:
                    if bid.get("company") != "SELF":
                        bid["totalValue"] = Decimal("100000000.00")

        report = simulate(data, floor=FLOOR, policy=Policy(fire_at_seconds_before_end=1e9))
        for entry in report.decisions:
            if entry.decision.total is not None:
                assert entry.decision.total >= FLOOR
