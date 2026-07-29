"""The server clock is the only clock that matters.

Local wall-clock time is irrelevant: the auction ends when the *server* says it does.
The bot therefore estimates its offset from server time and extrapolates between the
sparse clock ticks the platform sends (median 9.3 s apart, gaps up to 30 s).

Two measured properties of the platform drive the design:

* `signalr/send` is a real round trip (p50 832 ms) but `signalr/poll` is a long poll
  that holds for up to 110 s. Estimating offset from a long poll is meaningless, so
  samples carry their round-trip time and wide ones are distrusted.
* Ticks are sparse, so `now()` must extrapolate rather than return the last tick.
"""

from __future__ import annotations

import pytest

from centinela.domain.clock import ClockSample, ServerClock

# A convenient fixed point; the units are seconds since an arbitrary local epoch.
LOCAL_START = 1000.0


def sample(*, server: float, sent: float, received: float) -> ClockSample:
    return ClockSample(server_time=server, sent_at=sent, received_at=received)


class TestOffsetEstimation:
    def test_estimates_zero_offset_for_a_perfectly_synchronised_server(self) -> None:
        clock = ServerClock()
        # Sent at 1000.0, replied at 1001.0, server said 1000.5 — the midpoint.
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))
        assert clock.offset_seconds == pytest.approx(0.0)

    def test_estimates_a_positive_offset_when_the_server_runs_ahead(self) -> None:
        clock = ServerClock()
        # Midpoint is 1000.5 local; server claims 1013.65 -> it runs 13.15 s ahead.
        clock.observe(sample(server=1013.65, sent=1000.0, received=1001.0))
        assert clock.offset_seconds == pytest.approx(13.15)

    def test_uncertainty_is_half_the_round_trip(self) -> None:
        clock = ServerClock()
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))
        assert clock.uncertainty_seconds == pytest.approx(0.5)

    def test_prefers_the_tightest_sample_over_the_most_recent_one(self) -> None:
        # NTP's core insight: a fast round trip pins the offset far better than a slow
        # one, regardless of which arrived last.
        clock = ServerClock()
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))  # rtt 1.0s
        clock.observe(sample(server=1005.0, sent=1002.0, received=1010.0))  # rtt 8.0s, noisy
        assert clock.uncertainty_seconds == pytest.approx(0.5)
        assert clock.offset_seconds == pytest.approx(0.0)

    def test_adopts_a_later_sample_when_it_is_tighter(self) -> None:
        clock = ServerClock()
        clock.observe(sample(server=1005.0, sent=1002.0, received=1010.0))  # rtt 8.0s
        clock.observe(sample(server=1013.20, sent=1013.0, received=1013.2))  # rtt 0.2s
        assert clock.uncertainty_seconds == pytest.approx(0.1)
        assert clock.offset_seconds == pytest.approx(0.1)

    def test_refuses_a_sample_whose_round_trip_is_too_wide_to_be_informative(self) -> None:
        # This is what a long-poll response looks like. Accepting it would move the
        # estimate by seconds on the strength of a measurement worth nothing.
        clock = ServerClock(max_useful_rtt_seconds=10.0)
        accepted = clock.observe(sample(server=1050.0, sent=1000.0, received=1110.0))
        assert accepted is False
        assert not clock.has_estimate


class TestExtrapolation:
    def test_extrapolates_between_sparse_ticks(self) -> None:
        clock = ServerClock()
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))
        # 30 seconds later with no new tick, server time has still advanced 30 seconds.
        assert clock.now(local_now=1031.0) == pytest.approx(1031.0)

    def test_applies_the_offset_when_extrapolating(self) -> None:
        clock = ServerClock()
        clock.observe(sample(server=1013.65, sent=1000.0, received=1001.0))
        assert clock.now(local_now=1010.0) == pytest.approx(1023.15)

    def test_refuses_to_guess_before_any_sample_has_arrived(self) -> None:
        clock = ServerClock()
        assert clock.has_estimate is False
        with pytest.raises(RuntimeError, match="no clock sample"):
            clock.now(local_now=1000.0)


class TestStaleness:
    def test_is_fresh_immediately_after_a_sample(self) -> None:
        clock = ServerClock(stale_after_seconds=30.0)
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))
        assert clock.is_fresh(local_now=1005.0)

    def test_goes_stale_once_ticks_stop_arriving(self) -> None:
        # Observed gaps between real ticks reached 30 s. Beyond that the estimate is
        # no longer trustworthy enough to drive an endgame decision.
        clock = ServerClock(stale_after_seconds=30.0)
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))
        assert not clock.is_fresh(local_now=1040.0)


class TestSecondsRemaining:
    def test_reports_time_left_against_the_server_clock(self) -> None:
        clock = ServerClock()
        clock.observe(sample(server=1013.65, sent=1000.0, received=1001.0))
        # Server time at local 1010.0 is 1023.15; auction ends at server 1100.0.
        assert clock.seconds_remaining(end_at=1100.0, local_now=1010.0) == pytest.approx(76.85)

    def test_reports_the_pessimistic_edge_so_we_never_believe_we_have_more_time(self) -> None:
        # With +/-0.5 s of uncertainty, assume the server is at the LATE end of the
        # range. Believing we have more time than we do is the error that lands a bid
        # after close; believing we have less merely costs a little margin.
        clock = ServerClock()
        clock.observe(sample(server=1000.5, sent=1000.0, received=1001.0))
        pessimistic = clock.seconds_remaining_pessimistic(end_at=1100.0, local_now=1010.0)
        assert pessimistic == pytest.approx(89.5)
        assert pessimistic < clock.seconds_remaining(end_at=1100.0, local_now=1010.0)
