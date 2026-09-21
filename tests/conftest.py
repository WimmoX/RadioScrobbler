import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
from scraper import Play  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    """A fresh SQLite database in a temp dir — tests never touch data/ or the network."""
    connection = db.connect(str(tmp_path / "test.db"))
    yield connection
    connection.close()


def play(artist, title, when="2026-09-01 12:00"):
    return Play(artist=artist, title=title, played_at=datetime.strptime(when, "%Y-%m-%d %H:%M"))
