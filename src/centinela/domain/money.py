"""Money as integer centavos.

Every peso amount in this system is an ``int`` count of centavos. Floats are banned
in the money path: a reverse auction rewards the lowest bid, so a rounding slip does
not merely lose an auction, it *wins* one at a price the cooperative cannot deliver.

Two dialects arrive from the platform:

* **es-CO display strings**, used in error messages: ``"1.156.705,23 COP"`` — ``.``
  groups thousands and ``,`` is the decimal separator.
* **raw decimal strings**, used in JSON payloads: ``"115670523.000000000000"``.

Both parsers refuse ambiguous or lossy input rather than guessing. Guessing wrong by a
factor of a thousand is exactly the failure this module exists to prevent.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import NewType

__all__ = ["Centavos", "format_cop", "parse_cop_display", "parse_decimal_string"]

Centavos = NewType("Centavos", int)

_CENTAVOS_PER_PESO = 100

# es-CO display: optional thousands groups separated by '.', optional ',' decimals.
_ES_CO_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^\d+(?:,\d+)?$")


def parse_cop_display(text: str) -> Centavos:
    """Parse an es-CO money string such as ``"1.156.705,23 COP"``.

    Raises:
        ValueError: if the string is empty of digits, or is formatted in a way that
            could be read as either es-CO or anglo convention.
    """
    cleaned = text.replace("COP", "").replace("$", "").strip()
    cleaned = cleaned.replace("\xa0", "").replace(" ", "")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        raise ValueError(f"no digits in money string: {text!r}")

    # Anglo formatting ("1,156,705.23") would parse to a thousand-fold different value
    # under es-CO rules. Refuse rather than pick.
    last_comma, last_dot = cleaned.rfind(","), cleaned.rfind(".")
    if last_comma != -1 and last_dot > last_comma:
        raise ValueError(f"ambiguous money format, decimal separator unclear: {text!r}")
    if cleaned.count(",") > 1:
        raise ValueError(f"ambiguous money format, multiple decimal separators: {text!r}")

    if not _ES_CO_RE.match(cleaned):
        raise ValueError(f"not a valid es-CO money string: {text!r}")

    return _to_centavos(Decimal(cleaned.replace(".", "").replace(",", ".")), text)


def parse_decimal_string(text: str) -> Centavos:
    """Parse a raw JSON decimal string such as ``"115670523.000000000000"``.

    Raises:
        ValueError: if the value is not a number, or carries precision finer than a
            centavo. Sub-centavo input signals a unit-confusion bug upstream and must
            not be silently rounded away.
    """
    stripped = text.strip()
    try:
        value = Decimal(stripped)
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal number: {text!r}") from exc
    return _to_centavos(value, text)


def _to_centavos(pesos: Decimal, original: str) -> Centavos:
    shifted = pesos * _CENTAVOS_PER_PESO
    if shifted != shifted.to_integral_value():
        raise ValueError(f"sub-centavo precision is not representable: {original!r}")
    return Centavos(int(shifted))


def format_cop(amount: Centavos) -> str:
    """Render centavos as an es-CO string, for logs and the operator display."""
    negative = amount < 0
    pesos, centavos = divmod(abs(int(amount)), _CENTAVOS_PER_PESO)
    grouped = f"{pesos:,}".replace(",", ".")
    return f"{'-' if negative else ''}{grouped},{centavos:02d} COP"
