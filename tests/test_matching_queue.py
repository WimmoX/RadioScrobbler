from datetime import datetime, timedelta

import db
import matching_queue as mq
from conftest import play


def save(conn, station, *plays):
    db.save_plays(conn, station, plays)


def test_popularity_counts_every_play_across_stations(conn):
    save(conn, "radio2", play("Queen", "Bohemian Rhapsody", "2026-09-01 10:00"),
         play("Queen", "Bohemian Rhapsody", "2026-09-02 10:00"))
    save(conn, "npo3fm", play("Queen", "Bohemian Rhapsody", "2026-09-03 10:00"))
    (item,) = mq.build_queue(conn)
    assert item.play_count == 3


def test_a_play_seen_by_two_sources_counts_once(conn):
    # relisten reports it 2 minutes before onlineradiobox, and spells the artist differently
    save(conn, "radio2", play("Tiësto", "The Business", "2026-09-01 10:00"))
    save(conn, "radio2", play("Tiesto", "The Business", "2026-09-01 10:02"))
    (item,) = mq.build_queue(conn)
    assert item.play_count == 1


def test_spellings_of_one_track_share_one_queue_item(conn):
    save(conn, "radio2", play("Earth, Wind & Fire", "Star", "2026-09-01 10:00"),
         play("Earth, Wind & Fire", "Star", "2026-09-02 10:00"))
    save(conn, "npo3fm", play("Earth Wind & Fire", "Star", "2026-09-03 10:00"))
    (item,) = mq.build_queue(conn)
    assert item.play_count == 3
    assert len(item.variants) == 2
    assert item.artist == "Earth, Wind & Fire"          # the most played spelling is the one looked up


def test_repeated_track_goes_before_a_one_off_and_recency_breaks_ties(conn):
    save(conn, "radio2", play("A", "One-off", "2026-09-10 10:00"))
    save(conn, "radio2", play("B", "Twice, long ago", "2026-08-01 10:00"), play("B", "Twice, long ago", "2026-08-02 10:00"))
    save(conn, "radio2", play("C", "Twice, recent", "2026-09-01 10:00"), play("C", "Twice, recent", "2026-09-05 10:00"))
    assert [i.artist for i in mq.build_queue(conn)] == ["C", "B", "A"]


def test_states_follow_what_spotify_has_said(conn):
    for name in ("new", "cand", "done", "nomatch"):
        save(conn, "radio2", play(name, name))
    db.save_match(conn, "cand", "cand", "spotify:track:1", "cand", ["cand"], source="reccobeats")
    db.save_match(conn, "done", "done", "spotify:track:2", "done", ["done"], source="spotify")
    db.save_match(conn, "nomatch", "nomatch", None)
    states = {i.artist: i.state for i in mq.build_queue(conn)}
    assert states == {"new": mq.NEW, "cand": mq.VERIFY, "done": mq.DONE, "nomatch": mq.DONE}


def test_a_reccobeats_miss_does_not_settle_a_track_for_spotify(conn):
    save(conn, "radio2", play("Obscure", "Song"))
    db.save_match(conn, "Obscure", "Song", None, service="reccobeats")
    (item,) = mq.build_queue(conn)
    assert item.state == mq.NEW


def test_one_off_older_than_90_days_expires_but_a_repeat_does_not(conn):
    fmt = "%Y-%m-%d %H:%M"
    long_ago = datetime.now() - timedelta(days=120)
    save(conn, "radio2", play("Once", "Song one", long_ago.strftime(fmt)))
    save(conn, "radio2", play("Twice", "Song two", long_ago.strftime(fmt)),
         play("Twice", "Song two", (long_ago + timedelta(days=1)).strftime(fmt)))
    save(conn, "radio2", play("Recent", "Song three", datetime.now().strftime(fmt)))
    expired = {i.artist: i.expired(datetime.now()) for i in mq.build_queue(conn)}
    assert expired == {"Once": True, "Twice": False, "Recent": False}


def test_station_filter_limits_the_offer_but_not_the_popularity(conn):
    save(conn, "radio2", play("Queen", "Radio Ga Ga", "2026-09-01 10:00"))
    save(conn, "npo3fm", play("Queen", "Radio Ga Ga", "2026-09-02 10:00"), play("Other", "Track", "2026-09-02 11:00"))
    items = {i.artist: i for i in mq.build_queue(conn, stations=["radio2"])}
    assert set(items) == {"Queen"}
    assert items["Queen"].play_count == 2


def test_propagate_shares_a_settled_answer_with_other_spellings(conn):
    save(conn, "radio2", play("Tiësto", "The Business", "2026-09-01 10:00"), play("Tiësto", "The Business", "2026-09-02 10:00"))
    save(conn, "npo3fm", play("Tiesto", "The business", "2026-09-03 10:00"))
    db.save_match(conn, "Tiësto", "The Business", "spotify:track:7", "The Business", ["Tiësto"], source="spotify")
    assert db.get_cached_match(conn, "Tiesto", "The business") == (False, None)
    assert mq.propagate(conn, mq.build_queue(conn)) == 1
    assert db.get_cached_match(conn, "Tiesto", "The business") == (True, "spotify:track:7")


def test_propagate_keeps_a_sibling_that_is_already_settled(conn):
    save(conn, "radio2", play("A", "Song", "2026-09-01 10:00"), play("A", "Song", "2026-09-02 10:00"))
    save(conn, "npo3fm", play("A.", "Song", "2026-09-03 10:00"))
    db.save_match(conn, "A", "Song", "spotify:track:1", "Song", ["A"], source="spotify")
    db.save_match(conn, "A.", "Song", "spotify:track:2", "Song", ["A"], source="spotify")
    mq.propagate(conn, mq.build_queue(conn))
    assert db.get_cached_match(conn, "A.", "Song") == (True, "spotify:track:2")
