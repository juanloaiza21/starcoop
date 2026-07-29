"""The bid-legality engine, driven by the conformance vectors.

`fixtures/bid-validation-vectors.json` is the executable specification. Two of its
vectors are ground truth taken from live platform rejections; the rest are derived
from the confirmed minimum-step rule or encode project safety rules the platform
will never enforce for us.

This check runs *before* every `ValidateBid` call. That is not an optimisation: a
rejected validation is written permanently to the buyer's audit log (SOT.md 4.10),
so a bid we could have refused ourselves costs us a visible black mark.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from centinela.domain.money import Centavos
from centinela.domain.rules import BidRules, IllegalReason

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "bid-validation-vectors.json"

# Platform error identifiers -> our reason enum. The bot branches on stable keys,
# never on localized Spanish prose.
# The platform's BidErrMin covers both "not lower" and "not lower by enough" -- its
# message asserts both in one sentence -- so either of our finer reasons satisfies it.
_EXPECTED_REASON: dict[str, set[IllegalReason]] = {
    "BidErrMin": {IllegalReason.BELOW_MINIMUM_STEP, IllegalReason.NOT_AN_IMPROVEMENT},
    "FloorBreach": {IllegalReason.FLOOR_BREACH},
    "BidWarn_Wrong_Decimal": {IllegalReason.SUB_CENTAVO_PRECISION},
    "BidWarn_Total_Value_Desc": {IllegalReason.NOT_AN_IMPROVEMENT},
    "BidWarn_Auction_Not_In_Progress": {IllegalReason.AUCTION_NOT_RUNNING},
}


def _load() -> dict[str, object]:
    parsed: dict[str, object] = json.loads(
        FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal
    )
    return parsed


def _pesos(value: object) -> Centavos:
    return Centavos(int(Decimal(str(value)) * 100))


def _rules_for(vector: dict[str, object], step: Centavos) -> BidRules:
    given = vector["given"]
    assert isinstance(given, dict)
    return BidRules(
        minimum_step=step,
        floor=_pesos(given["floorPrice"]),
        decimal_places=2,
    )


def _vectors() -> list[tuple[str, dict[str, object]]]:
    data = _load()
    vectors = data["vectors"]
    assert isinstance(vectors, list)
    return [(str(v["id"]), v) for v in vectors]


def _step() -> Centavos:
    data = _load()
    constants = data["derivedConstants"]
    assert isinstance(constants, dict)
    return _pesos(constants["minimumStepAbsolute"])


@pytest.mark.parametrize(("vector_id", "vector"), _vectors())
def test_conformance_vector(vector_id: str, vector: dict[str, object]) -> None:
    given = vector["given"]
    expect = vector["expect"]
    assert isinstance(given, dict) and isinstance(expect, dict)

    rules = _rules_for(vector, _step())
    previous = given.get("previousOwnTotal")
    previous_c = None if previous is None else _pesos(previous)
    running = given.get("auctionExecutionState", "Running") == "Running"

    candidate = given.get("candidateTotal")

    if vector.get("confidence") == "STRUCTURAL":
        # Sub-centavo precision is unrepresentable in integer centavos, so the parser
        # is what must refuse it. Assert that, rather than pretending the engine does.
        from centinela.domain.money import parse_decimal_string

        with pytest.raises(ValueError, match="sub-centavo"):
            parse_decimal_string(str(candidate))
        return

    if candidate is None:
        # These vectors assert that no legal bid remains at all.
        best = given.get("bestCompetitorTotal")
        result = rules.next_legal_bid(
            previous_own_total=previous_c,
            best_competitor_total=None if best is None else _pesos(best),
        )
        assert result is None, f"{vector_id}: expected no legal bid, got {result}"
        return

    best_c = given.get("bestCompetitorTotal")
    verdict = rules.check(
        candidate_total=_pesos(candidate),
        previous_own_total=previous_c,
        best_competitor_total=None if best_c is None else _pesos(best_c),
        auction_running=running,
    )

    assert verdict.legal is expect["legal"], f"{vector_id}: {vector['description']}"

    if not expect["legal"]:
        key = expect.get("errorType") or expect.get("errorMessageLocale")
        if key in _EXPECTED_REASON:
            assert verdict.reason in _EXPECTED_REASON[key], (
                f"{vector_id}: expected one of {_EXPECTED_REASON[key]} for {key}, "
                f"got {verdict.reason}"
            )


class TestNextLegalBid:
    """The engine must also *propose* the most competitive legal bid."""

    def test_proposes_exactly_one_step_below_our_previous_bid(self) -> None:
        rules = BidRules(
            minimum_step=Centavos(115_670_523),
            floor=Centavos(9_000_000_000),
            decimal_places=2,
        )
        proposal = rules.next_legal_bid(
            previous_own_total=Centavos(11_567_052_300), best_competitor_total=None
        )
        assert proposal == Centavos(11_451_381_777)

    def test_does_not_clamp_to_the_floor_when_a_full_step_overshoots_it(self) -> None:
        # If one step below the reference lands under the floor, then the floor is by
        # definition less than one step away, so bidding it would break the minimum-step
        # rule. Clamping would produce a bid that is both illegal and margin-destroying.
        rules = BidRules(
            minimum_step=Centavos(115_670_523),
            floor=Centavos(9_000_000_000),
            decimal_places=2,
        )
        assert (
            rules.next_legal_bid(
                previous_own_total=Centavos(9_050_000_000), best_competitor_total=None
            )
            is None
        )

    def test_bids_exactly_the_floor_when_it_sits_exactly_one_step_away(self) -> None:
        rules = BidRules(
            minimum_step=Centavos(115_670_523),
            floor=Centavos(9_000_000_000),
            decimal_places=2,
        )
        proposal = rules.next_legal_bid(
            previous_own_total=Centavos(9_115_670_523), best_competitor_total=None
        )
        assert proposal == Centavos(9_000_000_000)

    def test_holds_rather_than_chasing_a_competitor_probe_bid(self) -> None:
        # Both real competitors opened the captured auction at 1 COP and 0 COP. An
        # engine that treats those as a price to beat walks itself to zero.
        rules = BidRules(
            minimum_step=Centavos(115_670_523),
            floor=Centavos(9_000_000_000),
            decimal_places=2,
        )
        assert (
            rules.next_legal_bid(
                previous_own_total=Centavos(10_000_000_000),
                best_competitor_total=Centavos(100),
            )
            is None
        )

    @pytest.mark.parametrize("previous", [500_900, 600_000, 1_000_000, 501_000, 502_000])
    def test_any_proposal_it_makes_is_at_or_above_the_floor(self, previous: int) -> None:
        rules = BidRules(
            minimum_step=Centavos(1_000), floor=Centavos(500_000), decimal_places=2
        )
        proposal = rules.next_legal_bid(
            previous_own_total=Centavos(previous), best_competitor_total=None
        )
        if proposal is not None:
            assert proposal >= rules.floor
