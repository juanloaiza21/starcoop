"""The money path must never touch binary floating point.

A reverse auction rewards the lowest bid, so a rounding slip does not merely lose an
auction -- it *wins* one at a price the cooperative cannot deliver. Every peso amount is
an integer count of centavos, and `Decimal` is used only at the parsing boundary.

This is enforced mechanically rather than by review, because "we all know not to use
floats here" is exactly the kind of rule that decays. The architecture calls for a lint
banning the token in these modules; this test is that lint.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[1] / "src" / "centinela" / "domain"

# Timing is measured in float seconds and that is fine -- a millisecond of clock drift
# is not a financial error. Only the modules that compute money are constrained.
MONEY_MODULES = ("money.py", "rules.py", "auction.py")


def money_path_files() -> list[Path]:
    return [DOMAIN / name for name in MONEY_MODULES]


@pytest.mark.parametrize("path", money_path_files(), ids=lambda p: p.name)
def test_no_float_calls_in_the_money_path(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "float"
    ]
    assert not offenders, f"{path.name} calls float() at lines {offenders}"


@pytest.mark.parametrize("path", money_path_files(), ids=lambda p: p.name)
def test_no_float_literals_in_the_money_path(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert not offenders, f"{path.name} contains float literals at lines {offenders}"


@pytest.mark.parametrize("path", money_path_files(), ids=lambda p: p.name)
def test_no_float_annotations_in_the_money_path(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.annotation, ast.Name)
        and node.annotation.id == "float"
    ]
    assert not offenders, f"{path.name} annotates a value as float at lines {offenders}"
