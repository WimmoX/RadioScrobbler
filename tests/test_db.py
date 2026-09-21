import db
from conftest import play


def test_connect_twice_in_a_row_is_fine(tmp_path):
    path = str(tmp_path / "t.db")
    db.connect(path).close()
    db.connect(path).close()


def test_same_play_from_two_sources_is_stored_once(conn):
    a = [play("Coolio Ft. L.V.", "Gangsta's Paradise (Album Version)", "2026-09-01 10:00")]
    b = [play("Coolio", "Gangsta's Paradise", "2026-09-01 10:02")]
    assert db.save_plays(conn, "radio2", a) == 1
    assert db.save_plays(conn, "radio2", b) == 0
    assert conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0] == 1


def test_the_same_title_far_apart_is_a_new_play(conn):
    db.save_plays(conn, "radio2", [play("A", "Song", "2026-09-01 10:00")])
    assert db.save_plays(conn, "radio2", [play("A", "Song", "2026-09-01 14:00")]) == 1


def test_copy_match_gives_the_same_answer_to_another_spelling(conn):
    db.save_match(conn, "A", "Song", "spotify:track:1", "Song", ["A"], source="spotify")
    db.copy_match(conn, "A", "Song", [("A.", "Song")])
    assert db.get_cached_match(conn, "A.", "Song") == (True, "spotify:track:1")
