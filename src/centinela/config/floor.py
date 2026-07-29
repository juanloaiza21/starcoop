"""The floor sheet — the lowest price a human has authorised us to bid.

Everything else in this system is a mechanism. This is the decision. The bot does not
work out what a contract is worth; it competes down to a limit somebody with authority
already approved, and refuses to go past it.

That makes the sheet a controlled document rather than a config file. It records who
computed the floor, from what basis, and who approved it, and it will not load without
those. It is bound to a single auction, so a sheet cannot be reused by accident against
a tender it was never costed for.

**What these checks cannot do.** Nothing here can tell you the floor is *correct* — a
sheet can be internally consistent, properly signed, and still wrong, and if it is, it
defeats every guard layer from above. The only real control for that is human: an
independent second computation, by a different person using a different method. The
sheet records whether that happened; it cannot verify it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from centinela.domain.money import Centavos

__all__ = ["FloorSheet", "FloorSheetError", "LotFloor"]

REQUIRED_PROVENANCE = ("computed_by", "computed_on", "approved_by", "approved_on", "basis")


class FloorSheetError(Exception):
    """The floor sheet is unusable. The bot must not arm."""


def _centavos(raw: object, field: str) -> Centavos:
    if raw is None:
        raise FloorSheetError(f"{field} is missing")
    try:
        pesos = Decimal(str(raw))
    except InvalidOperation as exc:
        raise FloorSheetError(f"{field} is not a number: {raw!r}") from exc
    shifted = pesos * 100
    if shifted != shifted.to_integral_value():
        raise FloorSheetError(
            f"{field} carries sub-centavo precision ({pesos}); floors are exact amounts"
        )
    return Centavos(int(shifted))


@dataclass(frozen=True, slots=True)
class ItemFloor:
    item_id: str
    floor: Centavos


@dataclass(frozen=True, slots=True)
class LotFloor:
    lot_id: str
    floor: Centavos
    items: tuple[ItemFloor, ...]


@dataclass(frozen=True, slots=True)
class FloorSheet:
    auction_id: str
    tender: str
    currency: str
    total_floor: Centavos
    lots: tuple[LotFloor, ...]
    computed_by: str
    computed_on: str
    approved_by: str
    approved_on: str
    basis: str
    independent_check_by: str
    source_path: Path | None = None

    # -- construction ------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> FloorSheet:
        """Read and validate a floor sheet.

        Raises:
            FloorSheetError: if the sheet is unsigned, internally inconsistent, or
                declares a floor that cannot be trusted.
        """
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise FloorSheetError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise FloorSheetError(f"{path} does not contain a mapping")

        cls._require_provenance(raw)

        total = _centavos(raw.get("total_floor_cop"), "total_floor_cop")
        if total <= 0:
            raise FloorSheetError(
                "total_floor_cop must be positive; a zero or negative floor is "
                "indistinguishable from no floor and would let the bot bid to nothing"
            )

        lots = tuple(cls._parse_lot(entry) for entry in raw.get("lots") or [])
        if not lots:
            raise FloorSheetError("the sheet declares no lots")

        cls._require_internal_consistency(total, lots)

        return cls(
            auction_id=str(raw.get("auction_id", "")),
            tender=str(raw.get("tender", "")),
            currency=str(raw.get("currency", "COP")),
            total_floor=total,
            lots=lots,
            computed_by=str(raw["computed_by"]),
            computed_on=str(raw["computed_on"]),
            approved_by=str(raw["approved_by"]),
            approved_on=str(raw["approved_on"]),
            basis=str(raw["basis"]),
            independent_check_by=str(raw.get("independent_check_by", "")),
            source_path=path,
        )

    # -- queries -----------------------------------------------------------------

    def lot_floor(self, lot_id: str) -> Centavos:
        for lot in self.lots:
            if lot.lot_id == lot_id:
                return lot.floor
        raise FloorSheetError(f"the sheet declares no floor for lot {lot_id!r}")

    def item_floor(self, lot_id: str, item_id: str) -> Centavos:
        for lot in self.lots:
            if lot.lot_id != lot_id:
                continue
            for item in lot.items:
                if item.item_id == item_id:
                    return item.floor
        raise FloorSheetError(f"the sheet declares no floor for item {item_id!r}")

    def assert_applies_to(self, auction_id: str) -> None:
        """Guard against arming with a sheet costed for a different tender."""
        if self.auction_id != auction_id:
            raise FloorSheetError(
                f"this sheet was costed for a different auction "
                f"({self.auction_id!r}, tender {self.tender!r}), not {auction_id!r}"
            )

    @property
    def had_independent_check(self) -> bool:
        """Whether a second person recomputed the floor independently.

        Not enforced at load time — an operator may legitimately be running a dry run
        or an observation. It is enforced before arming for live bidding.
        """
        return bool(self.independent_check_by.strip())

    # -- internals ---------------------------------------------------------------

    @staticmethod
    def _require_provenance(raw: dict[str, Any]) -> None:
        for field in REQUIRED_PROVENANCE:
            if not str(raw.get(field) or "").strip():
                raise FloorSheetError(
                    f"{field} is required: a floor nobody has computed, dated and "
                    "approved is a number somebody typed, not a decision anybody owns"
                )

    @staticmethod
    def _parse_lot(entry: object) -> LotFloor:
        if not isinstance(entry, dict):
            raise FloorSheetError(f"lot entry is not a mapping: {entry!r}")
        items = tuple(
            ItemFloor(
                item_id=str(item.get("item_id", "")),
                floor=_centavos(item.get("floor_cop"), "item floor_cop"),
            )
            for item in entry.get("items") or []
            if isinstance(item, dict)
        )
        return LotFloor(
            lot_id=str(entry.get("lot_id", "")),
            floor=_centavos(entry.get("floor_cop"), "lot floor_cop"),
            items=items,
        )

    @staticmethod
    def _require_internal_consistency(total: Centavos, lots: tuple[LotFloor, ...]) -> None:
        """A transcription slip must not authorise a bid the parts do not support."""
        lot_total = sum(lot.floor for lot in lots)
        if lot_total < total:
            raise FloorSheetError(
                f"the lot floors sum to {lot_total} but the declared total floor is "
                f"{total}; the total would authorise a bid the lots do not support"
            )
        for lot in lots:
            if not lot.items:
                continue
            item_total = sum(item.floor for item in lot.items)
            if item_total < lot.floor:
                raise FloorSheetError(
                    f"in lot {lot.lot_id!r} the item floors sum to {item_total} but the "
                    f"lot floor is {lot.floor}"
                )
