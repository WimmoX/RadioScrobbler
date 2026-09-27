import pytest
import spotipy

import db
import quota
import sync_playlist
from resolve import Resolver


class Budget:
    def __init__(self):
        self.calls = 0

    def check(self):
        pass

    def record_call(self):
        self.calls += 1


class FakeSp:
    """sp.search that raises the given answers in order (an Exception is raised, anything else returned)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.searches = 0

    def search(self, **kwargs):
        self.searches += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def bad_gateway():
    return spotipy.SpotifyException(502, -1, "An unexpected error occurred. Please try again later.")


EMPTY = {"tracks": {"items": []}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(sync_playlist.time, "sleep", lambda s: None)


def test_a_502_that_clears_up_is_fine():
    sp, budget = FakeSp(bad_gateway(), EMPTY, EMPTY), Budget()
    assert sync_playlist.find_track_match(sp, "Sven Versteeg", "Houdini", budget) is None
    assert sp.searches == 3                    # failed try, then structured + plain query
    assert budget.calls == 3                   # the failed call is counted too


def test_a_lasting_502_is_reported_as_unavailable_not_as_no_match():
    sp = FakeSp(*[bad_gateway()] * 4)
    with pytest.raises(quota.SpotifyUnavailable):
        sync_playlist.find_track_match(sp, "Sven Versteeg", "Houdini", Budget())
    assert sp.searches == 4                    # first try + three retries


def test_a_4xx_is_not_retried():
    sp = FakeSp(spotipy.SpotifyException(400, -1, "bad request"))
    with pytest.raises(spotipy.SpotifyException):
        sync_playlist.find_track_match(sp, "A", "B", Budget())
    assert sp.searches == 1


def down(sp, artist, title, budget):
    raise quota.SpotifyUnavailable("HTTP 502")


def test_the_resolver_records_nothing_when_spotify_does_not_answer(conn):
    r = Resolver(conn, None, None, down, progress_every=None)
    r.allow_fallback = False
    assert r.resolve("Some Artist", "Some Song") == (None, "untried")
    assert r.spotify_errors == 1
    assert db.get_cached_match(conn, "Some Artist", "Some Song") == (False, None)   # not cached as a miss
    assert r.spotify_available                                                        # one failure: keep asking


def test_the_resolver_stops_asking_spotify_after_failures_in_a_row(conn):
    r = Resolver(conn, None, None, down, progress_every=None)
    r.allow_fallback = False
    for i in range(3):
        r.resolve("Artist", f"Song {i}")
    assert not r.spotify_available
