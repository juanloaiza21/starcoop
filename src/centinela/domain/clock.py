"""Server-time estimation.

The auction ends when the *server* says it does. Our own wall clock is not evidence:
in the captured session the operator's machine was more than thirteen seconds away
from server time, which is longer than an entire submit cycle.

The platform offers ``auction.RequestClock`` / ``auction.ReceiveClock``, but the ticks
are sparse — median 9.3 s apart, with observed gaps up to 30 s — so this class holds an
offset estimate and extrapolates between ticks rather than treating the last tick as
"now".

Sample selection follows NTP's core insight: a *tight* round trip pins the offset far
better than a recent one. Each sample carries its round-trip time, and the estimate
adopts a new sample only when it is at least as tight as the one in hand. This is also
what protects us from ``signalr/poll``: a long poll can hold the connection for 110 s,
during which the server emits its clock at an arbitrary instant, so the midpoint
assumption is meaningless. Such samples are rejected outright.

Everything here is pure — local time arrives as a parameter. That keeps the module
exhaustively testable and keeps wall-clock calls out of the decision path.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ClockSample", "ServerClock"]

# A round trip wider than this tells us almost nothing about the offset. It is set well
# above the measured p90 of `signalr/send` (3.7 s) and well below a long-poll hold.
DEFAULT_MAX_USEFUL_RTT_SECONDS = 10.0

# Observed gaps between real clock ticks reached 30 s.
DEFAULT_STALE_AFTER_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ClockSample:
    """One observation of server time, with the local times bracketing it.

    Args:
        server_time: The instant the server reported, in server seconds.
        sent_at: Local time when we sent the request.
        received_at: Local time when the reply arrived.
    """

    server_time: float
    sent_at: float
    received_at: float

    def __post_init__(self) -> None:
        if self.received_at < self.sent_at:
            raise ValueError("received_at precedes sent_at")

    @property
    def round_trip_seconds(self) -> float:
        return self.received_at - self.sent_at

    @property
    def local_midpoint(self) -> float:
        """Our best guess at the local instant the server stamped its reply."""
        return (self.sent_at + self.received_at) / 2.0

    @property
    def offset_seconds(self) -> float:
        """How far the server clock runs ahead of ours."""
        return self.server_time - self.local_midpoint

    @property
    def uncertainty_seconds(self) -> float:
        """Worst-case error in :attr:`offset_seconds`.

        The server stamped its reply somewhere inside the round trip, so the midpoint
        can be wrong by at most half of it.
        """
        return self.round_trip_seconds / 2.0


class ServerClock:
    def __init__(
        self,
        *,
        max_useful_rtt_seconds: float = DEFAULT_MAX_USEFUL_RTT_SECONDS,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self._max_useful_rtt = max_useful_rtt_seconds
        self._stale_after = stale_after_seconds
        self._best: ClockSample | None = None

    # -- ingest ------------------------------------------------------------------

    def observe(self, sample: ClockSample) -> bool:
        """Offer a sample to the estimator.

        Returns:
            ``True`` if the sample was adopted, ``False`` if it was rejected as too
            wide or simply less informative than the one already held.
        """
        if sample.round_trip_seconds > self._max_useful_rtt:
            return False
        current = self._best
        if current is not None and sample.uncertainty_seconds > current.uncertainty_seconds:
            return False
        self._best = sample
        return True

    # -- state -------------------------------------------------------------------

    @property
    def has_estimate(self) -> bool:
        return self._best is not None

    @property
    def offset_seconds(self) -> float:
        return self._require().offset_seconds

    @property
    def uncertainty_seconds(self) -> float:
        return self._require().uncertainty_seconds

    def is_fresh(self, *, local_now: float) -> bool:
        """Whether the estimate is recent enough to drive a decision."""
        best = self._best
        if best is None:
            return False
        return (local_now - best.received_at) <= self._stale_after

    # -- queries -----------------------------------------------------------------

    def now(self, *, local_now: float) -> float:
        """Estimated server time, extrapolated from the local clock."""
        return local_now + self._require().offset_seconds

    def seconds_remaining(self, *, end_at: float, local_now: float) -> float:
        """Seconds until ``end_at`` in server time. The optimistic reading."""
        return end_at - self.now(local_now=local_now)

    def seconds_remaining_pessimistic(self, *, end_at: float, local_now: float) -> float:
        """Seconds remaining, assuming the server is at the late edge of our uncertainty.

        Every deadline decision uses this rather than :meth:`seconds_remaining`. The
        two errors are not symmetric: believing we have more time than we do lands a
        bid after the auction closes, while believing we have less costs a little
        margin. Always take the side that cannot lose the auction outright.
        """
        optimistic = self.seconds_remaining(end_at=end_at, local_now=local_now)
        return optimistic - self.uncertainty_seconds

    # -- internals ---------------------------------------------------------------

    def _require(self) -> ClockSample:
        if self._best is None:
            raise RuntimeError(
                "no clock sample yet — the bot must not act on local time alone"
            )
        return self._best
