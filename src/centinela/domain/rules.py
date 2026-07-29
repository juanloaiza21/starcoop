"""Bid-legality rules for a SECOP II reverse auction.

This module answers two questions and nothing else:

* ``check`` — is this exact bid legal?
* ``next_legal_bid`` — what is the most competitive legal bid available right now?

It is pure: no clock, no network, no I/O. That is what makes it exhaustively testable
against the conformance vectors, and it is why the floor check lives here rather than
somewhere it could be bypassed by a code path that forgot to call it.

Two properties of the platform shape the design:

**The minimum step is a fixed absolute amount.** ``minimumBidType`` is ``"Percentage"``
and ``minBidDifference`` is a percentage of the item's *initial* price, so the step never
shrinks as the price falls. Recomputing it against the current price is the most likely
silent correctness bug in the system — it drifts downward and starts producing bids that
look right and get rejected. Callers pass the step in already resolved, from the server.

**Validation is not free.** A rejected ``ValidateBid`` is written permanently to the
buyer's audit log. Every candidate passes through here first so we never spend an
audit-visible attempt on a bid we could have refused ourselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from centinela.domain.money import Centavos

__all__ = ["BidRules", "IllegalReason", "LegalityVerdict"]


class IllegalReason(Enum):
    """Why a candidate bid was refused.

    ``FLOOR_BREACH`` is ours, not the platform's. The platform will happily accept a
    bid that ruins the cooperative.
    """

    NOT_AN_IMPROVEMENT = "not_an_improvement"
    BELOW_MINIMUM_STEP = "below_minimum_step"
    SUB_CENTAVO_PRECISION = "sub_centavo_precision"
    AUCTION_NOT_RUNNING = "auction_not_running"
    FLOOR_BREACH = "floor_breach"
    NON_POSITIVE = "non_positive"


@dataclass(frozen=True, slots=True)
class LegalityVerdict:
    legal: bool
    reason: IllegalReason | None = None
    detail: str = ""

    @staticmethod
    def ok() -> LegalityVerdict:
        return LegalityVerdict(legal=True)

    @staticmethod
    def refuse(reason: IllegalReason, detail: str = "") -> LegalityVerdict:
        return LegalityVerdict(legal=False, reason=reason, detail=detail)


@dataclass(frozen=True, slots=True)
class BidRules:
    """The legality rules for one auction (or one lot of one auction).

    Args:
        minimum_step: The absolute minimum improvement, in centavos, as resolved from
            the server. Never computed locally from a percentage of the current price.
        floor: The lowest bid the operator has authorised. Bidding *at* the floor is
            permitted; below it is not, ever.
        decimal_places: The auction's declared precision (``decimalNum``).
    """

    minimum_step: Centavos
    floor: Centavos
    decimal_places: int = 2

    def __post_init__(self) -> None:
        if self.minimum_step <= 0:
            raise ValueError("minimum_step must be positive")
        if self.floor < 0:
            raise ValueError("floor must not be negative")
        if not 0 <= self.decimal_places <= 2:
            # Centavos are the smallest unit we represent; more precision than the
            # auction declares would be rejected anyway.
            raise ValueError("decimal_places must be between 0 and 2")

    # -- queries -----------------------------------------------------------------

    def reference_total(
        self,
        previous_own_total: Centavos | None,
        best_competitor_total: Centavos | None,
    ) -> Centavos | None:
        """The value a new bid must improve upon, or ``None`` if unconstrained.

        Which value the server actually compares against is **open question O-M1**: its
        error message names *"su último lance"* (our own last bid) yet it refused bids
        when we had placed none. We therefore take the *most binding* reference available
        — the lowest — which can only ever make us more conservative. Being refused for
        bidding too little costs an audit-visible rejection; being wrong the other way
        costs the contract.
        """
        candidates = [c for c in (previous_own_total, best_competitor_total) if c is not None]
        return Centavos(min(candidates)) if candidates else None

    def check(
        self,
        *,
        candidate_total: Centavos,
        previous_own_total: Centavos | None = None,
        best_competitor_total: Centavos | None = None,
        auction_running: bool = True,
    ) -> LegalityVerdict:
        """Decide whether ``candidate_total`` may be sent to ``ValidateBid``.

        Note there is no sub-centavo check here: money is integer centavos, so
        sub-centavo values are unrepresentable by construction. That bug class is
        eliminated at the parsing boundary in :mod:`centinela.domain.money`.
        """
        if not auction_running:
            return LegalityVerdict.refuse(
                IllegalReason.AUCTION_NOT_RUNNING,
                "the platform rejects bids outside the running window",
            )

        if not self._has_allowed_precision(candidate_total):
            return LegalityVerdict.refuse(
                IllegalReason.SUB_CENTAVO_PRECISION,
                f"auction declares {self.decimal_places} decimal places",
            )

        reference = self.reference_total(previous_own_total, best_competitor_total)
        if reference is not None:
            if candidate_total >= reference:
                return LegalityVerdict.refuse(
                    IllegalReason.NOT_AN_IMPROVEMENT,
                    "a reverse-auction bid must be lower than the reference bid",
                )
            if reference - candidate_total < self.minimum_step:
                return LegalityVerdict.refuse(
                    IllegalReason.BELOW_MINIMUM_STEP,
                    f"improvement is smaller than the minimum step of {self.minimum_step}",
                )
        elif candidate_total <= 0:
            return LegalityVerdict.refuse(
                IllegalReason.NON_POSITIVE, "a bid must be a positive amount"
            )

        # The floor is ours, not the platform's, and it is the check that must never be
        # skipped. It runs last so that a bid which is *also* platform-illegal reports
        # the platform's reason, which is the more actionable one.
        if candidate_total < self.floor:
            return LegalityVerdict.refuse(
                IllegalReason.FLOOR_BREACH,
                f"below the authorised floor of {self.floor}",
            )

        return LegalityVerdict.ok()

    def next_legal_bid(
        self,
        *,
        previous_own_total: Centavos | None,
        best_competitor_total: Centavos | None = None,
    ) -> Centavos | None:
        """The most competitive legal bid available, or ``None`` if we are finished.

        ``None`` is a real answer, not an error: it means our room above the floor is
        exhausted and the correct action is to hold.

        **There is deliberately no clamping to the floor.** If a full step below the
        reference lands under the floor, then the floor is by definition less than one
        step away, so bidding it would violate the minimum-step rule anyway. Clamping
        would produce a bid that is both illegal and margin-destroying.

        This is also what stops the bot chasing probe bids. Competitors opened the
        captured auction at 1 COP and 0 COP; against a reference like that, no legal bid
        clears the floor, so the engine holds instead of walking itself to zero.
        """
        reference = self.reference_total(previous_own_total, best_competitor_total)
        if reference is None:
            # Nothing to improve on. The opening bid is a strategy decision, not a
            # legality one, and may additionally be constrained by the platform
            # requiring the first lance to equal the base price (unverified, R5).
            return None

        target = Centavos(reference - self.minimum_step)
        if target < self.floor:
            return None

        verdict = self.check(
            candidate_total=target,
            previous_own_total=previous_own_total,
            best_competitor_total=best_competitor_total,
        )
        return target if verdict.legal else None

    # -- helpers -----------------------------------------------------------------

    def _has_allowed_precision(self, amount: Centavos) -> bool:
        if self.decimal_places == 2:
            return True
        divisor = 10 ** (2 - self.decimal_places)
        remainder: int = int(amount) % divisor
        return remainder == 0
