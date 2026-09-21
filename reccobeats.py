"""Search ReccoBeats for a track by artist/title — a free, unauthenticated
fallback for when Spotify's own Search is rate-limited or quota-blocked
(see quota.py, LessonsLearned.md Les 10-11).

ReccoBeats' /v1/track/search already returns a Spotify href per hit, so a
match here doubles as a candidate Spotify URI — but it's unverified until
Spotify's own Search confirms it (see the `source` column on `tracks` in
db.py). See RECCOBEATS.md for the API itself.
"""
import time

import requests

from matching import best_candidate, clean_title, text_variants
from retry import RateLimited, call_with_retry

SEARCH_URL = "https://api.reccobeats.com/v1/track/search"
# Misses recorded before this date came from the plain title-only search;
# search_track() has tried the other readings of the text since (matching.text_variants),
# so those misses are worth one more try (match_reccobeats_backlog.py --retry-misses).
ALGORITHM_DATE = "2026-09-21"
REQUEST_DELAY = 0.2


class SearchFailed(Exception):
    """ReccoBeats could not answer (timeout, connection error, server error).
    That is NOT "no match": nothing may be cached about the track, or a
    passing outage would turn into hundreds of permanent misses."""


RETRY_WAITS = (2, 5)   # seconds before the 2nd and 3rd attempt after a network/server failure


def _get_with_retry(url: str, params: dict) -> requests.Response | None:
    """Returns None (a rejected query = no match) on a 4xx other than 429 —
    a single track ReccoBeats doesn't like shouldn't crash a whole backlog batch.
    A timeout, connection error or 5xx is retried a few times and then raises
    SearchFailed, so callers can skip the track without recording a miss."""
    def attempt():
        try:
            response = requests.get(url, params=params, timeout=15)
        except requests.RequestException as e:
            raise SearchFailed(f"{type(e).__name__}: {e}") from e
        if response.status_code == 429:
            raise RateLimited(int(response.headers.get("Retry-After", 1)))
        if response.status_code >= 500:
            raise SearchFailed(f"HTTP {response.status_code}")
        return response

    for wait in (*RETRY_WAITS, None):
        try:
            response = call_with_retry(attempt, delay=REQUEST_DELAY)
            break
        except SearchFailed:
            if wait is None:
                raise
            time.sleep(wait)
    if response.status_code >= 400:
        return None
    return response


def _search(query: str, artist: str, title: str) -> dict | None:
    """One ReccoBeats search call for `query` (title-only, see search_track),
    best match for (artist, title) or None."""
    response = _get_with_retry(SEARCH_URL, {"searchText": query, "size": 50})
    if response is None:
        return None
    items = response.json().get("content", [])

    # Normalize to the shape best_candidate() expects (Spotify's item shape):
    # "name" instead of "trackTitle", "uri" extracted from the Spotify href.
    candidates = []
    for item in items:
        href = item.get("href")
        if not href:
            continue
        candidates.append({
            "name": item["trackTitle"],
            "artists": item["artists"],
            "uri": f"spotify:track:{href.rstrip('/').rsplit('/', 1)[-1]}",
            "_reccobeats_id": item["id"],
            "_isrc": item.get("isrc"),
        })

    best = best_candidate(candidates, artist, title)
    if not best:
        return None
    return {
        "uri": best["uri"],
        "name": best["name"],
        "artists": best["artists"],
        "reccobeats_id": best["_reccobeats_id"],
        "isrc": best["_isrc"],
    }


def search_track(artist: str, title: str) -> dict | None:
    """Returns {"uri", "name", "artists", "reccobeats_id", "isrc", "variant"}
    for the best match, or None.

    Unlike Spotify, ReccoBeats' searchText looks like a fairly literal phrase
    match rather than fuzzy full-text: combining "{artist} {title}" into one
    string very often returns zero results even when the track exists,
    because that exact phrase doesn't appear anywhere. A title-only query
    finds it reliably instead — Blind Guardian's "Bright Eyes" showed up at
    position 9 of 200 total results for just "Bright Eyes" — so we search on
    title alone (with a larger page to raise the odds of the right artist
    being on it) and let best_candidate() pick the right artist out of the
    results, same as we already do for Spotify's own search.

    The scraped text is often garbled, so when the text as scraped finds
    nothing we also try the other readings of it (matching.text_variants():
    cleaned of version tags and broken apostrophes, a hyphenated artist
    re-joined, artist and title swapped) — one more call each, only for tracks
    that would otherwise be a miss. `variant` says which reading matched.

    ReccoBeats also rejects searchText shorter than 3 characters (400,
    "size must be between 3 and 1000") — real short titles do exist (e.g.
    Doe Maar's "Pa"), so we just skip that query instead of erroring."""
    tried = set()
    for v_artist, v_title, label in text_variants(artist, title):
        query = clean_title(v_title)
        if len(query) < 3 or query.lower() in tried:
            continue
        tried.add(query.lower())
        match = _search(query, v_artist, v_title)
        if match:
            match["variant"] = label
            return match
    return None
