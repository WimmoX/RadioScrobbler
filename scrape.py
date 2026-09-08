"""Scrape recent plays for a station and store them in the local database.

Run this regularly (e.g. daily via cron) to build up history beyond
OnlineRadioBox's own 7-day window.
"""
import argparse
import os

from dotenv import load_dotenv

import db
from scraper import get_recent_plays
from stations import STATIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "station", nargs="?", choices=STATIONS.keys(),
        help="Station slug (defaults to STATION_SLUG in .env). Known: " + ", ".join(STATIONS),
    )
    args = parser.parse_args()

    load_dotenv()
    station_slug = args.station or os.environ["STATION_SLUG"]
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")

    conn = db.connect(db_path)
    plays = get_recent_plays(station_slug)
    db.save_plays(conn, station_slug, plays)
    print(f"Stored {len(plays)} plays for {station_slug} (duplicates ignored)")


if __name__ == "__main__":
    main()
