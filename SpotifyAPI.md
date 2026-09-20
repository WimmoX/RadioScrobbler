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
  calls — zie "Search-strategie" hieronder). We houden nu zelf een
  conservatieve, **zelf-calibrerende** limiet aan — zie `quota.py`: start op
  300 calls/rollend-24u, +25 bij een run die de limiet succesvol opzoekt
  zonder blokkade, terug-snappen naar het werkelijke aantal bij een échte
  blokkade.
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
| `GET /me/tracks/contains` (Liked Songs check) | ✅ | Vereist scope `user-library-read`. |
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
2. **Zelf-calibrerend budget i.p.v. een geraden vast getal** — zie
   `quota.py`. Spotify publiceert de limiet niet en kan 'm wijzigen.
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

### MusicBrainz — onderzocht, niet gebruikt
Publieke, gratis muziekdatabase (`https://musicbrainz.org/ws/2/...`, geen
auth nodig, wel een etiquette-richtlijn van ~1 request/seconde). Zoeken op
artiest+titel werkt prima. **Maar**: Spotify-koppelingen zijn crowdsourced
"relationships" (`inc=url-rels`) en vaak **afwezig**, zelfs voor bekende
nummers — getest op een specifieke Blind Guardian-opname en die had er
geen. Daardoor minder betrouwbaar dan ReccoBeats specifiek voor het
opzoeken van Spotify-ID's, dus (nog) niet geïntegreerd in de pipeline.
