# 📻 RadioScrobbler

Tracks what your favorite internet radio stations are playing, and keeps
Spotify playlists automatically up to date with it — fresh music, no ads,
zero effort on your end.

## 🎯 The idea

Some radio stations play exactly your kind of music (e.g. [Pinguin
Classics](https://pinguinradio.com/playlist/pinguinclassic),
[KINK](https://kink.nl/), [BBC Radio 6 Music](https://www.bbc.co.uk/6music))
— just with ads, jingles, and no "save this for later" button.
RadioScrobbler fixes that:

1. **Scrapes** what actually got played on a station recently.
2. **Matches** those tracks to Spotify.
3. **Syncs** a Spotify playlist per station, keeping it current automatically
   — new tracks added, tracks that stopped airing dropped again.

You just listen to the playlist from now on, not the radio. 🎧

## ✨ Features

- 🔁 **Multiple stations at once** — each with its own playlist (see
  `stations.py` for the current list: Pinguin Classics, KINK Classics,
  KINK Distortion, BBC Radio 6 Music).
- 🧠 **Smart, not wasteful** — every track only ever gets looked up on
  Spotify once (local cache), and only the *diff* gets sent to Spotify
  instead of rebuilding the whole playlist every run.
- 🚫 **Remove a track from a playlist yourself?** It's never added back
  automatically (blocklist).
- ❤️ **Liked songs are sacred** — they're never auto-removed, even if the
  station stops playing them.
- 🎵 **Audio profile per track** (optional, via
  [ReccoBeats](https://reccobeats.com)) — energy, danceability, valence,
  tempo and more, free and without an API key. See `RECCOBEATS.md`.
- 🗄️ Everything lives in a plain local SQLite file — no external database
  needed to get started.

## 🚀 Quick start

```bash
git clone https://github.com/WimmoX/RadioScrobbler.git
cd RadioScrobbler
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in your Spotify app credentials (see below)
```

### Create a Spotify app

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
   and click **Create app**.
2. Set the **Redirect URI** to exactly `http://127.0.0.1:8080/callback`.
3. Copy the **Client ID** and **Client secret** into your `.env`.

### Run it

```bash
# Step 1: fetch recently played tracks (no Spotify involved, always safe)
.venv/bin/python3 scrape.py pingclass

# Step 2: sync the Spotify playlist (first run will prompt you to log in)
.venv/bin/python3 sync_playlist.py pingclass

# Optional: fetch an audio profile (energy/valence/etc.) per track
.venv/bin/python3 fetch_audio_features.py

# Occasionally, after manual changes in Spotify: reconcile the local cache
.venv/bin/python3 resync_playlist.py pingclass
```

Run `scrape.py` regularly (e.g. daily) — most stations only expose 7 days of
history, so the more often you scrape, the longer your own history grows.

## 🧩 How it fits together

| Script | Does what | Talks to Spotify? |
|---|---|---|
| `scrape.py` | Fetches recently played tracks and stores them | ❌ |
| `sync_playlist.py` | Matches tracks, computes the diff, updates the playlist | ✅ |
| `resync_playlist.py` | Reconciles the local cache with the real playlist contents | ✅ |
| `fetch_audio_features.py` | Fetches energy/valence/etc. via ReccoBeats | ❌ (ReccoBeats instead) |

Everything runs on a local SQLite database (`db.py`) that tracks: which
tracks aired when, which Spotify match belongs to them, what's (as far as we
know) already in each playlist, and a blocklist of manually removed tracks.

Want more detail? 📖
- **`PROGRESS.md`** — full architecture, status, and open items.
- **`LessonsLearned.md`** — problems we already solved (so we don't run into
  them twice).
- **`RECCOBEATS.md`** — how the audio-profile integration works.

## 🗺️ Roadmap

- [x] MVP: playlist per station, updated with recently played tracks
- [x] Tracks that stop airing get dropped again
- [x] Multiple stations
- [x] Audio profile per track (ReccoBeats)
- [ ] "Banned Tracks" playlist as a manually managed blocklist
- [ ] Separate playlists per daypart (weekday-day/evening, weekend-morning/afternoon/evening, ...)
- [ ] Docker container with a minimal UI to pick station + music service

## 🛠️ Tech

Python, [Spotipy](https://spotipy.readthedocs.io/) for the Spotify Web API,
[OnlineRadioBox](https://onlineradiobox.com) as the play-history source,
[ReccoBeats](https://reccobeats.com) for audio features, and SQLite for
local storage. No heavy dependencies, no external database required.
