"""Shared logic for picking the right track out of a list of search results
(artist/title/name items shaped like Spotify's track objects), regardless of
which API produced the candidates — used by both sync_playlist.py (Spotify
Search) and reccobeats.py (ReccoBeats Search), which is also why this lives
in its own module instead of inside either of them (avoids a circular
import between the two).
"""
import difflib
import re
import unicodedata

MIN_TITLE_SIMILARITY = 0.6  # below this, we'd rather report "no match" than guess wrong
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12",
}


def _strip_accents(text: str) -> str:
    """'Caffé' -> 'Cafe', so accent differences between the radio source and
    Spotify's spelling don't break comparisons (they used to: stripping
    non-ASCII characters outright turned 'Caffé' into 'Caff', not 'Caffe')."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize_numbers(text: str) -> str:
    """'Ten Over Ten' -> '10 over 10', so it lines up with a title that spells
    numbers as digits (or vice versa) instead of scoring as barely similar."""
    return " ".join(_NUMBER_WORDS.get(word, word) for word in text.lower().split())


_TITLE_SUFFIX = re.compile(
    r"\s*[\(\[][^)\]]*(feat|ft|with)\.?\s[^)\]]*[\)\]]|\s+-\s+.+$", re.IGNORECASE
)


def _core_title(text: str) -> str:
    """Strip things Spotify adds that a radio announcer wouldn't say out loud:
    '(feat. X)' credits and ' - Remastered 2008' / ' - Radio Edit' style
    suffixes, which otherwise drag the similarity score down for an
    otherwise-correct match (e.g. 'Torch' vs 'Torch - Original 7" Single
    Version')."""
    return _TITLE_SUFFIX.sub("", text).strip()


def _title_similarity(a: str, b: str) -> float:
    variants_a = {a.lower(), _normalize_numbers(a), _core_title(a).lower()}
    variants_b = {b.lower(), _normalize_numbers(b), _core_title(b).lower()}
    return max(
        difflib.SequenceMatcher(None, va, vb).ratio()
        for va in variants_a for vb in variants_b
    )


_ARTIST_SEPARATORS = re.compile(r"\s*(?:&|,|/|\bfeat\.?\b|\bft\.?\b|\bx\b)\s*", re.IGNORECASE)


def _normalize_artist(name: str) -> str:
    """'Fischer-Z' and 'Fischer Z' (radio vs Spotify's stylisation) should be
    treated as the same artist, so strip everything but letters/digits
    (after transliterating accents, not just discarding them). Also drop a
    leading 'The ' ('Bangles' vs Spotify's 'The Bangles')."""
    name = re.sub(r"^the\s+", "", _strip_accents(name.lower()))
    return re.sub(r"[^a-z0-9]", "", name)


def _artist_matches(item: dict, artist: str) -> bool:
    # Radio sources often credit collabs as one string ("Bonobo & Joy Crookes"),
    # while Spotify lists each as a separate artist on the track — split ours
    # the same way and match if any part overlaps. But "&" and "/" can also be
    # part of a single act's actual name ("Echo & the Bunnymen", "AC/DC"), so
    # also check the whole un-split name — whichever form lines up with what
    # Spotify has wins.
    searched = {_normalize_artist(n) for n in _ARTIST_SEPARATORS.split(artist) if n.strip()}
    searched.add(_normalize_artist(artist))
    on_track = {_normalize_artist(a["name"]) for a in item["artists"]}
    return bool(searched & on_track)


def best_candidate(items: list, artist: str, title: str) -> dict | None:
    """Pick the item that's actually the right song, not just the search API's
    #1 ranked result — for loosely-matched queries, that ranking isn't always
    right (e.g. "Royal Blood - 10 over 10" from the radio once matched "Come
    on Over" instead of the correct "Ten Over Ten", which was ranked #1 in
    the same response). We require the artist to match (allowing for collabs
    and minor stylisation differences) and pick the item with the most
    similar title; if nothing clears the similarity bar, we report no match
    instead of silently caching a wrong one."""
    same_artist = [item for item in items if _artist_matches(item, artist)]
    if not same_artist:
        return None
    # Rank by the lenient score first (so "Torch - Single Version" can still
    # win when it's the only candidate), but break ties on the raw,
    # un-stripped similarity — otherwise "Marliese" and "Marliese -
    # Reimagined" score identically and we could just as easily pick the
    # remake over the original the radio actually played.
    def rank(item):
        raw = difflib.SequenceMatcher(None, item["name"].lower(), title.lower()).ratio()
        return (_title_similarity(item["name"], title), raw)
    best = max(same_artist, key=rank)
    if _title_similarity(best["name"], title) < MIN_TITLE_SIMILARITY:
        return None
    return best


# --- Readings of a scraped "artist - title" pair ------------------------------
# Radio sources garble the text: artist and title swapped ("War Of The Worlds |
# My Vitriol"), apostrophes turned into spaces ("They re On To Me"), version
# tags ("(albumversie)", "- Radio Edit"), a hyphenated artist split in two
# ("Hard | Fi - Living For The Weekend"). ReccoBeats' search is a literal
# phrase match on the title, so the query has to be clean AND the right way
# round; Spotify's is fuzzy but best_candidate() still checks the artist.

_CONTRACTION = re.compile(r"\b([A-Za-z]+) (s|re|t|ll|ve|d|m)\b", re.IGNORECASE)
_BRACKETED = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
# Only tails that are clearly a version tag: a plain " - x" can also be the
# real title after a hyphenated artist was split ("Hard - Fi - Living For...").
_VERSION_TAIL = re.compile(
    r"\s+-\s+.*\b(edit|remaster(ed)?|version|mix|remix|live|mono|stereo|single|extended|"
    r"acoustic|instrumental|demo|reprise|\d{4})\b.*$", re.IGNORECASE)
_HYPHEN_SPLIT = re.compile(r"^(.{1,15}?) - (.+)$")


def fix_contractions(text: str) -> str:
    """'They re On To Me' -> "They're On To Me", 'k s Choise' -> "k's Choise"."""
    return _CONTRACTION.sub(r"\1'\2", text)


def clean_title(text: str) -> str:
    """Drop bracketed parts and ' - Radio Edit' / ' - Remastered 2011' style tails, fix contractions;
    falls back to the input when nothing would be left."""
    cleaned = _VERSION_TAIL.sub("", _BRACKETED.sub("", fix_contractions(text))).strip()
    return cleaned or text


def text_variants(artist: str, title: str) -> list[tuple[str, str, str]]:
    """(artist, title, label) readings of a scraped pair, most likely first,
    without duplicates. The first one is always the text as scraped."""
    variants = [(artist, title, "as-is")]
    cleaned = (fix_contractions(artist), clean_title(title), "cleaned")
    if cleaned[:2] != (artist, title):
        variants.append(cleaned)
    m = _HYPHEN_SPLIT.match(title)
    if m:
        variants.append((f"{artist}-{m.group(1)}", clean_title(m.group(2)), "resplit"))
    variants.append((fix_contractions(title), clean_title(artist), "swapped"))
    seen, unique = set(), []
    for v in variants:
        if v[:2] not in seen:
            seen.add(v[:2])
            unique.append(v)
    return unique


def best_candidate_variants(items: list, artist: str, title: str) -> tuple[dict | None, str | None]:
    """best_candidate() over every reading of the pair, on the SAME search
    results — no extra API calls. Returns (item, label) or (None, None)."""
    for v_artist, v_title, label in text_variants(artist, title):
        best = best_candidate(items, v_artist, v_title)
        if best:
            return best, label
    return None, None
