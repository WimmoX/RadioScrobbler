# Spotify Web API — verzamelde kennis

Alles wat we empirisch en uit de officiële documentatie hebben uitgezocht
over Spotify's Web API, specifiek vanuit het perspectief van een
**Development Mode-app** (zoals deze — geen Extended Quota Mode, dat vereist
250.000+ maandelijkse actieve gebruikers, onhaalbaar voor een hobbyproject).
Details en de context waarin we dit ontdekten staan in `LessonsLearned.md`
(Les 5, 6, 9, 10, 11) — dit bestand is de opgeschoonde, doorzoekbare
naslag-versie daarvan.

## Toegangsniveau & quota

- Onze app zit in **Development Mode**. Dat betekent: beperkte endpoints,
  beperkte velden in responses, en een laag, ongepubliceerd quotum.
- **Sinds juli 2026** wordt de quota **per developer-account** geteld, niet
  meer per app/Client ID — een tweede app aanmaken geeft dus géén vers
  quotum. (Tot 25 Client ID's per account toegestaan, maar ze delen allemaal
  hetzelfde budget.)
- Er zijn **twee gescheiden mechanismes**, met andere tijdschalen:
  - **Rate limit**: rollend venster van 30 seconden. Geen gepubliceerd
    getal, kan wijzigen. Hier helpt een korte delay tussen calls.
  - **Quota**: een veel langduriger, ongepubliceerd dagbudget. Een 429
    hierdoor bevat sinds juli 2026 een JSON-body met
    `"error": {"reason": "QUOTA_EXCEEDED", ...}`.
- **Empirisch vastgesteld** (2026-09-19, zie Les 10): een quota-blokkade is
  géén rollend venster waarbij oude calls één voor één "wegvallen" — het is
  een **vaste teller die aftelt naar één vast toekomstig resetmoment**
  (gemeten met 15s nauwkeurigheid over bijna 8 uur). Duur bij dat incident:
  ~22-24 uur.
- **Retryen van een QUOTA_EXCEEDED maakt het erger, niet beter** — elke
  herhaalde poging verbruikt weer een stukje van hetzelfde uitgeputte
  budget (bevestigd door een andere developer op GitHub). Behandel het als
  terminaal: niet retryen, gewoon wachten tot het reset-tijdstip.
- **Empirisch datapunt voor het dagbudget**: bij één incident lukten nog
  ~400 nieuwe Search-matches voordat de quota dichtklapte (ergens tussen de
  400-800 daadwerkelijke HTTP-calls, want een deel van die matches kostte 2
  calls — zie "Search-strategie" hieronder). Tweede datapunt (2026-09-24):
  `QUOTA_EXCEEDED` bij ~700 calls in 24 uur. Sindsdien een **vaste** limiet
  van **650 calls per rollende 24u** (`quota.CALL_LIMIT`), die niet meer
  vanzelf omhoog of omlaag gaat. (Daarvoor: zelf-calibrerend, start 300,
  +25 per run die de limiet opmaakte — zie Les 10.)
- **Quota-buckets**: verschillende soorten endpoints lijken een apart
  budget te hebben. Empirisch bevestigd: tijdens een actieve Search-quota-
  blokkade werkten playlists lezen/toevoegen/verwijderen gewoon door. Ga er
  dus niet vanuit dat "quota op" betekent dat de hele API potdicht zit —
  test het per endpoint-categorie.

## Endpoints: wat werkt, wat niet (voor Development Mode)

| Endpoint | Werkt? | Notities |
|---|---|---|
| `GET /v1/search` | ✅ | Hoofdmatching-endpoint. Onderhevig aan de Search-quota hierboven. |
| `GET /v1/tracks/{id}` | ✅ | Los nummer opvragen, werkt prima. |
| `GET /v1/tracks?ids=...` (batch, tot 50) | ❌ **403 Forbidden** | Batch-variant geeft altijd 403 voor Dev Mode, ook als het enkelvoud werkt. Gebruik een loop van losse `GET /v1/tracks/{id}`-calls. |
| `POST /users/{user_id}/playlists` (playlist aanmaken, oud) | ❌ **403 Forbidden** | Sinds de februari 2026-migratie geblokkeerd voor Dev Mode (deadline was 9 maart 2026). In spotipy: dit is `user_playlist_create()`, al gemarkeerd als *deprecated*. |
| `POST /me/playlists` (playlist aanmaken, nieuw) | ✅ | Gebruik dit i.p.v. bovenstaande. In spotipy: `current_user_playlist_create()`. |
| `GET /me/playlists` | ✅ | Vereist scope `playlist-read-private`. |
| `GET /playlists/{id}/items` | ✅ | **Let op**: sinds de migratie heet het veld per item `item`, niet meer `track` — code die `entry["track"]` leest geeft een `KeyError`. |
| `POST /playlists/{id}/tracks` (toevoegen) | ✅ | |
| `DELETE /playlists/{id}/tracks` (verwijderen) | ✅ | |
| `PUT /playlists/{id}/tracks` (vervangen) | ✅ | |
| `GET /me/library/contains` (Liked Songs check) | ✅ | Vereist scope `user-library-read`. **Max 40 URI's per call** — 41+ geeft `400 Too many uris requested` (gemeten 2026-09-24; spotipy's `current_user_saved_tracks_contains` gebruikt dit endpoint). |
| `popularity`-veld op tracks | ❌ **verwijderd** | Sinds februari 2026 niet meer aanwezig in track-responses voor Dev Mode (samen met `available_markets`, `followers`, en user-velden `country`/`email`/`product`). Gebruik ReccoBeats' `/v1/track`/`/v1/track/search` als je toch een populariteitsscore wil — die geeft 'm wél. |
| Audio Features endpoint | ❌ **ingeperkt** | Sinds november 2024 achter een goedkeuringsmuur voor de meeste Dev Mode-apps. Gebruik ReccoBeats' `/v1/audio-features` als vervanger — zie `RECCOBEATS.md`. |

