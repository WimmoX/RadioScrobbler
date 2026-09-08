# RadioScrobbler — voortgang

## Doel
Spotify-playlists automatisch vullen met nummers die recent gedraaid zijn op
bepaalde internetradiozenders, zodat je altijd verse muziek hebt zonder
reclame.

**Scope:**
- **MVP**: playlist bijgewerkt met nummers van de afgelopen periode voor één
  zender (Pinguin Classics).
- **Level 1**: nummers die niet meer gedraaid worden, vallen er ook weer af.
- **Level 2** *(nog niet gebouwd)*: aparte playlists per dagdeel (bv.
  weekdag-dag/avond, vrijdag-middag/avond, zaterdag-ochtend/avond,
  zondag-ochtend/middag/avond).
- **Einddoel** *(nog niet gebouwd)*: Docker-container met minimale UI om
  zender + muziekdienst te kiezen.

## Architectuur

### Databron: OnlineRadioBox, niet Pinguin Radio zelf
Pinguin Radio's eigen site (pinguinradio.com) is een Angular SPA zonder
server-rendered data — de tracklist wordt clientside opgehaald, waarschijnlijk
via een Supabase-backend, maar de query zit in een lazy-loaded JS-chunk die we
niet zonder browser konden vinden. In plaats daarvan gebruiken we
**OnlineRadioBox** (`onlineradiobox.com/nl/{slug}/playlist/{day}`), dat voor
duizenden zenders tijd + artiest + titel bijhoudt. Vereist een
`X-Requested-With: XMLHttpRequest`-header voor de per-dag AJAX-variant.

Bekende zenders (`stations.py`):
| Zender | Station-ID |
|---|---|
| Pinguin Classics | `pingclass` |
| KINK Radio | `kink` |
| KINK Classics | `kinkclassics` |
| KINK Distortion | `kinkdistortion` |
| BBC Radio 6 Music | `uk/bbcradio6` |

OnlineRadioBox groepeert zenders per land in de URL (`/nl/...`, `/uk/...`,
etc.). Een station-ID is dus óf een kale slug (dan gaan we uit van `nl`), óf
`locale/slug` voor zenders onder een ander land — zie `_split_station()` in
`scraper.py`.

**Beperking**: OnlineRadioBox bewaart zelf maar **7 dagen** geschiedenis.
Voor een langer venster (bv. 2-3 weken) moet het scrape-script regelmatig
(handmatig of via cron) draaien — de lokale database stapelt dat vanzelf op.

### Lokale SQLite-database (`db.py`)
Losse database i.p.v. rechtstreeks tegen Spotify praten, om drie redenen:
1. Langere geschiedenis opbouwen dan de 7 dagen die OnlineRadioBox geeft.
2. Spotify Search-resultaten cachen, zodat we nooit twee keer naar hetzelfde
   nummer zoeken (zie "Les 1" hieronder).
3. Lokaal bijhouden wat er (naar ons weten) al in een Spotify-playlist staat,
   zodat we alleen het verschil (toevoegen/verwijderen) naar Spotify hoeven
   te sturen in plaats van steeds de hele boel te herbouwen.

Tabellen:
- `plays` — station_slug, artist, title, played_at (ruwe scrape-data, dedupliceert vanzelf op basis van PRIMARY KEY).
- `spotify_matches` — cache van artiest+titel → Spotify-URI (of `NULL` = bewust "geen match gevonden", ook gecached zodat we dat niet opnieuw proberen).
- `playlists` — station_slug → Spotify playlist-ID (voorkomt dat we elke run opnieuw playlists moeten opzoeken).
- `playlist_tracks` — wat we denken dat er (per zender) in de Spotify-playlist staat.
- `blocklist` — nummers die je zelf uit een playlist hebt verwijderd; worden nooit automatisch opnieuw toegevoegd.
- `audio_features` — cache van ReccoBeats-kenmerken per Spotify-URI (energy, valence, danceability, tempo, etc.).

