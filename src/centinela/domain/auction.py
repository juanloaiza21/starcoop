"""Resolving an auction's rules from the server's own payload.

The ruleset is *read*, never assumed. Two rules govern this module:

**Read the full ``GetAuction``, never ``GetAuctionSimple``.** The simple variant returns
opaque integer enums (``minimumBidType: 1``) and omits ``minBidDifference`` entirely. A
ruleset built from it would invent a minimum step, and the bot would spend
audit-visible rejections discovering that.

**Fail closed.** Every auction property outside the single configuration we have
actually observed raises :class:`UnsupportedAuctionError`. Refusing to bid costs one
opportunity; guessing at rules we have no evidence for can cost a contract at a loss.
The list of accepted values is therefore deliberately narrow, and widening it requires
new evidence rather than new optimism.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from centinela.domain.money import Centavos

__all__ = ["AuctionRuleset", "Item", "Lot", "UnsupportedAuctionError"]

# The only values we have observed and can defend. See docs/02-auction-mechanics.md.
SUPPORTED_AUCTION_TYPE = "Reversed"
SUPPORTED_EVALUATION_TYPE = "BestPrice"
SUPPORTED_MINIMUM_BID_TYPE = "Percentage"


class UnsupportedAuctionError(Exception):
    """The auction is not a shape this bot has evidence for. The bot must not arm."""


def _decimal(value: object, field: str) -> Decimal:
    if value is None:
        raise UnsupportedAuctionError(f"{field} is missing from the payload")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise UnsupportedAuctionError(f"{field} is not a number: {value!r}") from exc


def _to_centavos(pesos: Decimal, field: str) -> Centavos:
    shifted = pesos * 100
    if shifted != shifted.to_integral_value():
        raise UnsupportedAuctionError(f"{field} carries sub-centavo precision: {pesos}")
    return Centavos(int(shifted))


@dataclass(frozen=True, slots=True)
class Item:
    item_id: str
    reference: str
    description: str
    unit: str
    quantity: Decimal
    initial_price: Centavos
    minimum_step: Centavos
    """Absolute step for this item, resolved from ``minBidDifference``.

    With ``minimumBidType: "Percentage"`` the step is a percentage of the item's
    *initial* price, which makes it a fixed amount for the whole auction. It does not
    shrink as the price falls.
    """


@dataclass(frozen=True, slots=True)
class Lot:
    lot_id: str
    name: str
    items: tuple[Item, ...]

    @property
    def minimum_step(self) -> Centavos:
        return Centavos(sum(item.minimum_step for item in self.items))

    @property
    def initial_price(self) -> Centavos:
        return Centavos(sum(item.initial_price for item in self.items))


@dataclass(frozen=True, slots=True)
class AuctionRuleset:
    auction_id: str
    is_reverse: bool
    currency: str
    decimal_places: int
    start_price: Centavos
    lots: tuple[Lot, ...]
    extension_window_seconds: int
    """A bid landing within this many seconds of the end extends the auction."""
    extension_period_seconds: int
    """How much time an extension adds."""
    extensions_enabled: bool
    is_test_auction: bool

    @property
    def minimum_step(self) -> Centavos:
        """The auction-wide step: the sum across lots.

        Validation applies independently at item, lot and total scope, so a legal bid
        must satisfy all three. This is the total-scope figure only.
        """
        return Centavos(sum(lot.minimum_step for lot in self.lots))

    @classmethod
    def from_get_auction(cls, payload: dict[str, Any]) -> AuctionRuleset:
        """Build a ruleset from a full ``GetAuction`` response.

        Raises:
            UnsupportedAuctionError: for any shape outside the observed configuration.
        """
        cls._reject_unsupported_shape(payload)

        lots = tuple(cls._parse_lot(raw) for raw in payload.get("biddingList") or [])
        if not lots:
            raise UnsupportedAuctionError("the auction declares no lots")

        return cls(
            auction_id=str(payload.get("id", "")),
            is_reverse=True,
            currency=str(payload.get("currencyCode", "COP")),
            decimal_places=int(payload.get("decimalNum", 2)),
            start_price=_to_centavos(_decimal(payload.get("startPrice"), "startPrice"),
                                     "startPrice"),
            lots=lots,
            extension_window_seconds=(
                int(payload.get("extraTimeLastMinutesStartPeriod", 0)) * 60
            ),
            extension_period_seconds=int(payload.get("extraTimePeriod", 0)) * 60,
            extensions_enabled=bool(payload.get("extraTimeAutomaticAllow", False)),
            is_test_auction=bool(payload.get("testAuction", False)),
        )

    # -- internals ---------------------------------------------------------------

    @staticmethod
    def _reject_unsupported_shape(payload: dict[str, Any]) -> None:
        auction_type = payload.get("auctionTypeCode")
        if auction_type != SUPPORTED_AUCTION_TYPE:
            raise UnsupportedAuctionError(
                f"auctionTypeCode {auction_type!r} is not {SUPPORTED_AUCTION_TYPE!r}; "
                "this bot only understands reverse auctions where lower wins"
            )

        evaluation = payload.get("auctionEvaluationTypeCode")
        if evaluation != SUPPORTED_EVALUATION_TYPE:
            raise UnsupportedAuctionError(
                f"auctionEvaluationTypeCode {evaluation!r} is not "
                f"{SUPPORTED_EVALUATION_TYPE!r}; anything else scores on more than price"
            )

        bid_type = payload.get("minimumBidType")
        if bid_type != SUPPORTED_MINIMUM_BID_TYPE:
            raise UnsupportedAuctionError(
                f"minimumBidType {bid_type!r} is not {SUPPORTED_MINIMUM_BID_TYPE!r}. "
                "Only Percentage is confirmed; another type changes the arithmetic of "
                "every bid and we have no evidence for how. Note GetAuctionSimple "
                "returns the integer 1 here — read the full GetAuction instead"
            )

        if payload.get("scoreCalcFormula") is not None:
            raise UnsupportedAuctionError(
                "the auction carries a scoreCalcFormula, so price is not the only "
                "criterion and a price-only bot must not arm"
            )

        if payload.get("evaluationRules"):
            raise UnsupportedAuctionError(
                "the auction carries evaluationRules, which this bot does not model"
            )

    @staticmethod
    def _parse_lot(raw: dict[str, Any]) -> Lot:
        items = tuple(
            AuctionRuleset._parse_item(item) for item in raw.get("biddingPriceListItems") or []
        )
        if not items:
            raise UnsupportedAuctionError(f"lot {raw.get('internalId')!r} declares no items")
        return Lot(lot_id=str(raw.get("internalId", "")), name=str(raw.get("lotName", "")),
                   items=items)

    @staticmethod
    def _parse_item(raw: dict[str, Any]) -> Item:
        if "minBidDifference" not in raw:
            raise UnsupportedAuctionError(
                f"item {raw.get('id')!r} has no minBidDifference. This is what a "
                "GetAuctionSimple payload looks like; the full GetAuction carries it"
            )
        initial = _to_centavos(
            _decimal(raw.get("itemInitialPrice"), "itemInitialPrice"), "itemInitialPrice"
        )
        percent = _decimal(raw.get("minBidDifference"), "minBidDifference")
        step = _to_centavos(
            (_decimal(raw.get("itemInitialPrice"), "itemInitialPrice") * percent / 100),
            "minimum step",
        )
        return Item(
            item_id=str(raw.get("id", "")),
            reference=str(raw.get("itemInternalReference", "")),
            description=str(raw.get("itemDescription", "")),
            unit=str(raw.get("itemUnit", "")),
            quantity=_decimal(raw.get("itemQuantity", 1), "itemQuantity"),
            initial_price=initial,
            minimum_step=step,
        )