## Vereiste scopes

```
playlist-modify-public
playlist-modify-private
playlist-read-private   ← makkelijk te vergeten, nodig om te checken of een playlist al bestaat vóór je 'm aanmaakt
user-library-read        ← nodig voor de Liked Songs-check
```

## spotipy-specifieke valkuilen

**1. `spotipy.Spotify()` heeft standaard een eigen, onzichtbare retry.**
Met de default-instellingen (`max_retries=3`) vangt spotipy een 429 zelf al
af op urllib3-niveau en **slaapt intern de volledige `Retry-After` uit** —
tot bijna een uur, zonder dat je eigen code daar iets van meekrijgt of kan
onderbreken. Fix: `retries=0, status_retries=0` bij het aanmaken van de
client.

**2. Maar `retries=0` alleen verbergt daarna de échte foutinfo.** Met
`retries=0` onderschept urllib3's retry-adapter een 429 nog steeds (want
429 zit in de *default* `status_forcelist`), en omdat er met `total=0`
niets te retryen valt, gooit hij een `requests.exceptions.RetryError`
i.p.v. een normale `HTTPError`. Spotipy's foutafhandeling voor die twee
gevallen is niet gelijk: de `RetryError`-tak leest de JSON-body **niet**
uit, dus je krijgt een neppe `Retry-After` (fallback-waarde, vaak `1`) en
`reason` is nooit `"QUOTA_EXCEEDED"` — zelfs niet tijdens een bevestigd
actieve, urenlange blokkade. **Fix**: geef ook `status_forcelist=[999]` mee
(een onmogelijke statuscode) zodat urllib3 nooit meer denkt dat 't iets
moet retryen, en elke fout via het normale, correct-parserende pad loopt.

```python
spotipy.Spotify(
    auth_manager=...,
    retries=0,
    status_retries=0,
    status_forcelist=[999],
)
```

⚠️ `status_forcelist=[]` (lege lijst) werkt **niet** — spotipy doet intern
`status_forcelist or self.default_retry_codes`, en een lege lijst is
*falsy* in Python, dus dat valt stilzwijgend terug op de default (die 429
juist wél bevat). Gebruik een niet-lege, onmogelijke waarde zoals `[999]`.

**3. Verouderde methodes vermijden:**
- `user_playlist_create()` → gebruik `current_user_playlist_create()`.
- `sp.tracks([...])` (batch) → gebruik een loop van `sp.track(id)`.

**4. `sp.playlist_items()`-resultaten: `item["item"]`, niet `item["track"]`.**

## Foutmeldingen — wat ze betekenen

| Foutmelding / situatie | Betekenis | Actie |
|---|---|---|
| `429`, generieke `Retry-After`, geen `reason` | Gewone rate limit (rollend 30s-venster) | Retry met backoff, respecteer `Retry-After` |
| `429`, `reason: "QUOTA_EXCEEDED"` | Dagbudget op | **Niet retryen.** Wacht tot `Retry-After` verstreken is |
| `403 Forbidden` op een endpoint dat "zou moeten werken" | Vaak een Dev Mode-beperking op dát specifieke endpoint (zie tabel hierboven), geen scope-probleem | Check eerst de endpoint-tabel, dan pas scopes |
| `requests.exceptions.RetryError` / "Max Retries reached" | urllib3 onderschepte de fout zelf, echte info is weg | Zie spotipy-valkuil #2 hierboven — fix de client-config, niet de foutafhandeling |

## Best practices die we hebben vastgesteld

1. **Cache elke match lokaal, zoek nooit twee keer hetzelfde op** (zie
   `track_match`/`tracks` in `db.py`). Dit is de eigenlijke oplossing tegen
   quota-problemen, niet "trager gaan".
2. **Vast budget net onder het gemeten plafond** — zie `quota.py` (650/24u).
   Spotify publiceert de limiet niet en kan 'm wijzigen; een echte blokkade
   stopt alle Search-calls tot `Retry-After`.
3. **Bij Search-uitval overschakelen op ReccoBeats** (zie `reccobeats.py`)
   i.p.v. de hele run stoppen — playlist-acties blijven namelijk gewoon
   werken tijdens een Search-blokkade (aparte quota-bucket).
