"""Replaying a captured auction through the decision engine.

This is the closest thing to a dress rehearsal we can build without touching the live
platform: the real events, the real timings, the real competitor behaviour, driving the
same engine that would drive a real bid.

What it produces is a decision timeline — for each state change, what the bot would have
done and why. That timeline is the artefact an operator reads before agreeing to arm the
bot on a real auction, and it is the regression test that proves a strategy change did
not quietly turn the floor guard off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from centinela.domain.latency import LatencyBudget
from centinela.domain.money import Centavos, format_cop
from centinela.domain.rules import BidRules
from centinela.strategy.engine import AuctionView, Decision, DecisionKind, Policy, decide

__all__ = ["SimulationReport", "TimelineEntry", "simulate"]


def _instant(iso: str) -> float:
    text = iso.replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        offset = ""
        for marker in ("+", "-"):
            if marker in tail:
                offset = tail[tail.index(marker):]
                tail = tail[: tail.index(marker)]
                break
        digits = "".join(ch for ch in tail if ch.isdigit())[:6]
        text = f"{head}.{digits or '0'}{offset}"
    return datetime.fromisoformat(text).timestamp()


def _centavos(value: object) -> Centavos:
    return Centavos(int(Decimal(str(value)) * 100))


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    offset_ms: int
    seconds_remaining: float
    event: str
    decision: Decision

    def render(self) -> str:
        amount = format_cop(self.decision.total) if self.decision.total is not None else "-"
        return (
            f"{self.offset_ms / 1000:7.1f}s  {self.seconds_remaining:7.1f}s left  "
            f"{self.decision.kind.value:<13} {amount:>22}  {self.decision.reason}"
        )


@dataclass
class SimulationReport:
    decisions: list[TimelineEntry] = field(default_factory=list)
    bids_that_would_have_been_submitted: int = 0
    alarms_raised: int = 0
    best_competitor_total: Centavos | None = None
    our_last_total: Centavos | None = None

    def render(self, *, limit: int | None = None) -> str:
        rows = self.decisions if limit is None else self.decisions[:limit]
        return "\n".join(entry.render() for entry in rows)


def simulate(
    fixture: dict[str, Any],
    *,
    floor: Centavos,
    policy: Policy | None = None,
) -> SimulationReport:
    """Replay a captured auction and record what the engine would have decided.

    Args:
        fixture: A ``starcoop/auction-replay/v1`` document.
        floor: The operator-authorised floor, in centavos.
        policy: Timing policy; defaults to firing 90 s before the close.
    """
    auction = fixture["auction"]
    active_policy = policy or Policy()
    rules = BidRules(
        minimum_step=_minimum_step(auction),
        floor=floor,
        decimal_places=int(auction.get("decimalNum", 2)),
    )
    budget = LatencyBudget()
    report = SimulationReport()

    end_at = _instant(str(auction["endDate"]))
    started = _instant(str(fixture["events"][0]["at"]))
    last_decision: Decision | None = None

    for event in fixture["events"]:
        kind = event["type"]
        if kind not in ("bidbook.snapshot", "self.validate_bid", "auction.new_data"):
            continue

        if kind == "bidbook.snapshot":
            _apply_bidbook(event, report)
        elif kind == "self.validate_bid":
            # A real round trip we can charge to the budget.
            budget.record_validate(1.0)

        now = _instant(str(event["at"]))
        remaining = end_at - now

        decision = decide(
            AuctionView(
                seconds_remaining=remaining,
                our_last_total=report.our_last_total,
                best_competitor_total=report.best_competitor_total,
                auction_running=remaining > 0,
                link_healthy=True,
                clock_fresh=True,
            ),
            rules=rules,
            budget=budget,
            policy=active_policy,
        )

        # Only record when the answer changes; a timeline of 300 identical HOLDs is
        # noise an operator will not read.
        if last_decision is None or _changed(last_decision, decision):
            report.decisions.append(
                TimelineEntry(
                    offset_ms=int((now - started) * 1000),
                    seconds_remaining=remaining,
                    event=kind,
                    decision=decision,
                )
            )
        last_decision = decision

        if decision.kind is DecisionKind.BID:
            report.bids_that_would_have_been_submitted += 1
            report.our_last_total = decision.total
        if decision.alarm:
            report.alarms_raised += 1

    return report


# -- helpers -------------------------------------------------------------------------


def _changed(previous: Decision, current: Decision) -> bool:
    return previous.kind is not current.kind or previous.total != current.total


def _minimum_step(auction: dict[str, Any]) -> Centavos:
    total = 0
    for lot in auction.get("lots") or []:
        for item in lot.get("items") or []:
            initial = Decimal(str(item["itemInitialPrice"]))
            percent = Decimal(str(item["minBidDifference"]))
            total += int(initial * percent / 100 * 100)
    return Centavos(total)


def _apply_bidbook(event: dict[str, Any], report: SimulationReport) -> None:
    for bid in event.get("bids") or []:
        total = _centavos(bid.get("totalValue", 0))
        if bid.get("company") == "SELF":
            report.our_last_total = total
        elif report.best_competitor_total is None or total < report.best_competitor_total:
            report.best_competitor_total = total
