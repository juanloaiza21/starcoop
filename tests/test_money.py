"""Money is integer centavos. A float slip here wins a contract at a loss.

The platform speaks two money dialects and the bot must read both:
  - es-CO display strings in error messages: "1.156.705,23 COP"
  - raw decimal strings in JSON payloads: "115670523.000000000000"
"""

import pytest

from centinela.domain.money import Centavos, parse_cop_display, parse_decimal_string


class TestParseCopDisplay:
    """es-CO formatting: '.' groups thousands, ',' is the decimal separator."""

    def test_parses_the_min_bid_the_platform_quoted_when_it_rejected_our_bid(self) -> None:
        # Ground truth from the capture: ValidateBid returned minBid "1.156.705,23 COP".
        assert parse_cop_display("1.156.705,23 COP") == Centavos(115_670_523)

    def test_parses_without_the_currency_suffix(self) -> None:
        assert parse_cop_display("1.156.705,23") == Centavos(115_670_523)

    def test_parses_a_whole_number_of_pesos(self) -> None:
        assert parse_cop_display("115.670.523,00 COP") == Centavos(11_567_052_300)

    def test_parses_a_value_with_no_thousands_separator(self) -> None:
        assert parse_cop_display("500,50 COP") == Centavos(50_050)

    def test_rejects_anglo_formatting_rather_than_silently_misreading_it(self) -> None:
        # "1,156,705.23" in es-CO would mean 1.15 pesos. Guessing here is how you
        # bid a thousand times too little, so refuse instead.
        with pytest.raises(ValueError, match="ambiguous"):
            parse_cop_display("1,156,705.23")

    def test_rejects_a_string_with_no_digits(self) -> None:
        with pytest.raises(ValueError):
            parse_cop_display("COP")


class TestParseDecimalString:
    """Raw JSON payload values, e.g. itemInitialPrice."""

    def test_parses_the_captured_item_initial_price(self) -> None:
        assert parse_decimal_string("115670523.000000000000") == Centavos(11_567_052_300)

    def test_parses_a_value_with_two_meaningful_decimals(self) -> None:
        assert parse_decimal_string("1156705.23") == Centavos(115_670_523)

    def test_parses_an_integer_string(self) -> None:
        assert parse_decimal_string("1") == Centavos(100)

    def test_rejects_precision_below_a_centavo_rather_than_rounding_silently(self) -> None:
        # Silently rounding a sub-centavo value hides a unit-confusion bug upstream.
        with pytest.raises(ValueError, match="sub-centavo"):
            parse_decimal_string("1.005")

    def test_accepts_trailing_zeros_beyond_a_centavo(self) -> None:
        assert parse_decimal_string("1.2300000") == Centavos(123)