4. **Altijd `retries=0, status_retries=0, status_forcelist=[999]`** bij het
   aanmaken van de Spotify-client — zie spotipy-valkuilen hierboven.
5. Test een 429-scenario altijd door de **echte response te inspecteren**
   (`e.reason`, `e.headers.get("Retry-After")`) i.p.v. te vertrouwen op wat
   je dénkt dat er gebeurt — de spotipy-valkuil hierboven werd alleen
   gevonden door dat expliciet te printen.

---

## Andere externe databronnen

### OnlineRadioBox — afspeelhistorie per zender
```
https://onlineradiobox.com/{locale}/{slug}/playlist/{day}
```
- `day`: 0 = vandaag, t/m 6 = 7 dagen terug. **Bewaart zelf maar 7 dagen** —
  hard plafond, vandaar dat wij regelmatig moeten scrapen om een langere
  eigen geschiedenis op te bouwen (zie `PROGRESS.md`).
- Vereist header `X-Requested-With: XMLHttpRequest` voor de per-dag
  AJAX-variant.
- Zenders zijn per land gegroepeerd in de URL. Een station-ID in onze
  `stations.py` is een kale slug (dan gaan we uit van `nl`) of
  `locale/slug` voor een ander land — zie `_split_station()` in
  `scraper.py`.
- Zoeken via `https://onlineradiobox.com/search?q=...` — geeft soms ook
  irrelevante "andere zenders"-suggesties mee, dus met de hand nakijken
  (zie `add_station.py`).
- Geen API-key nodig, gewone HTML-scraping met BeautifulSoup.

### ReccoBeats — audio-profiel + fallback-zoekfunctie
Gratis, geen API-key. Volledige documentatie staat in **`RECCOBEATS.md`**
(endpoints, velden, batchen, de eigenaardigheid dat `searchText` een vrij
letterlijke frase-match is i.p.v. fuzzy search). Twee toepassingen in dit
project:
1. `fetch_audio_features.py` — energy/valence/danceability/etc.
2. `reccobeats.py` — fallback-zoekfunctie als Spotify's Search geblokkeerd is.

### Relisten.nl — afspeelhistorie per zender (jaren) + Spotify-links
```
https://www.relisten.nl/playlists/{slug}/{dd-mm-yyyy}.html      # hele dag op één pagina
https://www.relisten.nl/out?songID={id}&option=spotify          # 302 -> open.spotify.com/track/{id}
```
- **Historie:** dagpagina's bestaan minstens tot sept 2015 (Radio 2) en
  sept 2012 (NPO 3FM); ~140–350 plays per dag, tijdstip tot op de seconde.
  Geen login; `robots.txt` blokkeert alleen `/logs/`. Voorwaarden niet
  gecontroleerd — het is een door advertenties gefinancierde site, dus
  rustig scrapen (1 s tussen dagen, 0,5 s tussen `out`-verzoeken).
- **Zenders die wij gebruiken/wilden:** `radio2`, `kink-distortion`, `3fm`
  (OnlineRadioBox heeft geen 3FM). Niet dezelfde zender: "SLAM!" /
  "SLAM! Hardstyle" (wij: Non Stop), "Pinguin Radio" (wij: Classics). Niet
  aanwezig: KINK Classics, Zeilsteen.
- **Valkuil:** de dagpagina toont ook "nieuwe tracks" uit een zijbalk met een
  andere datum — filteren op datum (zie `relisten.parse_day()`).
- **Spotify-id via `out`:** geen Spotify-API, dus geen quota. Alleen
  relisten's eigen koppeling: ~93% correct in een steekproef (Radio 2: 28
  goed / 2 fout / 10 niet te controleren; 3FM: 12 / 1 / 24), ~11% van de
  nummers heeft geen link. Daarom opgeslagen als **kandidaat**
  (`verified = 0`), nooit als bevestigd. `option=itunes` geeft Apple
  Music-links (relevant voor issue #2) maar één geteste link was duidelijk fout.
- **Tijdstippen wijken af van OnlineRadioBox:** relisten loopt meestal 1–2
  min voor (gemeten op Radio 2: +1 min in 66% van de gevallen, tot +5), en
  namen worden anders geschreven (accenten, `&` / `,` / `Ft.`,
  "Cult, The", "Adele" tegenover "Adkins, A"). Zie `db.save_plays()`.

### MusicBrainz — onderzocht, niet gebruikt
Publieke, gratis muziekdatabase (`https://musicbrainz.org/ws/2/...`, geen
auth nodig, wel een etiquette-richtlijn van ~1 request/seconde). Zoeken op
artiest+titel werkt prima. **Maar**: Spotify-koppelingen zijn crowdsourced
"relationships" (`inc=url-rels`) en vaak **afwezig**, zelfs voor bekende
nummers — getest op een specifieke Blind Guardian-opname en die had er
geen. Daardoor minder betrouwbaar dan ReccoBeats specifiek voor het
opzoeken van Spotify-ID's, dus (nog) niet geïntegreerd in de pipeline.
