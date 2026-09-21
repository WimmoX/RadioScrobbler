import pytest
import requests

import db
import reccobeats
from resolve import Resolver


class Resp:
    def __init__(self, status, payload=None):
        self.status_code, self._payload, self.headers = status, payload or {}, {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(reccobeats.time, "sleep", lambda s: None)


def test_a_timeout_is_retried_and_then_reported_as_a_failure_not_a_miss(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(reccobeats.requests, "get", boom)
    with pytest.raises(reccobeats.SearchFailed):
        reccobeats._get_with_retry("http://x", {})
    assert len(calls) == 3                                  # first try + the two retries


def test_a_timeout_that_clears_up_is_fine(monkeypatch):
    answers = iter([requests.exceptions.ConnectionError("boom"), Resp(200, {"content": []})])

    def get(*a, **k):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(reccobeats.requests, "get", get)
    assert reccobeats._get_with_retry("http://x", {}).status_code == 200


def test_a_server_error_is_a_failure_but_a_rejected_query_is_just_no_match(monkeypatch):
    monkeypatch.setattr(reccobeats.requests, "get", lambda *a, **k: Resp(503))
    with pytest.raises(reccobeats.SearchFailed):
        reccobeats._get_with_retry("http://x", {})
    monkeypatch.setattr(reccobeats.requests, "get", lambda *a, **k: Resp(400))
    assert reccobeats._get_with_retry("http://x", {}) is None


def test_the_resolver_records_nothing_when_reccobeats_does_not_answer(conn, monkeypatch):
    def down(artist, title):
        raise reccobeats.SearchFailed("timeout")

    monkeypatch.setattr(reccobeats, "search_track", down)
    r = Resolver(conn, None, None, None, progress_every=None)
    r.spotify_available = False
    assert r.resolve("Some Artist", "Some Song") == (None, "untried")
    assert r.reccobeats_errors == 1
    assert db.get_cached_match(conn, "Some Artist", "Some Song", "reccobeats") == (False, None)   # not cached as a miss