### Scripts
- **`scrape.py [station]`** — haalt de laatste 7 dagen op van OnlineRadioBox en
  slaat ze op in `plays`. Geen Spotify-calls, dus altijd veilig om te draaien.
  Draai dit regelmatig (dagelijks) om historie op te bouwen.
- **`sync_playlist.py [station]`** — leest de laatste 14 dagen uit de
  database, matcht alleen *nieuwe* artiest/titel-combinaties via Spotify
  Search (rest komt uit cache), berekent lokaal het verschil met wat er al in
  de playlist staat (`to_add` / `to_remove`), en stuurt alléén dat verschil
  naar Spotify. Nummers op de blocklist worden nooit toegevoegd; geliked
  nummers (via Spotify's "Liked Songs") worden nooit verwijderd.
- **`resync_playlist.py [station]`** — haalt de écht huidige inhoud van de
  Spotify-playlist op (voor het geval je zelf iets hebt toegevoegd/verwijderd
  in de Spotify-app), zet de lokale `playlist_tracks`-cache gelijk aan de
  realiteit, en voegt handmatig verwijderde nummers toe aan de blocklist.
  Af en toe handmatig draaien, na eigen wijzigingen in Spotify.
- **`stations.py`** — mapping van zender-slug naar playlistnaam, gedeeld door
  de scripts.
- **`fetch_audio_features.py`** — haalt voor alle al-gematchte Spotify-tracks
  (uit `spotify_matches`) de ReccoBeats-kenmerken op (energy, valence,
  danceability, tempo, ...) en cachet ze in `audio_features`. Geen API-key of
  Spotify-login nodig, kan onafhankelijk van `sync_playlist.py` draaien
  zodra er matches zijn. Zie `RECCOBEATS.md`.

### Config
`.env` (niet in git, zie `.env.example`): Spotify client ID/secret/redirect
URI, standaard zender-slug/playlist-naam, en `DB_PATH` (pad naar het
SQLite-bestand — kan wijzen naar een gemounte Synology-share zodat de
geschiedenis persistent is, nu nog lokaal op de Mac).

## Lessons learned
Zie `LessonsLearned.md` voor opgeloste problemen (rate limits, dedup-bugs,
scraping-aanpak) — apart bestand zodat we niet steeds tegen hetzelfde
aanlopen.

## Status (2026-09-07/08)
- Scrapen (`scrape.py`) getest en werkt voor alle 4 zenders, dedup bevestigd
  over meerdere runs.
- `sync_playlist.py` en `resync_playlist.py` gebouwd (incl. diff-gebaseerde
  updates, blocklist, liked-bescherming) maar **nog niet end-to-end getest**
  — geblokkeerd door de rate-limit-lockout van Les 1 (verwacht opgeheven rond
  2026-09-08 avond).
- Nog niet gebouwd: Level 2 (dagdelen), Docker/UI, en de "Verbannen Nummers"-feature
  (zie hieronder).
- `fetch_audio_features.py` gebouwd en getest (handmatig, met tijdelijke
  testregels) — werkt en cachet correct. Kan pas écht iets doen zodra
  `sync_playlist.py` voor het eerst tracks aan `spotify_matches` heeft
  toegevoegd.

## Openstaand: "Verbannen Nummers"-playlist (nog niet gebouwd, wacht op akkoord)
Idee: een Spotify-playlist "Verbannen Nummers" als zichtbare, handmatig te beheren
ban-lijst (i.p.v. of naast de lokale `blocklist`-tabel). Open vraag: is dit
één gedeelde/globale lijst voor alle zenders, of per zender? Zodra dat
akkoord is, bouwen: nummers uit main-playlists verwijderd (handmatig of via
resync gedetecteerd) → toegevoegd aan Verbannen Nummers; alles wat in Verbannen Nummers
staat (ook zelf toegevoegd) wordt bij elke sync genegeerd, maar blijft wel
gewoon in de scrape/database staan.
