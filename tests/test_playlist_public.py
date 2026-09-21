import sync_playlist


class FakeSp:
    def __init__(self, created_public):
        self.created_public = created_public
        self.calls = []

    def current_user_playlists(self):
        return {"items": [], "next": None}

    def current_user_playlist_create(self, name, public):
        self.calls.append(("create", name, public))
        return {"id": "abc", "public": self.created_public}

    def playlist_change_details(self, playlist_id, public):
        self.calls.append(("change", playlist_id, public))


def test_new_playlists_are_created_public(conn):
    sp = FakeSp(created_public=True)
    assert sync_playlist.get_or_create_playlist(sp, conn, "key", "Name") == "abc"
    assert sp.calls == [("create", "Name", True)]


def test_a_playlist_that_came_back_private_is_switched_to_public(conn):
    sp = FakeSp(created_public=False)
    sync_playlist.get_or_create_playlist(sp, conn, "key", "Name")
    assert sp.calls == [("create", "Name", True), ("change", "abc", True)]
