"""The floor sheet: the one number a human must get right.

Every guard layer in the system enforces this sheet. None of them validates that the
sheet itself is correct — as the roadmap puts it, *a wrong FloorSheet defeats all five
guard layers from above*. So the sheet is treated as a controlled document: it declares
who computed it, from what, and who approved it, and it refuses to load if those are
missing.

The checks here are the ones software can actually make. They cannot tell you the floor
is *right*; they can only stop obviously-wrong sheets and unapproved ones from arming
the bot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from centinela.config.floor import FloorSheet, FloorSheetError
from centinela.domain.money import Centavos

VALID = """
auction_id: "e66a68a3-a14b-4d8a-8f8e-fd6333ade7ca"
tender: "SASI-001-2026"
currency: COP
computed_by: "Adriana Piñeros"
computed_on: "2026-03-01"
approved_by: "Consejo de Administración"
approved_on: "2026-03-01"
basis: "Tarifa mínima Supervigilancia 2026 + prestaciones + AIU 8%"
independent_check_by: "Contabilidad"
total_floor_cop: "90000000.00"
lots:
  - lot_id: "CO1.OTLN.68743791"
    floor_cop: "90000000.00"
    items:
      - item_id: "16c20486-4219-45da-beaf-1ee35f123949"
        floor_cop: "90000000.00"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "floor.yaml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadingAValidSheet:
    def test_loads_the_total_floor_as_centavos(self, tmp_path: Path) -> None:
        sheet = FloorSheet.load(write(tmp_path, VALID))
        assert sheet.total_floor == Centavos(9_000_000_000)

    def test_binds_the_sheet_to_one_auction(self, tmp_path: Path) -> None:
        sheet = FloorSheet.load(write(tmp_path, VALID))
        assert sheet.auction_id == "e66a68a3-a14b-4d8a-8f8e-fd6333ade7ca"

    def test_exposes_the_per_lot_floor(self, tmp_path: Path) -> None:
        sheet = FloorSheet.load(write(tmp_path, VALID))
        assert sheet.lot_floor("CO1.OTLN.68743791") == Centavos(9_000_000_000)

    def test_records_the_provenance_a_reviewer_would_ask_for(self, tmp_path: Path) -> None:
        sheet = FloorSheet.load(write(tmp_path, VALID))
        assert sheet.computed_by
        assert sheet.approved_by
        assert sheet.basis


class TestRefusingUnsafeSheets:
    def test_refuses_a_sheet_nobody_approved(self, tmp_path: Path) -> None:
        # An unapproved floor is a number somebody typed, not a decision anybody owns.
        text = VALID.replace(
            'approved_by: "Consejo de Administración"', 'approved_by: ""'
        )
        with pytest.raises(FloorSheetError, match="approved_by"):
            FloorSheet.load(write(tmp_path, text))

    def test_refuses_a_sheet_with_no_stated_basis(self, tmp_path: Path) -> None:
        text = VALID.replace(
            'basis: "Tarifa mínima Supervigilancia 2026 + prestaciones + AIU 8%"',
            'basis: ""',
        )
        with pytest.raises(FloorSheetError, match="basis"):
            FloorSheet.load(write(tmp_path, text))

    def test_refuses_a_zero_floor(self, tmp_path: Path) -> None:
        # A zero floor is indistinguishable from no floor at all, and would let the
        # bot bid itself to nothing.
        text = VALID.replace('total_floor_cop: "90000000.00"', 'total_floor_cop: "0.00"')
        with pytest.raises(FloorSheetError, match="positive"):
            FloorSheet.load(write(tmp_path, text))

    def test_refuses_a_negative_floor(self, tmp_path: Path) -> None:
        text = VALID.replace('total_floor_cop: "90000000.00"', 'total_floor_cop: "-1.00"')
        with pytest.raises(FloorSheetError, match="positive"):
            FloorSheet.load(write(tmp_path, text))

    def test_refuses_when_the_lot_floors_do_not_reach_the_declared_total(
        self, tmp_path: Path
    ) -> None:
        # A transcription slip here is exactly the failure the sheet exists to prevent:
        # the total would authorise a bid the lots do not support.
        text = VALID.replace(
            '    floor_cop: "90000000.00"', '    floor_cop: "50000000.00"'
        )
        with pytest.raises(FloorSheetError, match="lot floors"):
            FloorSheet.load(write(tmp_path, text))

    def test_refuses_when_item_floors_do_not_reach_their_lot_floor(
        self, tmp_path: Path
    ) -> None:
        text = VALID.replace(
            '        floor_cop: "90000000.00"', '        floor_cop: "10000000.00"'
        )
        with pytest.raises(FloorSheetError, match="item floors"):
            FloorSheet.load(write(tmp_path, text))

    def test_refuses_a_sheet_for_a_different_auction(self, tmp_path: Path) -> None:
        sheet = FloorSheet.load(write(tmp_path, VALID))
        with pytest.raises(FloorSheetError, match="different auction"):
            sheet.assert_applies_to("some-other-auction-id")

    def test_accepts_the_sheet_for_its_own_auction(self, tmp_path: Path) -> None:
        sheet = FloorSheet.load(write(tmp_path, VALID))
        sheet.assert_applies_to("e66a68a3-a14b-4d8a-8f8e-fd6333ade7ca")

    def test_refuses_sub_centavo_precision(self, tmp_path: Path) -> None:
        text = VALID.replace(
            'total_floor_cop: "90000000.00"', 'total_floor_cop: "90000000.005"'
        )
        with pytest.raises(FloorSheetError):
            FloorSheet.load(write(tmp_path, text))
