"""Budget for Spotify Search calls: a fixed number of calls per rolling 24h.

Spotify doesn't publish the Development Mode quota for Search. We used to
probe it (+25 per run that used up its limit, snap down on a real block, see
LessonsLearned.md Les 10), but on 2026-09-24 a real QUOTA_EXCEEDED came at
~700 calls in 24h. The limit is now fixed at CALL_LIMIT, safely below that,
and never changes by itself. A real block still stops all Search calls until
Spotify's Retry-After has passed.
"""
from datetime import datetime, timedelta

import db

CALL_LIMIT = 650


class QuotaExhausted(Exception):
    """Our own self-imposed limit for this window is used up (not a real Spotify block)."""


class BucketExhausted(QuotaExhausted):
    """The sub-allowance of the current bucket (see SearchBudget.set_bucket) is
    used up — NOT the overall limit, so it never counts as evidence about the
    real ceiling."""


class QuotaBlocked(Exception):
    """Spotify itself returned a 429 we should not retry (QUOTA_EXCEEDED)."""

    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"quota exceeded, retry after {retry_after_seconds}s")


class SearchBudget:
    """Use as: budget.check() before each Search call, budget.record_call()
    after a successful one, budget.record_block(seconds) on a real 429, and
    budget.finish() once at the end of a run."""

    def __init__(self, conn):
        self.conn = conn
        _, blocked_until = db.get_quota_state(conn)
        self.limit = CALL_LIMIT
        self.blocked_until = datetime.fromisoformat(blocked_until) if blocked_until else None
        self._hit_real_block = False
        self.bucket: str | None = None
        self.bucket_share: float | None = None

    def set_bucket(self, name: str | None, share: float | None = None) -> None:
        """Tag the following calls with a bucket, optionally capped at `share`
        (0..1) of the overall limit per 24h. A bucket without a share is only
        bound by the overall limit — so what a capped bucket leaves unused
        flows to the next one."""
        self.bucket, self.bucket_share = name, share

    @property
    def blocked(self) -> bool:
        """Spotify itself blocked us during this run."""
        return self._hit_real_block

    def exhausted(self) -> bool:
        """The overall self-imposed limit is used up right now."""
        return db.search_calls_in_last_24h(self.conn) >= self.limit

    def check(self) -> None:
        """Raise QuotaBlocked/QuotaExhausted if we shouldn't make a Search call right now."""
        if self.blocked_until and self.blocked_until > datetime.now():
            remaining = int((self.blocked_until - datetime.now()).total_seconds())
            raise QuotaBlocked(remaining)
        if db.search_calls_in_last_24h(self.conn) >= self.limit:
            raise QuotaExhausted()
        if self.bucket and self.bucket_share is not None:
            if db.search_calls_in_last_24h(self.conn, self.bucket) >= int(self.bucket_share * self.limit):
                raise BucketExhausted()

    def record_call(self) -> None:
        db.log_search_call(self.conn, self.bucket)

    def record_block(self, retry_after_seconds: int) -> None:
        """Call when Spotify returns a 429 we're treating as a real block."""
        self.blocked_until = datetime.now() + timedelta(seconds=retry_after_seconds)
        db.set_blocked_until(self.conn, self.blocked_until.isoformat())
        self._hit_real_block = True

    def finish(self) -> None:
        """Call once at the end of a run. Nothing to do since the limit is fixed;
        kept so the callers don't need to change if that ever comes back."""
