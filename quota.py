"""Self-calibrating budget for Spotify Search calls.

Spotify doesn't publish the Development Mode quota for Search, and it can
change — so instead of guessing a fixed number, we probe it: a run that
exhausts its self-imposed limit without ever getting a real 429 nudges the
limit up a bit for next time (additive increase). A run that DOES get a
real "quota exceeded" response snaps the limit straight down to however
many calls actually succeeded in the trailing 24h window before the block —
that's the true, current ceiling, not a guess. See LessonsLearned.md.
"""
from datetime import datetime, timedelta

import db

STEP = 10


class QuotaExhausted(Exception):
    """Our own self-imposed limit for this window is used up (not a real Spotify block)."""


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
        self.limit, blocked_until = db.get_quota_state(conn)
        self.blocked_until = datetime.fromisoformat(blocked_until) if blocked_until else None
        self._hit_real_block = False
        self._hit_own_limit = False

    def check(self) -> None:
        """Raise QuotaBlocked/QuotaExhausted if we shouldn't make a Search call right now."""
        if self.blocked_until and self.blocked_until > datetime.now():
            remaining = int((self.blocked_until - datetime.now()).total_seconds())
            raise QuotaBlocked(remaining)
        if db.search_calls_in_last_24h(self.conn) >= self.limit:
            self._hit_own_limit = True
            raise QuotaExhausted()

    def record_call(self) -> None:
        db.log_search_call(self.conn)

    def record_block(self, retry_after_seconds: int) -> None:
        """Call when Spotify returns a 429 we're treating as a real block."""
        actual = db.search_calls_in_last_24h(self.conn)
        self.limit = actual
        self.blocked_until = datetime.now() + timedelta(seconds=retry_after_seconds)
        db.set_call_limit(self.conn, self.limit)
        db.set_blocked_until(self.conn, self.blocked_until.isoformat())
        self._hit_real_block = True

    def finish(self) -> None:
        """Call once at the end of a run. Only nudge the limit up if this run
        actually pushed against it (self-imposed QuotaExhausted was raised)
        AND Spotify never complained — that's the only situation with real
        evidence there's headroom. A run that finishes early just because
        there weren't many new tracks to look up proves nothing about the
        real ceiling and must NOT inflate the limit — otherwise a handful of
        small, harmless sessions would ratchet it up for no reason."""
        if self._hit_own_limit and not self._hit_real_block:
            self.limit += STEP
            db.set_call_limit(self.conn, self.limit)
