"""Scrape a station's play history from relisten.nl — a second source next to
OnlineRadioBox (scraper.py). Its history goes back years (not 7 days) and each
play carries a relisten song id, which can be turned into a Spotify track id
(see spotify_id_for()).

One request per day, the whole day is on the page. Plays are returned in the
same shape as scraper.py's, with `played_at` rounded down to the minute so a
play seen by both sources gets the same primary key in `plays` (Les 2).
"""
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from scraper import Play

DAY_URL = "https://www.relisten.nl/playlists/{slug}/{day}.html"
OUT_URL = "https://www.relisten.nl/out"
HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_DELAY = 1.0  # an ad-funded site: be gentle
_SPOTIFY_TRACK = re.compile(r"open\.spotify\.com/track/(\w+)")


def parse_day(html: str, day: date) -> list[Play]:
    """Plays on `day`. The page also shows a few 'new tracks' from a sidebar,
    dated differently — those are dropped by checking the date."""
    soup = BeautifulSoup(html, "html.parser")
    plays = []
    for row in soup.select('li.media[itemprop="track"]'):
        name = row.select_one('[itemprop="name"]')
        artist = row.select_one('[itemprop="byArtist"]')
        stamp = row.select_one("small[title]")
        if not (name and artist and stamp):
            continue
        try:
            played_at = datetime.strptime(stamp["title"], "%d-%m-%Y %H:%M:%S")
        except ValueError:
            continue
        if played_at.date() != day:
            continue
        title_text, artist_text = name.get_text(strip=True), artist.get_text(strip=True)
        if not title_text or not artist_text:
            continue
        plays.append(Play(
            artist=artist_text, title=title_text,
            played_at=played_at.replace(second=0, microsecond=0),
            source_id=row.get("data-id"),
        ))
    return plays


def get_plays(slug: str, days: int = 7, until: date | None = None) -> list[Play]:
    """Plays for the `days` days ending at `until` (default today), oldest last."""
    until = until or date.today()
    plays = []
    for i in range(days):
        day = until - timedelta(days=i)
        response = requests.get(DAY_URL.format(slug=slug, day=day.strftime("%d-%m-%Y")),
                                headers=HEADERS, timeout=30)
        response.raise_for_status()
        plays.extend(parse_day(response.text, day))
        if i < days - 1:
            time.sleep(REQUEST_DELAY)
    return plays


def spotify_id_for(song_id: str) -> str | None:
    """Relisten's own Spotify link for one of its songs (a 302 to
    open.spotify.com/track/<id>), or None if it has none. Not the Spotify
    API, so no Spotify quota — but only relisten's word for it (~93% right
    in a first check), so treat it as an unverified candidate."""
    response = requests.get(OUT_URL, params={"songID": song_id, "option": "spotify"},
                            headers=HEADERS, allow_redirects=False, timeout=20)
    match = _SPOTIFY_TRACK.search(response.headers.get("location", ""))
    return match.group(1) if match else None
