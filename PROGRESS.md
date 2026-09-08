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
| KINK Classics | `kinkclassics` |
| KINK Distortion | `kinkdistortion` |

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

## Status (2026-09-08)
- **KINK Radio (`kink`) is uit het systeem gehaald** op verzoek (muzieksmaak
  paste niet) — verwijderd uit `stations.py`, alle `plays`-data voor die
  zender gewist, en de 14 nummers die *uitsluitend* via KINK Radio in
  "RadioScrobbled" terechtkwamen zijn er ook weer uitgehaald (nummers die
  ook op andere zenders draaiden zijn blijven staan). KINK Classics en KINK
  Distortion blijven gewoon meedoen — dit was specifiek de brede "KINK
  Radio"-zender, niet het hele KINK-merk.
- **BBC Radio 6 Music is ook uit het systeem gehaald** op verzoek (muziek
  beviel niet) — zelfde behandeling: uit `stations.py`, alle `plays`-data
  gewist (2064 rows), en de 24 nummers die *uitsluitend* via deze zender in
  "RadioScrobbled" stonden eruit gehaald (6 bleven staan, want die draaiden
  ook op Pinguin Classics/KINK Classics). Dit was tegelijk de enige zender
  met een niet-`nl`-locale (`uk/bbcradio6`); de multi-locale support in
  `scraper.py` blijft gewoon bestaan voor een eventuele toekomstige zender.
- Scrapen (`scrape.py`) getest en werkt voor de 3 resterende zenders,
  dedup bevestigd over meerdere runs.
- `sync_playlist.py` **werkt nu end-to-end, bevestigd in productie**: eerste
  echte playlist ("RadioScrobbled", een handmatige top-30-selectie over alle
  zenders behalve KINK Distortion) succesvol aangemaakt en gevuld op Spotify.
  Onderweg nog twee bugs gevonden en gefixt die alleen in de praktijk (niet
  in code review) aan het licht kwamen: spotipy's verborgen interne retry
  (Les 5) en het door Spotify geblokkeerde `user_playlist_create()`-endpoint
  (Les 6). `resync_playlist.py` is met dezelfde onderliggende functies
  gebouwd maar nog niet apart end-to-end getest.
- Matching-logica in `find_track_uri()`/`_best_candidate()` flink verbeterd
  na een echte misser (Royal Blood) en enkele valse afwijzingen (accenten,
  Spotify-titeltoevoegingen zoals "(feat. X)" of "- Single Version") — zie
  Les 7 en het vervolg daarop in `LessonsLearned.md`. "RadioScrobbled" staat
  nu op 30 correct gematchte nummers.
- Nog niet gebouwd: Level 2 (dagdelen), Docker/UI, en de "Verbannen Nummers"-feature
  (zie hieronder).
- `fetch_audio_features.py` gebouwd en getest — werkt en cachet correct. Nu
  ook echt bruikbaar: `spotify_matches` bevat sinds vandaag voor het eerst
  échte matches.

## Openstaand: "Verbannen Nummers"-playlist (nog niet gebouwd, wacht op akkoord)
Idee: een Spotify-playlist "Verbannen Nummers" als zichtbare, handmatig te beheren
ban-lijst (i.p.v. of naast de lokale `blocklist`-tabel). Open vraag: is dit
één gedeelde/globale lijst voor alle zenders, of per zender? Zodra dat
akkoord is, bouwen: nummers uit main-playlists verwijderd (handmatig of via
resync gedetecteerd) → toegevoegd aan Verbannen Nummers; alles wat in Verbannen Nummers
staat (ook zelf toegevoegd) wordt bij elke sync genegeerd, maar blijft wel
gewoon in de scrape/database staan.
