import db
import build_playlist


class FakeResolver:
    """resolve() answers from a dict {(artist, title): uri or None}; remembers what was asked."""
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def resolve(self, artist, title):
        self.asked.append((artist, title))
        uri = self.answers.get((artist, title))
        return uri, ("cached" if uri else "untried")


def cands(*names):
    return [(n, "Song", 100 - i) for i, n in enumerate(names)]


def test_tracks_without_an_id_are_skipped_and_the_next_one_takes_their_place():
    r = FakeResolver({("A", "Song"): "u1", ("C", "Song"): "u3", ("D", "Song"): "u4"})
    uris, scanned = build_playlist.pick_tracks(r, cands("A", "B", "C", "D", "E"), top=3)
    assert uris == ["u1", "u3", "u4"] and scanned == 4         # B has no id; E is never looked at


def test_it_stops_asking_once_top_tracks_are_found():
    r = FakeResolver({(n, "Song"): f"u{n}" for n in "ABCDE"})
    build_playlist.pick_tracks(r, cands("A", "B", "C", "D", "E"), top=2)
    assert len(r.asked) == 2


def test_two_spellings_of_the_same_track_count_once():
    r = FakeResolver({("A", "Song"): "u1", ("A.", "Song"): "u1", ("B", "Song"): "u2"})
    uris, _ = build_playlist.pick_tracks(r, cands("A", "A.", "B"), top=2)
    assert uris == ["u1", "u2"]


def test_fewer_found_than_wanted_when_the_candidates_run_out():
    r = FakeResolver({("A", "Song"): "u1"})
    uris, scanned = build_playlist.pick_tracks(r, cands("A", "B", "C"), top=5)
    assert uris == ["u1"] and scanned == 3


def _seed(conn):
    from conftest import play
    def p(artist, when):
        return play(artist, "Song", when)
    db.save_plays(conn, "zeilsteen", [p("Z", "2026-09-01 10:00"), p("Z", "2026-09-02 10:00")])
    db.save_plays(conn, "pingclass", [p("P", "2026-09-01 10:00")])
    db.save_plays(conn, "be/studiobrussel", [p("S", "2026-09-01 10:00")])


def test_include_keeps_only_that_station_also_when_stations_are_added_later(conn):
    _seed(conn)
    rows = build_playlist.top_tracks(conn, [], [], [], 10, stations=["zeilsteen"])
    assert [r[0] for r in rows] == ["Z"]


def test_include_takes_several_stations(conn):
    _seed(conn)
    rows = build_playlist.top_tracks(conn, [], [], [], 10, stations=["zeilsteen", "pingclass"])
    assert sorted(r[0] for r in rows) == ["P", "Z"]


def test_exclude_still_works_for_an_everything_but_mix(conn):
    _seed(conn)
    rows = build_playlist.top_tracks(conn, [], [], ["pingclass"], 10)
    assert sorted(r[0] for r in rows) == ["S", "Z"]
