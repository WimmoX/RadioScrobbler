=====================================================
REVISION NOTES (2026-09-21) — in-place refinement
=====================================================

This list was checked against the code and the database as of 2026-09-21
(15,897 unique tracks, ~94,000 plays, 12,529 tracks with a Spotify id of which
~1,350 confirmed by Spotify and ~11,100 still candidates, ~1,900 never
attempted, Spotify Search budget ~460 calls/day). Nothing has been put on the
GitHub board yet; review first, then create the tickets.

Decisions taken:
- **Popularity = number of times a track has been played on the radio.** Every
  play adds 1. Popular tracks float up, and this same number drives the
  priority when matching against ReccoBeats and Spotify. A track that is
  played only once and never gets its turn simply drops off the radar without
  costing resources. (Spotify's own `popularity` field no longer exists in the
  API responses; ReccoBeats' `popularity` is not used for this.)
- **SQLite stays** as long as nothing forces a change.
- **Budget split stays a fixed 80/20** (80% new unresolved, 20% verification).
  Making it configurable is a later step.
- **UI technology is not chosen yet** and needs its own card (RS-UI-00).

Status markers used below: NEW (added in this revision), ADJUSTED (premise or
acceptance criteria changed), PARTLY DONE (some of it exists already),
MERGED (covered elsewhere), OK (unchanged).
Cross-references: GitHub #1 (other sources), #4 (matching algorithm),
#5 (blocked-tracks playlist).

=====================================================
DEEL 0 — FOUNDATIONS (prerequisites found during review)
=====================================================

### RS-DEV-01 — Set up automated tests  [PARTLY DONE — 37 tests in `tests/` as of 2026-09-24]
**Description:** (At the time of the revision) the repository had no tests (no test folder, no pytest in
requirements.txt), yet several items below require tests. Several past bugs
(see LessonsLearned.md, Les 8, 10, 13, 14) are exactly the kind a small test
suite would have caught.

**Acceptance criteria:**
- pytest is a documented dev dependency; one command runs the whole suite.
- Tests run on a temporary SQLite database and never touch the network or the
  real database.
- First tests cover: `db.save_plays` de-duplication (including cross-source
  cases: same track 1–3 minutes apart, accents, `&` vs `,`), the schema
  migration (connect twice in a row), `quota.SearchBudget` (limit only rises
  when the limit is actually tested; a block with zero logged calls does not
  set the limit to 0), `matching.best_candidate` (e.g. "Echo & The
  Bunnymen"), and `resolve.Resolver` cache semantics with fake sources (a
  "no match" is only cached by the source that tried).

### RS-OPS-05 — SQLite WAL mode and busy timeout  [NEW]
**Description:** The database currently runs in the default journal mode
(`delete`) with SQLite's default 5-second lock wait. As soon as a UI, a
scheduler and a script use the same file at once, "database is locked" errors
appear.

**Acceptance criteria:**
- WAL journal mode and a longer busy timeout are set when a connection is
  opened.
- Verified with two concurrent processes (e.g. a scrape and a match run)
  without lock errors.
- Documented: the database file must live on a local disk (see RS-OPS-02).

### RS-OPS-06 — Turn the scripts into callable tasks with progress reporting  [NEW]
**Description:** scrape, match_relisten, match_tracks,
match_reccobeats_backlog, sync/build_playlist and fetch_audio_features are
command-line scripts. A UI or scheduler needs to start them as functions and
get progress and results back.

**Acceptance criteria:**
- Each exposes a function that takes a connection and options, reports
  progress through a callback (processed/total, per-source counts), and
  returns a result summary (counts, outcome).
- The command line stays a thin wrapper and prints the same progress lines as
  today (every 100–200 tracks).
- No change in behaviour.

### RS-DATA-01 — Move station configuration from stations.py into the database  [NEW]
**Description:** Stations live in `stations.py` (plus `RELISTEN_SLUGS`), and
add_station.py / remove_station.py edit that file with regular expressions. A UI
cannot edit that safely, station tiers (RS-OPS-04) and multiple sources per
station (GitHub #1) need a real home.

**Acceptance criteria:**
- A `stations` table: id, display name, enabled flag, tier, playlist name.
- Per-source configuration per station (source type such as OnlineRadioBox or
  Relisten, plus that source's slug); a station can have more than one source.
- The current 7 stations, including their Relisten slugs, are migrated.
- Scripts read stations from the database; add_station.py and
  remove_station.py work on the database.
- Removing a station keeps the current behaviour of remove_station.py
  (history and exclusive playlist tracks) and asks for confirmation.

### RS-DATA-02 — Record capture time and source per play  [NEW]
**Description:** `plays` only has `played_at`. The play-history view (RS-UI-04)
needs a capture time, and with two sources it helps to know which one
delivered a play.

**Acceptance criteria:**
- `plays.captured_at` and `plays.source` are filled by `db.save_plays`. Because
  the existing row wins on a near-duplicate, `source` is the source that saw
  the play first.
- Existing rows get NULL (unknown) — this cannot be reconstructed, and the UI
  shows "unknown" for them.

=====================================================
DEEL 1 — MVP UI BACKLOG
=====================================================

## Epic: Foundation for the UI

### RS-UI-00 — Choose the UI/backend technology (spike)  [NEW]
**Description:** No technology has been chosen for the UI and nobody involved
has front-end experience, so this needs its own card before any UI item
starts. The code is largely AI-generated and must stay readable and
maintainable for someone who is not a front-end developer.

**Acceptance criteria:**
- A shortlist of 2–3 options, with pros and cons in plain language.
- Criteria include: runs in Docker next to SQLite, single user without login,
  mostly Python, can start background tasks and show their progress, little
  maintenance, and how much front-end code it needs.
- A small proof of concept that shows the play-history table from the real
  database (the shape of RS-UI-04).
- The decision is written down (PROGRESS.md) including how scheduled tasks
  will be run (needed for RS-UI-09).

## Epic: Station Registry

### RS-UI-01 — Build radio station list  [ADJUSTED]
**Description:** Create a UI page listing every indexed radio station.
**Depends on:** RS-UI-00, RS-DATA-01.

**Acceptance criteria:**
- Shows station name, its source(s) (type and slug), enabled status, and most
  recent successful scrape.
- Default sort is by station name.
- Empty state explains that no stations have been added yet.

### RS-UI-02 — Add and edit a radio station  [ADJUSTED]
**Description:** Provide a form for maintaining station configuration.
**Depends on:** RS-DATA-01.

**Acceptance criteria:**
- User can add, edit, enable/disable, and remove a station.
- Form supports station name, one or more sources (type and slug), tier, and
  scrape configuration.
- Invalid or incomplete configuration is shown clearly before saving.
- Removing a station says what will be deleted (its play history and playlist
  tracks that came only from it) and requires confirmation; disabling is the
  non-destructive alternative.

### RS-UI-03 — Show station health summary  [ADJUSTED]
**Description:** Add basic operational status to each station.
**Depends on:** RS-OPS-03.

**Acceptance criteria:**
- Shows last scrape time, last result, latest track captured, and most recent error if applicable.
- Status is visually distinguishable: healthy, warning, failed, disabled.
- The thresholds are defined: for example, a station that only has OnlineRadioBox
  (7 days of history) and has not been scraped for more than 5 days is a warning,
  because data would start to be lost.

## Epic: Play History

### RS-UI-04 — Build station play-history view  [ADJUSTED]
**Description:** Display captured radio plays in a table, similar in spirit to an online-radio playlist history.
**Depends on:** RS-UI-00, RS-DATA-02.

**Acceptance criteria:**
- Shows station, artist, track title, played-at date/time, and capture time
  (unknown for plays captured before RS-DATA-02).
- Default order is most recently aired first.
- Clearly indicates how current the displayed data is.

### RS-UI-05 — Add play-history filters and sorting  [ADJUSTED]
**Description:** Make the history usable once the database grows.

**Acceptance criteria:**
- Filter by station and date/time range.
- Filter by Spotify match state (the states defined in RS-UI-06).
- Sort by played time, capture time, artist, title, and popularity (= play count).
- Filters and sorting work together.

### RS-UI-06 — Display Spotify verification state  [ADJUSTED]
**Description:** Surface whether a captured track has been successfully matched to Spotify.

**Acceptance criteria:**
- The states are the ones that exist in the data: **verified** (Spotify
  itself confirmed it), **candidate** (an id from ReccoBeats or Relisten, not
  yet confirmed by Spotify; the source is shown), **no match on Spotify**
  (Spotify searched and found nothing), and **not attempted yet** (includes
  tracks that only ReccoBeats or Relisten could not find). There is no
  per-track "failed" state; failed runs belong in the task log.
- Verified matches show a green check mark; the other states are visually distinct.
- Hover/tap detail explains the current matching state.
- Verified entries can expose the Spotify track ID/link when available.

### RS-UI-07 — Show track metadata and popularity  [ADJUSTED]
**Description:** Expose collected metadata in the history/detail view.
**Depends on:** RS-UI-15.

**Acceptance criteria:**
- Displays popularity = the persistent play count (see RS-MATCH-01).
- Displays available metadata: track length/duration, energy, tempo, valence, danceability.
- Missing metadata is handled gracefully rather than looking like an error.
- Audio features are backfilled for existing tracks first: today only 325 of
  12,529 tracks have them (`fetch_audio_features.py`, ReccoBeats, no Spotify
  budget), so without this most rows would show nothing.

### RS-UI-15 — Add track length to the data model  [ADJUSTED]
**Description:** Track duration is not in the current schema and is needed for the history/detail view and future playlist logic.

**Acceptance criteria:**
- Duration is stored per track (`tracks.duration_ms`). Both Spotify
  (`duration_ms`) and ReccoBeats (`durationMs`) return it in their search
  results, so new matches get it at no extra cost.
- Existing tracks are backfilled through ReccoBeats' single-track lookup, which
  uses no Spotify budget (about 1 second per track, so hours for ~12,500 tracks;
  run in the background, most played first).
- Duration is visible in the history/detail view.

### RS-UI-16 — Move daypart logic from the database to the application layer  [ADJUSTED]
**Description:** daynr/hr/daypart are currently hardcoded generated columns on `plays`. Daypart boundaries must be user-configurable (e.g. "Monday morning" = 06:00–12:00 for one user, 08:00–12:00 for another).

**Acceptance criteria:**
- The database stores the raw timestamp; daypart is derived at query/application level.
- Daypart windows are configurable per weekday via configuration/UI.
- build_playlist.py and the UI use the same shared daypart definition
  (build_playlist.py currently takes numbers: `--daynr 5 --daypart 3`; it moves
  to named dayparts).
- Changing a daypart window requires no database migration or backfill.
- Whether the generated columns can be dropped from the existing table is
  checked (not verified yet).

## Epic: Task Scheduling and Runtime Status

### RS-UI-08 — List background tasks  [ADJUSTED]
**Description:** Create a task overview for scraping, matching, and refresh jobs.
**Depends on:** RS-OPS-03, RS-OPS-06.

**Acceptance criteria:**
- Shows task name, type, target station where applicable, schedule, enabled state, and last run result.
- Shows whether a task is queued, running, completed, failed, or locked.

### RS-UI-09 — Configure task schedules  [ADJUSTED]
**Description:** Allow scheduling without editing cron/config files manually.
**Depends on:** RS-UI-00 (how scheduled tasks are run), RS-OPS-06.

**Acceptance criteria:**
- User can enable/disable a task.
- User can configure its interval or cron-style schedule.
- UI validates schedules before saving.
- Next planned run is visible.

### RS-UI-10 — Show live task progress and duration  [ADJUSTED]
**Description:** Make it obvious that jobs are active and how they are progressing.
**Depends on:** RS-OPS-06.

**Acceptance criteria:**
- Running task shows start time and a live elapsed timer that counts up.
- Shows processed/total counts when the task can report them (e.g. "track 412 of 1,200").
- Progress bar is shown when a meaningful total exists.
- Do not show a fictional ETA; task lengths are highly variable (ReccoBeats ~1.6–2.2s per lookup).

### RS-UI-11 — Prevent overlapping task runs  [ADJUSTED]
**Description:** Implement locking so the same work is not accidentally run twice.
**Depends on:** RS-OPS-05.

**Acceptance criteria:**
- A task run obtains a lock before work begins. There is no locking today.
- Duplicate or conflicting runs are blocked or queued with a clear status.
- Two runs that use the Spotify Search budget cannot run at the same time
  (otherwise the shared budget gets spent twice).
- Stale locks can be detected and safely cleared.
- Lock events are written to the activity log.

### RS-UI-17 — Manual "collect data / reindex" trigger  [OK]
**Description:** Allow firing a scrape or match run on demand from the UI, next to the scheduled runs.
**Depends on:** RS-UI-11, RS-OPS-06.

**Acceptance criteria:**
- A button per station (and globally) starts the relevant task.
- The triggered run appears in the task list with live progress.
- The manual trigger respects the same locking as scheduled runs.

## Epic: Activity and Error Logging

### RS-UI-12 — Create structured application activity log  [ADJUSTED]
**Description:** Record system activity as a general operational log, not only errors.
**Depends on:** RS-OPS-03.

**Acceptance criteria:**
- Records task start, completion, failure, lock events, records scraped, and tracks matched.
- Each entry includes timestamp, level, source/task, and human-readable message.
- Log is persisted in the database.
- Log volume is bounded: per-track lines are not stored in the database
  (a single run handles thousands of tracks), only per-step summaries and
  problems; entries older than a defined retention period are removed.

### RS-UI-13 — Build log viewer  [OK]
**Description:** Add an interface for checking whether the system is doing its job.

**Acceptance criteria:**
- Displays newest log entries first.
- Supports filters for time range, task/station, and level (info, warning, error).
- Error entries show useful detail without exposing secrets.
- User can inspect an individual log entry for context.

### RS-UI-14 — Add dashboard heartbeat indicators  [OK]
**Description:** Give the home screen a fast answer to: "is it working?"

**Acceptance criteria:**
- Shows last successful scrape, last successful Spotify match, currently running tasks, and recent errors.
- Uses clear status indicators and links into the relevant station, task, or log detail.

## Suggested MVP delivery order (proposal, adjusted)
1. Foundations: RS-DEV-01, RS-OPS-05, RS-DATA-02, RS-OPS-06 (see Deel 0).
2. Matching value first (Deel 2): RS-MATCH-01, RS-MATCH-02, RS-UI-15 (they share
   the same ReccoBeats lookups and can be backfilled in one pass).
3. RS-OPS-03 (runs table), RS-OPS-04 (orchestrator).
4. RS-UI-00 (technology choice) and RS-DATA-01 (stations in the database).
5. RS-UI-01, RS-UI-02 — Station registry.
6. RS-UI-04, RS-UI-05, RS-UI-06, RS-UI-07 — Play-history table, filters, match state, metadata.
7. RS-UI-08, RS-UI-10, RS-UI-11, RS-UI-17 — Task status, progress, locking, manual trigger.
8. RS-UI-12, RS-UI-13 — Activity/error log.
9. RS-UI-03, RS-UI-09, RS-UI-14, RS-UI-16 — Health, schedule editing, dashboard heartbeat, configurable dayparts.

Later / post-MVP: Spotify OAuth connection flow in the UI, notification/alert rules, deeper task retry controls, log exports, multi-user permissions (Gen 2).

=====================================================
DEEL 2 — MATCHING, BUDGET & PLAYLISTS
=====================================================

### RS-MATCH-01 — Implement priority-based matching queue  [ADJUSTED — first version built 2026-09-21, see below]
**Description:** With ~15,900 unique tracks, processing unresolved matches in arbitrary order spends scarce Spotify Search capacity on random one-off plays. Match tracks with repeated airplay first. Popularity is the number of times a track has been played (+1 per play), it never resets, and it decides the order for both ReccoBeats and Spotify matching.

**Current state (2026-09-21):** `match_tracks.py` already orders by play count over all history, but sync_playlist.py and the ReccoBeats backlog script do not, nothing is persisted, and the Spotify budget in `quota.py` is one single pool. Roughly 11,100 tracks have a candidate id waiting for Spotify verification versus ~1,900 never attempted.

**Built (2026-09-21):** `matching_queue.py` (popularity by counting `plays`, normalised
key, priority = popularity then recency, 90-day one-off filter, `--explain` in
match_tracks.py), used by match_tracks.py, sync_playlist.py, match_relisten.py and
match_reccobeats_backlog.py; 80/20 as two steps with `bucket` accounting in
quota.py (unused budget flows on); 19 tests in `tests/`. Not done: showing the
reason in a UI, and ReccoBeats-only stations of the queue in scheduled jobs (RS-OPS-04).

**Requirements:**
- Popularity = number of plays of the track. The count survives beyond the 90-day window.
  Implementation note: `plays` is never pruned and already de-duplicates on
  insert, so a query or view over `plays` (grouped on a normalised
  artist/title key) gives exactly this number, with nothing to keep in sync and
  no risk of double counting. Only store a separate counter if `plays` is ever pruned.
- The key is a normalised artist/title, so the same track spelled differently
  by two sources (accents, `&` vs `,`, "Ft.") is one queue entry and is not
  matched — and paid for — twice.
- Priority = popularity first, recency (last played) as tie-breaker. Keep the
  rule transparent, no opaque score. Station weighting ("tastemaker stations
  count double") can wait until there is evidence it helps.
- One shared ordering used by match_tracks.py, playlist sync, scheduled jobs and the
  ReccoBeats matching — no per-command ranking logic.
- Spotify Search budget (a fixed 650 calls per 24h since 2026-09-24, see
  LessonsLearned Les 17): fixed 80% for tracks with no id at all, 20% for verifying
  existing candidates (ReccoBeats or Relisten). Each bucket is ordered by
  priority internally. Suggested addition: budget a bucket does not use flows to
  the other one, otherwise the 80% sits idle once the few unresolved tracks are done.
- A track played only once whose 90 days have passed without another play is no
  longer queued for Spotify matching. This is a filter on the queue, not a
  deletion; the plays history stays. It drops off the radar without costing resources.
- ReccoBeats and Relisten matching does not use the Spotify budget and can go
  on independently, in the same priority order.

**Acceptance criteria:**
- A repeated track is selected before a comparable one-off track.
- Deduplicated scrape runs do not inflate the popularity.
- Two spellings of the same track share one queue entry.
- A one-off with no repeat within 90 days no longer consumes Spotify Search calls.
- Logs and the UI can show why an item was selected: play count, last played, and priority.
- Tests (RS-DEV-01) cover popularity counting, scrape de-duplication, ordering, the 90-day filter, and separation of the matching/verification budget buckets.

### RS-MATCH-02 — Backfill ISRC from existing data  [ADJUSTED]
**Description:** `tracks.isrc` exists and is filled for 3,685 of 12,529 tracks (29%), all of them from ReccoBeats. The ISRC is the key that later links the same recording across services (GitHub #2 follow-up). Spotify's track object carries `external_ids.isrc` (checked 2026-09-21), and so do ReccoBeats' search and track responses. Relisten candidates have none.

**Acceptance criteria:**
- The ISRC is stored whenever it is present in a Spotify Search/track response or a ReccoBeats hit — no extra API calls for new matches.
- Existing tracks without an ISRC are backfilled through ReccoBeats' single-track lookup (no Spotify budget), most played first, in the same pass as the duration backfill (RS-UI-15).
- Coverage is reportable: how many tracks have an ISRC.

### RS-MATCH-03 — Store match confidence and build a review list  [ADJUSTED]
**Description:** Match quality is currently unmeasurable; bad matches (e.g. Royal Blood) are only found by listening.

**Acceptance criteria:**
- The score from best_candidate() is stored alongside the match.
- Candidates from Relisten have no such score; define one for them too (for example by comparing Relisten's artist/title with the metadata of the id it points to) so the review list can rank them.
- A view/report lists the lowest-scoring matches for manual review.
- A reviewed match can be confirmed or rejected, and a rejection is remembered per (artist, title, track) so it does not get re-matched to the same wrong track.

### RS-MATCH-04 — Investigate low match rate for Zeilsteen  [MERGED]
**Status:** The investigation is done and written up in GitHub issue #4 ("matching
algoritme verbeteren"): 843 misses, samples of 40 and 60 reviewed, four causes
(generic titles beyond ReccoBeats' 200-result search, artist and title swapped in
the source, mangled punctuation, junk/obscure repertoire). What remains is to write
the finding in LessonsLearned.md (done: Les 15, together with the first fixes: swapped/cleaned/re-joined readings, +11.5% on real misses); the remaining fixes are tracked in #4. No separate ticket.

### RS-OPS-03 — Add a runs table with metrics  [OK]
**Description:** There is no persistent record of how long runs take or how well they perform, which makes "is it getting better?" a matter of feel.

**Acceptance criteria:**
- Each run records: task, start, end, duration, lookups attempted, Spotify vs ReccoBeats (and Relisten) split, hits, misses, and outcome.
- Metrics are queryable and feed the UI task list and heartbeat.

### RS-OPS-04 — Build run.py orchestrator  [ADJUSTED]
**Description:** A single daily entry point that runs the daily cycle in order. Playlists are not part of the default cycle: matching and scraping must not create or fill playlists by themselves.
**Depends on:** RS-OPS-06, RS-DATA-01 (tiers).

**Acceptance criteria:**
- One command runs scrape → Relisten ids (`match_relisten.py`) → matching (Spotify budget per RS-MATCH-01, then ReccoBeats) for all enabled stations.
- Playlist building only runs for playlists that have a definition (RS-PLAY-01).
- The Spotify budget is already one shared pot (it lives in the database and all
  scripts use it); the split between steps follows RS-MATCH-01.
- Stations can be tiered: "daily drivers" get aggressive updates, the rest run on a lazy schedule.
- OnlineRadioBox-only stations are scraped often enough to lose no data (its window
  is 7 days; KINK Classics lost 9–14 September to this), or a warning is raised.
- Failure of one step does not silently skip the rest; results are logged per step.

### RS-SPOT-01 — Read Spotify secrets clearly from environment variables  [PARTLY DONE]
**Description:** The credentials are already read from environment variables
(`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI`; `.env` is only loaded into the environment and is not in the repository or its history). What remains is small.

**Acceptance criteria:**
- A missing variable produces a clear startup message naming the variable, not a bare `KeyError` mid-run.
- The location of the Spotify token cache (`.cache`, written in the working directory by default) is configurable, so it can live on a mounted volume.
- No credentials in the source tree or the image (already true; keep it true).

### RS-PLAY-01 — Playlist definitions  [NEW]
**Description:** Playlists should be designed by the user afterwards, not created as a side effect of matching. Today build_playlist.py supports day/daypart windows and `--exclude-station` only, and sync_playlist.py builds a whole playlist per station. Wanted: definitions such as "Zeilsteen top 100" or "SLAM! Non Stop – last 30 days", built or refreshed on demand.
**Related:** GitHub #5 (tracks that must never be played go to a "RS - Blocked" playlist); the existing per-station blocklist; RS-UI-16 (shared daypart definition).

**Acceptance criteria:**
- build_playlist.py gets `--station` (repeatable) and `--days N`.
- A definition (name, stations, period or daypart, top N) can be stored and rebuilt on demand.
- An explicit setting decides whether unconfirmed candidate ids may be added to a playlist (they are about 93% right in a first check), and whether only tracks with an id are used.
- Tracks blocked by the user are left out (relation to #5 and to the blocklist to be settled).

=====================================================
DEEL 3 — LATER: CONTAINERIZATION
=====================================================

### RS-OPS-01 — Containerize the application with a Dockerfile  [ADJUSTED]
**Description:** Create a production-ready Dockerfile that packages the RadioScrobbler application and its runtime dependencies into a reproducible container image.
**Depends on:** RS-SPOT-01.

**Acceptance criteria:**
- Builds the application from a clean checkout with a documented build command.
- Installs and pins required Python/runtime dependencies.
- Starts the backend application using a configurable host and port.
- Reads configuration and secrets from environment variables; no Spotify credentials are committed into the image or source code.
- Writes durable application data only to explicitly mounted paths/volumes (database file and Spotify token cache).
- The first Spotify authorization opens a browser, which a container cannot do: the documented procedure is to authorize once on the host and mount the resulting token cache.
- Includes a .dockerignore so local virtual environments, caches, logs, databases, and secrets are excluded from the image.
- Documents local build, run, stop, and upgrade steps.

**Notes:** Do this after the scripts are stable enough to run without frequent hands-on changes. During active development, local execution remains faster to iterate on. Use a bind mount for the scripts during development so you don't rebuild the image on every change.

### RS-OPS-02 — Create Docker Compose deployment for UI and backend  [ADJUSTED]
**Description:** Create a docker-compose.yml configuration that runs the UI and the backend/job runner together as one self-hosted installation, on SQLite. There is no separate database service: SQLite is a file, so the "database" is a volume (revisit only if SQLite stops being enough).
**Depends on:** RS-OPS-01, RS-OPS-05.

**Acceptance criteria:**
- A single documented `docker compose up -d` command starts all required services.
- Defines the UI and the backend/job runner as services, or one combined service if the chosen technology (RS-UI-00) makes that simpler; if two containers share the database file they share one local volume.
- Only the UI/application port is exposed to the host.
- Uses named volumes for the database file and any required application state.
- The database file lives on a local disk or volume, not on a network share (SQLite and SMB/NFS shares do not mix well: locking and WAL are not reliable there — a known SQLite warning, not tested here). A Synology share is for backups, not for the live database.
- Loads configuration and secrets through an ignored .env file or host environment variables.
- Starts services in dependency-aware order and includes health checks where practical.
- Supports restart policies so the system returns after a machine reboot or a service crash.
- Includes a documented backup and restore procedure for the database (an online SQLite backup written to a Synology share).
- Documents normal operations: start, stop, view logs, update images, and recover from a failed service.

**Notes:** Keep the first Compose setup single-user and local/self-hosted. Spotify OAuth, multi-user access, reverse proxies, and public internet exposure are Gen 2 work.
