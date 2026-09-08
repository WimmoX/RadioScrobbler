# ReccoBeats API — gebruiksaantekeningen

Gratis, geen API-key nodig (in elk geval voor de calls die we getest hebben).
Vervangt functioneel Spotify's ingeperkte Audio Features-endpoint: geeft
`energy`, `valence`, `danceability` e.d. per nummer — precies het soort
"profiel" (high energy / slow / easy) waar we naar op zoek waren.

Let op: de documentatiesite (`reccobeats.com/docs/...`) is een client-side
gerenderde React-app en dus niet zomaar te scrapen/lezen zonder browser. De
onderliggende doc-*tekst*pagina's (Rate Limiting, Request And Response) zijn
wél gewoon server-rendered en leesbaar. Alle onderstaande info komt uit die
pagina's + eigen empirische tests tegen de live API (2026-09-08).

## Base URL
```
https://api.reccobeats.com
```
(Niet `reccobeats.com/api/...` — dat 404't. De API draait op het `api.`-subdomein.)

## Track-identificatie
Gebruikt **Spotify track-ID's** (het stuk na `/track/` in een Spotify-URL,
bv. `0VjIjW4GlUZAMYd2vXMi3b` voor "Blinding Lights"). Dat zijn dezelfde ID's
die we al krijgen uit de Spotify Search-matching in `sync_playlist.py` — dus
geen aparte matching-stap nodig, gewoon de bestaande `spotify_uri`
(`spotify:track:{id}`, pak het laatste deel na de laatste `:`).

## Endpoints

### `GET /v1/track?ids=...`
Metadata: titel, artiest(en), duur, ISRC, Spotify-link, populariteit.

```
curl "https://api.reccobeats.com/v1/track?ids=0VjIjW4GlUZAMYd2vXMi3b"
```
```json
{
  "content": [{
    "id": "25c8ca63-5895-4572-84eb-a7040bc08c4d",
    "trackTitle": "Blinding Lights",
    "artists": [{"id": "...", "name": "The Weeknd", "href": "https://open.spotify.com/artist/..."}],
    "durationMs": 200040,
    "isrc": "USUG11904206",
    "href": "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b",
    "popularity": 87
  }]
}
```

### `GET /v1/audio-features?ids=...`
Het echte "profiel" van een nummer.

```
curl "https://api.reccobeats.com/v1/audio-features?ids=0VjIjW4GlUZAMYd2vXMi3b"
```
```json
{
  "content": [{
    "id": "25c8ca63-5895-4572-84eb-a7040bc08c4d",
    "href": "https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b",
    "isrc": "USUG11904206",
    "acousticness": 0.00143,
    "danceability": 0.513,
    "energy": 0.73,
    "instrumentalness": 0.0000954,
    "key": 1,
    "liveness": 0.0897,
    "loudness": -5.94,
    "mode": 1,
    "speechiness": 0.0598,
    "tempo": 171.001,
    "valence": 0.334
  }]
}
```

Veldbetekenis (identiek aan het oude Spotify-schema):
| Veld | Betekenis |
|---|---|
| `energy` | Intensiteit/kracht (0-1), **los van tempo** — dit is precies het "hoge energie, laag bpm"-scenario dat je zocht |
| `valence` | Hoe positief/vrolijk het klinkt (0-1) |
| `danceability` | Hoe geschikt om op te dansen (0-1) |
| `acousticness` | Kans dat het akoestisch is (0-1) |
| `instrumentalness` | Kans dat er geen vocals inzitten (0-1) |
| `liveness` | Kans dat het een live-opname is (0-1) |
| `speechiness` | Hoeveel gesproken woord erin zit (0-1) |
| `tempo` | BPM |
| `loudness` | Gemiddeld volume in dB |
| `key` / `mode` | Toonsoort / majeur(1)-mineur(0) |

## Batchen (meerdere nummers in één call)
Twee ondersteunde vormen, beide getest en werkend:
```
GET /v1/audio-features?ids=1,2,3                  # comma-separated
GET /v1/audio-features?ids=1&ids=2&ids=3          # herhaalde parameter
```
Geen expliciete bovengrens gedocumenteerd voor het aantal ID's per call —
in de praktijk stapsgewijs opschalen en op 400/413-achtige fouten letten.

## Fouten & rate limiting
Standaard HTTP-codes (200/400/401/403/404/429/500) plus een eigen
error-code-veld (bv. `4001` = verplichte parameter ontbreekt, `4291` = rate
limit overschreden). Rate limits zijn niet in een concreet getal
gepubliceerd ("intern geconfigureerd, wacht op basis van `Retry-After`").
→ We kunnen de bestaande `_call_with_retry`-helper uit `sync_playlist.py`
hergebruiken (dezelfde 429/backoff-aanpak).

## Integratie — gebouwd
`fetch_audio_features.py` haalt voor alle al-gematchte tracks in
`spotify_matches` de ReccoBeats-kenmerken op en cachet ze in de tabel
`audio_features` (spotify_uri → energy/valence/danceability/tempo/etc. +
fetched_at). Batcht per 40 ID's, hergebruikt dezelfde 429/backoff-aanpak als
Spotify. Getest (2026-09-08) met twee bekende tracks + één niet-bestaand
nummer (om de "geen match"-cache te checken) — werkt, en een herhaalde run
doet niets meer zodra alles gecachet is.

Nog niet gebruikt: `sync_playlist.py` (of een toekomstige dagdeel-indeling,
Level 2) zou hierop kunnen filteren/sorteren, bv. "alleen high-energy
nummers in de weekdag-ochtend-playlist". Vereist wel eerst dat
`sync_playlist.py` daadwerkelijk tracks matcht (zie `PROGRESS.md` —
geblokkeerd geweest door de Spotify-rate-limit-lockout).
