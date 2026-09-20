"""Resolve a played (artist, title) to a Spotify URI — the one place that
decides *which source gets asked, and what may be cached about the answer*.
Shared by sync_playlist.py and build_playlist.py.

Order: the local cache first, then Spotify's own Search while its budget
lasts (quota.py), then ReccoBeats once it doesn't (no quota of its own
observed so far — only its ~2s per lookup slows us down).

What may be cached (LessonsLearned.md, Les 13): a "no match" is a claim
about one source, and is only recorded for the source that actually looked.
  - Spotify looked and found nothing -> track_match(service='spotify', NULL)
  - ReccoBeats looked and found nothing -> track_match(service='reccobeats', NULL)
    (only stops ReccoBeats from asking again; Spotify may still try later)
  - Spotify was never asked -> nothing recorded for Spotify
A ReccoBeats hit is stored as an *unverified* Spotify match (see
track_services.verified) and is retried on Spotify in a later run.
"""
import time

import db
import quota
import reccobeats

PROGRESS_EVERY = 100


class Resolver:
    def __init__(self, conn, sp, budget, spotify_search, total: int | None = None,
                 progress_every: int | None = PROGRESS_EVERY):
        """`spotify_search(sp, artist, title, budget)` is sync_playlist.find_track_match;
        passed in rather than imported, because sync_playlist imports this module.
        Prints a progress line every `progress_every` tracks handled (cached or not),
        so a long run visibly does something; `total` only adds the "/N"."""
        self.total = total
        self.processed = 0
        self.spotify_search = spotify_search
        self.conn = conn
        self.sp = sp
        self.budget = budget
        self.progress_every = progress_every
        self.spotify_available = True
        self.spotify_lookups = 0
        self.reccobeats_lookups = 0
        self.started = time.monotonic()

    @property
    def new_lookups(self) -> int:
        return self.spotify_lookups + self.reccobeats_lookups

    def resolve(self, artist: str, title: str) -> tuple[str | None, str]:
        result = self._resolve(artist, title)
        self.processed += 1
        self._progress()
        return result

    def _resolve(self, artist: str, title: str) -> tuple[str | None, str]:
        """Returns (uri or None, how) where how is one of:
        'cached'    already resolved (uri None = confirmed no match)
        'spotify'   looked up on Spotify just now (uri None = Spotify has no match)
        'reccobeats' found via ReccoBeats just now (unverified)
        'candidate' Spotify unavailable; keeping the earlier ReccoBeats candidate
        'untried'   Spotify unavailable and ReccoBeats has nothing: nothing recorded
                    for Spotify, so a later run can still ask it"""
        is_cached, uri = db.get_cached_match(self.conn, artist, title)
        if is_cached:
            return uri, "cached"

        if self.spotify_available:
            try:
                match = self.spotify_search(self.sp, artist, title, self.budget)
            except quota.QuotaExhausted:
                self.spotify_available = False
            except quota.QuotaBlocked as e:
                self.budget.record_block(e.retry_after_seconds)
                self.spotify_available = False
            else:
                db.save_match(
                    self.conn, artist, title, match["uri"] if match else None,
                    match["name"] if match else None,
                    [a["name"] for a in match["artists"]] if match else None,
                )
                self.spotify_lookups += 1
                return (match["uri"] if match else None), "spotify"

        if uri:
            return uri, "candidate"
        if db.get_cached_match(self.conn, artist, title, service="reccobeats")[0]:
            return None, "untried"

        match = reccobeats.search_track(artist, title)
        self.reccobeats_lookups += 1
        if match is None:
            db.save_match(self.conn, artist, title, None, service="reccobeats")
            return None, "untried"
        db.save_match(
            self.conn, artist, title, match["uri"], match["name"],
            [a["name"] for a in match["artists"]],
            source="reccobeats", reccobeats_id=match["reccobeats_id"], isrc=match.get("isrc"),
        )
        return match["uri"], "reccobeats"

    def _progress(self) -> None:
        if self.progress_every and self.processed % self.progress_every == 0:
            elapsed = int(time.monotonic() - self.started)
            of = f"/{self.total}" if self.total else ""
            print(f"  ...{self.processed}{of} tracks handled ({self.spotify_lookups} Spotify, "
                  f"{self.reccobeats_lookups} ReccoBeats lookups), {elapsed // 60}m{elapsed % 60:02d}s",
                  flush=True)
