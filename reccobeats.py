"""Search ReccoBeats for a track by artist/title — a free, unauthenticated
fallback for when Spotify's own Search is rate-limited or quota-blocked
(see quota.py, LessonsLearned.md Les 10-11).

ReccoBeats' /v1/track/search already returns a Spotify href per hit, so a
match here doubles as a candidate Spotify URI — but it's unverified until
Spotify's own Search confirms it (see the `source` column on `tracks` in
db.py). See RECCOBEATS.md for the API itself.
"""
import requests

from matching import best_candidate
from retry import RateLimited, call_with_retry

SEARCH_URL = "https://api.reccobeats.com/v1/track/search"
REQUEST_DELAY = 0.2


def _get_with_retry(url: str, params: dict) -> requests.Response | None:
    """Returns None (not a match) on a non-429 HTTP error instead of raising —
    a single track ReccoBeats doesn't like (e.g. a rejected query) shouldn't
    crash a whole backlog batch of hundreds of other, unrelated tracks."""
    def attempt():
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 429:
            raise RateLimited(int(response.headers.get("Retry-After", 1)))
        return response
    response = call_with_retry(attempt, delay=REQUEST_DELAY)
    if response.status_code >= 400:
        return None
    return response


def search_track(artist: str, title: str) -> dict | None:
    """Returns {"uri", "name", "artists", "reccobeats_id"} for the best match, or None.

    Unlike Spotify, ReccoBeats' searchText looks like a fairly literal phrase
    match rather than fuzzy full-text: combining "{artist} {title}" into one
    string very often returns zero results even when the track exists,
    because that exact phrase doesn't appear anywhere. A title-only query
    finds it reliably instead — Blind Guardian's "Bright Eyes" showed up at
    position 9 of 200 total results for just "Bright Eyes" — so we search on
    title alone (with a larger page to raise the odds of the right artist
    being on it) and let best_candidate() pick the right artist out of the
    results, same as we already do for Spotify's own search.

    ReccoBeats also rejects searchText shorter than 3 characters (400,
    "size must be between 3 and 1000") — real short titles do exist (e.g.
    Doe Maar's "Pa"), so we just skip the call and report no match instead
    of erroring."""
    if len(title) < 3:
        return None

    response = _get_with_retry(SEARCH_URL, {"searchText": title, "size": 50})
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
        })

    best = best_candidate(candidates, artist, title)
    if not best:
        return None
    return {
        "uri": best["uri"],
        "name": best["name"],
        "artists": best["artists"],
        "reccobeats_id": best["_reccobeats_id"],
    }
