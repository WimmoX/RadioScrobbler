"""Fetch ReccoBeats audio features (energy, valence, danceability, tempo, ...)
for tracks that are already matched to a Spotify URI, and cache them locally.

No API key needed (see RECCOBEATS.md). Reads straight from spotify_matches,
so this can run independently of sync_playlist.py as soon as that has
matched at least some tracks.
"""
import os

import requests
from dotenv import load_dotenv

import db
from retry import RateLimited, call_with_retry

API_URL = "https://api.reccobeats.com/v1/audio-features"
BATCH_SIZE = 40  # no documented hard limit; kept conservative
REQUEST_DELAY = 0.2


def _get_with_retry(url: str, params: dict) -> requests.Response:
    def attempt():
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 429:
            raise RateLimited(int(response.headers.get("Retry-After", 1)))
        response.raise_for_status()
        return response
    return call_with_retry(attempt, delay=REQUEST_DELAY)


def fetch_batch(track_ids: list[str]) -> dict[str, dict]:
    """Return {track_id: audio_features_item} for whichever ids ReccoBeats recognised."""
    response = _get_with_retry(API_URL, params={"ids": ",".join(track_ids)})
    by_track_id = {}
    for item in response.json().get("content", []):
        track_id = item["href"].rstrip("/").rsplit("/", 1)[-1]
        by_track_id[track_id] = item
    return by_track_id


def main():
    load_dotenv()
    db_path = os.environ.get("DB_PATH", "data/radioscrobbler.db")
    conn = db.connect(db_path)

    uris = db.get_uris_missing_audio_features(conn)
    print(f"{len(uris)} tracks need audio features")

    done = 0
    not_found = 0
    failed_batches = 0
    for i in range(0, len(uris), BATCH_SIZE):
        batch = uris[i:i + BATCH_SIZE]
        track_ids = [uri.split(":")[-1] for uri in batch]

        try:
            results = fetch_batch(track_ids)
        except requests.HTTPError as e:
            # Leave this batch's URIs unsaved so they're picked up again next run.
            print(f"  batch failed ({e}), skipping {len(batch)} tracks for now")
            failed_batches += 1
            continue

        for uri, track_id in zip(batch, track_ids):
            features = results.get(track_id)
            if features is None:
                not_found += 1
            db.save_audio_features(conn, uri, features)

        done += len(batch)
        print(f"  ...{done}/{len(uris)}")

    print(f"Done. {done - not_found} tracks enriched, {not_found} had no ReccoBeats match"
          + (f", {failed_batches} batch(es) failed and will retry next run" if failed_batches else "") + ".")


if __name__ == "__main__":
    main()
