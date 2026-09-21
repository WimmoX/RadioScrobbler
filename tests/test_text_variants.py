import matching
import reccobeats
import sync_playlist


def labels(artist, title):
    return [label for _, _, label in matching.text_variants(artist, title)]


def test_apostrophes_broken_into_spaces_are_repaired():
    assert matching.fix_contractions("They re On To Me") == "They're On To Me"
    assert matching.fix_contractions("k s Choise") == "k's Choise"
    assert matching.fix_contractions("Don t Stop") == "Don't Stop"
    assert matching.fix_contractions("Take That") == "Take That"          # not a contraction


def test_version_tags_are_dropped_but_a_real_title_after_a_hyphen_is_kept():
    assert matching.clean_title("In The Air Tonight (albumversie)") == "In The Air Tonight"
    assert matching.clean_title("Bohemian Rhapsody - Remastered 2011") == "Bohemian Rhapsody"
    assert matching.clean_title("Fi - Living For The Weekend") == "Fi - Living For The Weekend"
    assert matching.clean_title("(Untitled)") == "(Untitled)"               # never empty


def test_all_readings_of_a_pair():
    assert labels("War Of The Worlds", "My Vitriol") == ["as-is", "swapped"]
    assert labels("Ari Hest", "They re On To Me") == ["as-is", "cleaned", "swapped"]
    assert labels("Hard", "Fi - Living For The Weekend") == ["as-is", "resplit", "swapped"]
    variants = {label: (a, t) for a, t, label in matching.text_variants("Hard", "Fi - Living For The Weekend")}
    assert variants["resplit"] == ("Hard-Fi", "Living For The Weekend")


def item(artist, title):
    return {"name": title, "artists": [{"name": artist}], "uri": "spotify:track:x"}


def test_a_swapped_pair_is_found_in_the_same_results():
    results = [item("My Vitriol", "War of the Worlds")]
    assert matching.best_candidate(results, "War Of The Worlds", "My Vitriol") is None
    best, label = matching.best_candidate_variants(results, "War Of The Worlds", "My Vitriol")
    assert best and label == "swapped"


def test_the_text_as_scraped_still_wins_when_it_matches():
    best, label = matching.best_candidate_variants([item("Queen", "Radio Ga Ga")], "Queen", "Radio Ga Ga")
    assert label == "as-is"


def test_reccobeats_tries_the_next_reading_only_after_a_miss(monkeypatch):
    queries = []

    def fake_search(query, artist, title):
        queries.append(query)
        if query == "War Of The Worlds":              # the real title: only the swapped reading searches for it
            return {"uri": "spotify:track:1", "name": "War of the Worlds", "artists": [{"name": "My Vitriol"}],
                    "reccobeats_id": "r", "isrc": None}
        return None

    monkeypatch.setattr(reccobeats, "_search", fake_search)
    match = reccobeats.search_track("War Of The Worlds", "My Vitriol")
    assert match["variant"] == "swapped"
    assert queries == ["My Vitriol", "War Of The Worlds"]     # as-is first, then swapped


def test_reccobeats_does_not_repeat_a_query_or_search_titles_shorter_than_3(monkeypatch):
    queries = []
    monkeypatch.setattr(reccobeats, "_search", lambda q, a, t: queries.append(q))
    assert reccobeats.search_track("Doe Maar", "Pa") is None
    assert queries == ["Doe Maar"]                            # "Pa" (2 chars) is skipped; the swapped reading is searched


def test_spotify_plain_query_is_only_used_when_the_structured_one_finds_nothing():
    class Budget:
        def check(self): pass
        def record_call(self): pass

    calls = []

    class Sp:
        def search(self, q, type, limit):
            calls.append(q)
            return {"tracks": {"items": [] if q.startswith("artist:") else [item("My Vitriol", "War of the Worlds")]}}

    found = sync_playlist.find_track_match(Sp(), "War Of The Worlds", "My Vitriol", Budget())
    assert found and len(calls) == 2

    calls.clear()
    class SpDirect(Sp):
        def search(self, q, type, limit):
            calls.append(q)
            return {"tracks": {"items": [item("Queen", "Radio Ga Ga")]}}
    assert sync_playlist.find_track_match(SpDirect(), "Queen", "Radio Ga Ga", Budget()) and len(calls) == 1
