"""Scrape recent plays for a station and store them in the local database.

Run this regularly (e.g. daily via cron) to build up history. Two sources:
OnlineRadioBox (only the last 7 days) and relisten.nl (years of history, plus
NPO 3FM). A station listed in stations.RELISTEN_SLUGS uses relisten by
default; --source overrides that. Plays seen by both sources are stored once
(see db.save_plays).

Examples:
    python scrape.py radio2                       # daily run, last 7 days
    python scrape.py npo3fm --days 90             # backfill three months
    python scrape.py radio2 --source onlineradiobox
"""
import argparse
import os

from dotenv import load_dotenv

import db
import relisten
from scraper import get_recent_plays
from stations import RELISTEN_SLUGS, STATIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "station", nargs="?", choices=STATIONS.keys(),
        help="Station slug (defaults to STATION_SLUG in .env). Known: " + ", ".join(STATIONS),
    )
    parser.add_argument("--source", choices=["auto", "relisten", "onlineradiobox"], default="auto",
                        help="auto (default): relisten if the station is in RELISTEN_SLUGS, else OnlineRadioBox")
    parser.add_argument("--days", type=int, default=7,
                        help="How many days back (default 7; OnlineRadioBox has at most 7)")
    args = parser.parse_args()

    load_dotenv()
    station_slug = args.station or os.environ["STATION_SLUG"]
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    source = args.source
    if source == "auto":
        source = "relisten" if station_slug in RELISTEN_SLUGS else "onlineradiobox"
    if source == "relisten" and station_slug not in RELISTEN_SLUGS:
        parser.error(f"'{station_slug}' has no relisten slug in stations.RELISTEN_SLUGS")

    conn = db.connect(db_path)
    if source == "relisten":
        plays = relisten.get_plays(RELISTEN_SLUGS[station_slug], days=args.days)
        db.save_relisten_songs(conn, plays)
    else:
        plays = get_recent_plays(station_slug, days=args.days)
    new = db.save_plays(conn, station_slug, plays)
    print(f"{station_slug} via {source}: {len(plays)} plays fetched, {new} new "
          f"({len(plays) - new} already known)")


if __name__ == "__main__":
    main()
