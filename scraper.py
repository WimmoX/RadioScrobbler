"""Scrape recently played tracks for a radio station from OnlineRadioBox."""
from dataclasses import dataclass
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://onlineradiobox.com/{locale}/{slug}/playlist/{day}"
DEFAULT_LOCALE = "nl"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
}
DAYS_AVAILABLE = 7
# Station jingles/idents are logged as tracks with an artist name that starts
# with the station name and no real Spotify-matchable title. This is
# per-station (not global) so a prefix for one station can't accidentally
# drop a real artist on another (e.g. "kink" would wrongly match "The Kinks").
JINGLE_PREFIXES = {
    "pingclass": ("pinguin",),
}


@dataclass
class Play:
    artist: str
    title: str
    played_at: datetime


def _split_station(station: str) -> tuple[str, str]:
    """A station is either a bare slug (default locale) or 'locale/slug'."""
    if "/" in station:
        locale, slug = station.split("/", 1)
        return locale, slug
    return DEFAULT_LOCALE, station


def _parse_day(station: str, day: int) -> list[Play]:
    locale, slug = _split_station(station)
    url = BASE_URL.format(locale=locale, slug=slug, day=day)
    response = requests.get(url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    jingle_prefixes = JINGLE_PREFIXES.get(station, ())
    date = (datetime.now() - timedelta(days=day)).date()

    plays = []
    for row in soup.select("table.tablelist-schedule tr"):
        time_cell = row.select_one(".tablelist-schedule__time")
        track_cell = row.select_one(".track_history_item")
        if not time_cell or not track_cell:
            continue

        text = track_cell.get_text(strip=True)
        if " - " not in text:
            continue

        artist, title = text.split(" - ", 1)
        artist, title = artist.strip(), title.strip()
        if not artist or not title:
            continue
        if artist.lower().startswith(jingle_prefixes):
            continue

        time_text = time_cell.get_text(strip=True)
        if time_text == "Live":
            played_at = datetime.now().replace(second=0, microsecond=0)
        else:
            hour, minute = map(int, time_text.split(":"))
            played_at = datetime.combine(date, datetime.min.time()).replace(hour=hour, minute=minute)

        plays.append(Play(artist=artist, title=title, played_at=played_at))

    return plays


def get_recent_plays(station: str, days: int = DAYS_AVAILABLE) -> list[Play]:
    """Return plays for the last `days` days (max 7, OnlineRadioBox's own limit).

    `station` is either a bare OnlineRadioBox slug (assumes the 'nl' locale)
    or 'locale/slug' for stations listed under another country.
    """
    days = min(days, DAYS_AVAILABLE)
    all_plays = []
    for day in range(days):
        all_plays.extend(_parse_day(station, day))
    return all_plays
