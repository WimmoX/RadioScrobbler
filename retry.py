"""Shared retry-with-backoff for external APIs that respond 429 Too Many Requests.

See LessonsLearned.md ("Les 1") for why this exists as one shared
implementation instead of a copy per API client: an unthrottled retry loop
against Spotify's Search API once triggered a ~24h lockout.
"""
import time

MAX_RETRIES = 5
REQUEST_DELAY = 0.15  # seconds to wait after every successful call


class RateLimited(Exception):
    """Raise from an `attempt` callable to signal a 429; call_with_retry backs off and retries."""

    def __init__(self, wait_seconds: int):
        super().__init__(f"rate limited, retry after {wait_seconds}s")
        self.wait_seconds = max(wait_seconds, 1)


def call_with_retry(attempt, max_retries: int = MAX_RETRIES, delay: float = REQUEST_DELAY):
    """Call `attempt()` (no args), retrying with backoff if it raises RateLimited."""
    for _ in range(max_retries):
        try:
            result = attempt()
            time.sleep(delay)
            return result
        except RateLimited as e:
            print(f"  rate limited, waiting {e.wait_seconds}s...")
            time.sleep(e.wait_seconds)
    raise RuntimeError(f"Gave up after {max_retries} retries calling {attempt}")
