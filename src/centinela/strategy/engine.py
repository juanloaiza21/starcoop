"""The decision engine.

A pure function from an observed auction state to one of four answers: hold, bid, stop,
or skip because there is no longer time. No I/O, no timers, no hidden state — which is
what lets every branch be exercised deterministically against the replayed capture.

Three principles decide the shape of it.

**The default is HOLD.** Anything the engine is unsure about resolves toward doing
nothing. In a reverse auction a missed bid costs one contract; a wrong bid wins a
contract that must then be delivered at a loss. Those are not the same size of mistake.

**Fire outside the closing window, not inside it.** A bid landing within
``extension_window_seconds`` of the end extends the auction by three minutes, handing
every rival another chance. The engine therefore aims to land its lance *before* that
window opens, so the auction can end with us in front. This is why "snipe at the last
second" is not merely hard here — it is the wrong goal.

**Never start a cycle that cannot finish.** A submit is two sequential round trips
against a platform whose p90 is around seven seconds each. If the live budget says the
cycle will not land before the close, the engine skips loudly rather than firing a bid
into the void.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from centinela.domain.latency import LatencyBudget
from centinela.domain.money import Centavos
from centinela.domain.rules import BidRules

__all__ = ["AuctionView", "Decision", "DecisionKind", "Policy", "decide"]


class DecisionKind(Enum):
    HOLD = "hold"
    """Nothing to do yet, or not safe to act."""

    BID = "bid"
    """Submit the accompanying total."""

    STOP = "stop"
    """Our room above the floor is exhausted. We are finished competing."""

    SKIP_NO_TIME = "skip_no_time"
    """There was a bid to make, but not enough time left to land it."""


@dataclass(frozen=True, slots=True)
class Policy:
    fire_at_seconds_before_end: float = 90.0
    """When to place the lance, measured back from the auction's end.

    Default 90 s: far enough ahead that the bid lands outside the 60 s closing window,
    absorbing the worst observed two-leg submit (~20 s) plus clock uncertainty and a
    retry. Owned here; the architecture chapter owns the mechanical abort deadline.
    """

    extension_window_seconds: float = 60.0
    """A bid landing inside this window extends the auction."""

    abort_margin_seconds: float = 20.0
    hard_cutoff_seconds: float = 15.0


@dataclass(frozen=True, slots=True)
class AuctionView:
    """Everything the engine is allowed to know. Deliberately small."""

    seconds_remaining: float
    """Server-time seconds until the close, taken from the pessimistic edge."""

    our_last_total: Centavos | None
    best_competitor_total: Centavos | None
    auction_running: bool = True
    link_healthy: bool = True
    clock_fresh: bool = True


@dataclass(frozen=True, slots=True)
class Decision:
    kind: DecisionKind
    reason: str
    total: Centavos | None = None
    alarm: bool = False
    """Whether a human should be looking at this right now."""


def decide(
    view: AuctionView,
    *,
    rules: BidRules,
    budget: LatencyBudget,
    policy: Policy,
) -> Decision:
    """Decide what to do, given what we can currently see."""

    # -- gates: reasons not to act at all ----------------------------------------

    if not view.auction_running:
        return Decision(DecisionKind.HOLD, "the auction is not running")

    if not view.clock_fresh:
        # Without a fresh clock we do not know how long is left, and every remaining
        # decision is a deadline decision.
        return Decision(
            DecisionKind.HOLD,
            "the server-clock estimate is stale; we cannot tell how long is left",
            alarm=True,
        )

    if not view.link_healthy:
        # Most of the room drops at least once per auction. Acting on a stale view is
        # how you bid against a price that has already moved.
        return Decision(
            DecisionKind.HOLD,
            "the realtime link is unhealthy; our view of the bid book may be stale",
            alarm=True,
        )

    # -- is there anything worth bidding? ----------------------------------------

    proposal = rules.next_legal_bid(
        previous_own_total=view.our_last_total,
        best_competitor_total=view.best_competitor_total,
    )

    if proposal is None:
        return Decision(
            DecisionKind.STOP,
            "no legal bid remains above the authorised floor; holding is correct and "
            "chasing further would mean bidding below cost",
        )

    # -- is it time? -------------------------------------------------------------

    if view.seconds_remaining > policy.fire_at_seconds_before_end:
        return Decision(
            DecisionKind.HOLD,
            f"{view.seconds_remaining:.0f}s remain; waiting until "
            f"{policy.fire_at_seconds_before_end:.0f}s so the lance lands outside the "
            f"{policy.extension_window_seconds:.0f}s closing window",
        )

    # -- is there time to land it? -----------------------------------------------

    if not budget.can_start_cycle(
        seconds_remaining=view.seconds_remaining, hard_floor=policy.hard_cutoff_seconds
    ):
        needed = budget.submit_cycle_seconds(quantile=0.9)
        return Decision(
            DecisionKind.SKIP_NO_TIME,
            f"a validate-then-submit cycle needs about {needed:.0f}s at p90 and only "
            f"{view.seconds_remaining:.0f}s remain; firing now risks landing after the "
            "close. A human should take control",
            alarm=True,
        )

    return Decision(
        DecisionKind.BID,
        f"firing at {view.seconds_remaining:.0f}s to land outside the closing window",
        total=proposal,
    )
