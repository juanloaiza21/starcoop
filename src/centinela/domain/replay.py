"""Offline replay of a captured auction.

Auctions are rare events we cannot summon on demand, and generating test data by
probing a government platform is not acceptable. Replay is therefore the only honest
way to exercise the decision path against real timings, real competitor behaviour and
a real hostile network trace.

The replay is deterministic and does no I/O: it consumes the event stream produced by
``tools/build_replay_fixture.py`` and drives the same domain objects the live runtime
uses. What it reports is not "what happened" — that is in the fixture — but **what the
bot would have decided** at each point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from centinela.domain.clock import ClockSample, ServerClock
from centinela.domain.money import Centavos
from centinela.domain.rules import BidRules

__all__ = ["ReplayResult", "replay_auction"]


def _instant(iso: str) -> float:
    """Parse an ISO timestamp to epoch seconds, tolerating .NET's extra precision."""
    text = iso.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        offset = tail[len(digits):] if not tail[len(digits):].isdigit() else ""
        for marker in ("+", "-"):
            if marker in tail:
                offset = tail[tail.index(marker):]
                break
        text = f"{head}.{digits or '0'}{offset}"
    return datetime.fromisoformat(text).timestamp()


def _centavos(value: object) -> Centavos:
    return Centavos(int(Decimal(str(value)) * 100))


@dataclass
class ReplayResult:
    events_applied: int = 0
    clock_synchronised: bool = False
    distinct_participants: int = 0
    disconnect_events: int = 0
    reconnect_events: int = 0
    observed_rejections: int = 0
    rejections_our_guard_would_have_prevented: int = 0
    best_competitor_total: Centavos | None = None
    our_last_total: Centavos | None = None
    proposals: list[Centavos] = field(default_factory=list)
    proposals_below_floor: int = 0
    final_decision_was_hold: bool = False


def replay_auction(fixture: dict[str, Any], *, floor: Centavos) -> ReplayResult:
    """Replay a captured auction and report what the bot would have decided.

    Args:
        fixture: A ``starcoop/auction-replay/v1`` document.
        floor: The operator-authorised floor, in centavos.
    """
    auction = fixture["auction"]
    step = _minimum_step(auction)
    rules = BidRules(
        minimum_step=step,
        floor=floor,
        decimal_places=int(auction.get("decimalNum", 2)),
    )

    clock = ServerClock()
    result = ReplayResult()
    participants: set[str] = set()

    for event in fixture["events"]:
        result.events_applied += 1
        kind = event["type"]

        if kind == "clock.tick":
            # The fixture records when the tick arrived, not when it was requested.
            # Treat the surrounding poll as a nominal round trip; the replay cares
            # about wiring, not about re-deriving the offset precisely.
            received = _instant(event["at"])
            clock.observe(
                ClockSample(
                    server_time=_instant(event["serverTime"]),
                    sent_at=received - 1.0,
                    received_at=received,
                )
            )

        elif kind == "participants.onConnected":
            for entry in event.get("participants") or []:
                for company in entry if isinstance(entry, list) else [entry]:
                    if isinstance(company, dict) and company.get("companyCode"):
                        participants.add(str(company["companyCode"]))

        elif kind == "participants.onNewUserConnected":
            result.reconnect_events += 1
            for company in event.get("participants") or []:
                if isinstance(company, dict) and company.get("companyCode"):
                    participants.add(str(company["companyCode"]))

        elif kind == "participants.onUserDisconnected":
            result.disconnect_events += 1

        elif kind == "bidbook.snapshot":
            _apply_bidbook(event, result)

        elif kind == "self.validate_bid":
            _apply_our_attempt(event, rules, result)

        # After any state change, ask the engine what it would do now.
        _record_proposal(rules, result, floor)

    result.clock_synchronised = clock.has_estimate
    result.distinct_participants = len(participants)
    result.final_decision_was_hold = _current_proposal(rules, result) is None
    return result


# -- helpers -------------------------------------------------------------------------


def _minimum_step(auction: dict[str, Any]) -> Centavos:
    total = 0
    for lot in auction.get("lots") or []:
        for item in lot.get("items") or []:
            initial = Decimal(str(item["itemInitialPrice"]))
            percent = Decimal(str(item["minBidDifference"]))
            total += int(initial * percent / 100 * 100)
    return Centavos(total)


def _apply_bidbook(event: dict[str, Any], result: ReplayResult) -> None:
    """Track the best visible bid, and our own, from a GetSupplierBids snapshot."""
    for bid in event.get("bids") or []:
        total = _centavos(bid.get("totalValue", 0))
        if bid.get("company") == "SELF":
            result.our_last_total = total
        elif result.best_competitor_total is None or total < result.best_competitor_total:
            result.best_competitor_total = total


def _apply_our_attempt(event: dict[str, Any], rules: BidRules, result: ReplayResult) -> None:
    """Compare the platform's verdict on our real bid with our own guard's."""
    response = event.get("response") or {}
    if response.get("isValid") is not False:
        return

    result.observed_rejections += 1
    candidate = _centavos(event["request"]["totalValue"])
    verdict = rules.check(
        candidate_total=candidate,
        previous_own_total=result.our_last_total,
        best_competitor_total=result.best_competitor_total,
    )
    if not verdict.legal:
        result.rejections_our_guard_would_have_prevented += 1


def _current_proposal(rules: BidRules, result: ReplayResult) -> Centavos | None:
    return rules.next_legal_bid(
        previous_own_total=result.our_last_total,
        best_competitor_total=result.best_competitor_total,
    )


def _record_proposal(rules: BidRules, result: ReplayResult, floor: Centavos) -> None:
    proposal = _current_proposal(rules, result)
    if proposal is None:
        return
    result.proposals.append(proposal)
    if proposal < floor:
        result.proposals_below_floor += 1
