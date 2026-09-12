"""Find a station on OnlineRadioBox and add it to stations.py.

Usage:
    python add_station.py "<search term>" "<Spotify playlist name>"
    python add_station.py "<search term>" "<Spotify playlist name>" --pick 0
"""
import argparse
import re
import sys

import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://onlineradiobox.com/search"
HEADERS = {"User-Agent": "Mozilla/5.0"}
STATIONS_FILE = "stations.py"
_HREF_PATTERN = re.compile(r"^/([a-z]{2})/([a-zA-Z0-9_-]+)/$")


def search_stations(query: str) -> list[tuple[str, str]]:
    """Return [(station_id, display_name), ...]. station_id is a bare slug
    (nl locale) or 'locale/slug' for other countries. Note: OnlineRadioBox's
    search results page also surfaces unrelated "other stations" links, so
    this list isn't guaranteed to all be relevant — eyeball it."""
    resp = requests.get(SEARCH_URL, params={"q": query}, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    results = []
    seen = set()
    for a in soup.find_all("a", href=True):
        match = _HREF_PATTERN.match(a["href"])
        if not match:
            continue
        locale, slug = match.groups()
        station_id = slug if locale == "nl" else f"{locale}/{slug}"
        if station_id in seen:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        seen.add(station_id)
        results.append((station_id, name))
    return results


def add_to_config(station_id: str, playlist_name: str) -> None:
    with open(STATIONS_FILE) as f:
        content = f.read()

    if f'"{station_id}"' in content:
        print(f"'{station_id}' staat al in {STATIONS_FILE}, niks gewijzigd.")
        return

    insert_at = content.rstrip().rindex("}")
    new_line = f'    "{station_id}": "{playlist_name}",\n'
    content = content[:insert_at] + new_line + content[insert_at:]

    with open(STATIONS_FILE, "w") as f:
        f.write(content)
    print(f"'{station_id}' toegevoegd aan {STATIONS_FILE} als '{playlist_name}'.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Zoekterm voor OnlineRadioBox, bv. 'kink' of 'bbc radio 6'")
    parser.add_argument("playlist_name", help="Naam voor de bijbehorende Spotify-playlist")
    parser.add_argument("--pick", type=int, metavar="N",
                         help="Kies kandidaat #N direct (0-based), sla de vraag over")
    args = parser.parse_args()

    candidates = search_stations(args.query)
    if not candidates:
        print(f"Geen zenders gevonden voor '{args.query}'.")
        sys.exit(1)

    if args.pick is not None:
        station_id, name = candidates[args.pick]
    else:
        print("Gevonden zenders (dit kan ook irrelevante suggesties bevatten, kijk goed):")
        for i, (sid, name) in enumerate(candidates[:15]):
            print(f"  [{i}] {name}  ({sid})")
        choice = input("Welk nummer wil je toevoegen? ")
        station_id, name = candidates[int(choice)]

    add_to_config(station_id, args.playlist_name)


if __name__ == "__main__":
    main()
