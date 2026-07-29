"""Resolving an auction's rules from the server payload.

Two principles, both safety rules rather than conveniences:

**Read the full `GetAuction`, never `GetAuctionSimple`.** The simple variant returns
opaque integer enums and omits `minBidDifference` entirely, so a ruleset built from it
would silently invent a minimum step.

**Fail closed on anything we do not understand.** Refusing to bid is always safe;
guessing is not. An auction shape outside the one configuration we have actually
observed must stop the bot, not be approximated.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from centinela.domain.auction import AuctionRuleset, UnsupportedAuctionError
from centinela.domain.money import Centavos

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "auction-sasi-001-2026.json"


def captured_auction() -> dict[str, Any]:
    """The real auction config, as captured. The one shape we have observed."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)
    auction: dict[str, Any] = dict(data["auction"])
    # The fixture stores the resolved ruleset; restore the raw payload shape.
    auction["biddingList"] = [
        {
            "internalId": lot["lotInternalId"],
            "lotName": lot["lotName"],
            "biddingPriceListItems": [
                {
                    "id": item["itemId"],
                    "itemInternalReference": item["itemInternalReference"],
                    "itemDescription": item["itemDescription"],
                    "itemUnit": item["itemUnit"],
                    "itemQuantity": item["itemQuantity"],
                    "itemInitialPrice": item["itemInitialPrice"],
                    "minBidDifference": item["minBidDifference"],
                }
                for item in lot["items"]
            ],
        }
        for lot in auction.pop("lots")
    ]
    return auction


class TestTheCapturedAuction:
    def test_resolves_the_confirmed_minimum_step(self) -> None:
        ruleset = AuctionRuleset.from_get_auction(captured_auction())
        # 1.0% of 115,670,523 = 1,156,705.23 COP, exactly the minBid the platform quoted.
        assert ruleset.minimum_step == Centavos(115_670_523)

    def test_recognises_it_as_a_reverse_auction(self) -> None:
        assert AuctionRuleset.from_get_auction(captured_auction()).is_reverse is True

    def test_reads_the_extension_rule(self) -> None:
        ruleset = AuctionRuleset.from_get_auction(captured_auction())
        assert ruleset.extension_window_seconds == 60
        assert ruleset.extension_period_seconds == 180

    def test_reads_the_start_price(self) -> None:
        ruleset = AuctionRuleset.from_get_auction(captured_auction())
        assert ruleset.start_price == Centavos(11_567_052_300)

    def test_exposes_a_single_lot_with_a_single_item(self) -> None:
        ruleset = AuctionRuleset.from_get_auction(captured_auction())
        assert len(ruleset.lots) == 1
        assert len(ruleset.lots[0].items) == 1


class TestFailClosed:
    """Anything outside the observed configuration must stop the bot."""

    def test_refuses_a_forward_auction_where_higher_wins(self) -> None:
        payload = captured_auction()
        payload["auctionTypeCode"] = "Direct"
        with pytest.raises(UnsupportedAuctionError, match="auctionTypeCode"):
            AuctionRuleset.from_get_auction(payload)

    def test_refuses_an_evaluation_type_other_than_best_price(self) -> None:
        payload = captured_auction()
        payload["auctionEvaluationTypeCode"] = "BestScore"
        with pytest.raises(UnsupportedAuctionError, match="auctionEvaluationTypeCode"):
            AuctionRuleset.from_get_auction(payload)

    def test_refuses_a_minimum_bid_type_we_have_never_observed(self) -> None:
        # Only "Percentage" is confirmed. A different type changes the arithmetic of
        # every bid, and we have no evidence for how.
        payload = captured_auction()
        payload["minimumBidType"] = "Value"
        with pytest.raises(UnsupportedAuctionError, match="minimumBidType"):
            AuctionRuleset.from_get_auction(payload)

    def test_refuses_an_auction_carrying_a_scoring_formula(self) -> None:
        payload = captured_auction()
        payload["scoreCalcFormula"] = "price * 0.7 + quality * 0.3"
        with pytest.raises(UnsupportedAuctionError, match="scoreCalcFormula"):
            AuctionRuleset.from_get_auction(payload)

    def test_refuses_an_auction_carrying_evaluation_rules(self) -> None:
        payload = captured_auction()
        payload["evaluationRules"] = [{"rule": "something"}]
        with pytest.raises(UnsupportedAuctionError, match="evaluationRules"):
            AuctionRuleset.from_get_auction(payload)

    def test_refuses_a_payload_missing_the_minimum_bid_difference(self) -> None:
        # This is what GetAuctionSimple looks like. Building a ruleset from it would
        # invent a step out of nothing.
        payload = captured_auction()
        del payload["biddingList"][0]["biddingPriceListItems"][0]["minBidDifference"]
        with pytest.raises(UnsupportedAuctionError, match="minBidDifference"):
            AuctionRuleset.from_get_auction(payload)

    def test_refuses_an_auction_with_no_price_list(self) -> None:
        payload = captured_auction()
        payload["biddingList"] = []
        with pytest.raises(UnsupportedAuctionError, match="no lots"):
            AuctionRuleset.from_get_auction(payload)


class TestMultiLot:
    """Universality is a stated hard requirement, so multi-lot must be explicit."""

    def test_resolves_an_independent_step_for_each_lot(self) -> None:
        payload = captured_auction()
        second = json.loads(json.dumps(payload["biddingList"][0], default=str))
        second["internalId"] = "CO1.OTLN.99999999"
        second["biddingPriceListItems"][0]["id"] = "second-item"
        second["biddingPriceListItems"][0]["itemInitialPrice"] = "50000000.00"
        second["biddingPriceListItems"][0]["minBidDifference"] = "2.0"
        payload["biddingList"].append(second)

        ruleset = AuctionRuleset.from_get_auction(payload)

        assert len(ruleset.lots) == 2
        assert ruleset.lots[0].minimum_step == Centavos(115_670_523)
        # 2.0% of 50,000,000 = 1,000,000 COP
        assert ruleset.lots[1].minimum_step == Centavos(100_000_000)

    def test_the_auction_wide_step_is_the_sum_of_its_lots(self) -> None:
        payload = captured_auction()
        second = json.loads(json.dumps(payload["biddingList"][0], default=str))
        second["internalId"] = "CO1.OTLN.99999999"
        second["biddingPriceListItems"][0]["id"] = "second-item"
        second["biddingPriceListItems"][0]["itemInitialPrice"] = "50000000.00"
        second["biddingPriceListItems"][0]["minBidDifference"] = "2.0"
        payload["biddingList"].append(second)

        ruleset = AuctionRuleset.from_get_auction(payload)
        assert ruleset.minimum_step == Centavos(115_670_523 + 100_000_000)
