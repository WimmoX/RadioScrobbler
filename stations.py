"""Known stations: OnlineRadioBox station identifier -> Spotify playlist name.

Station identifiers are either a bare slug (assumes the 'nl' locale on
OnlineRadioBox) or 'locale/slug' for stations listed under another country.
"""

STATIONS = {
    "pingclass": "Penguin Classics",
    "kinkclassics": "KINK Classics",
    "kinkdistortion": "KINK Distortion",
    "zeilsteen": "Zeilsteen Radio",
    "slamnonst": "SLAM! Non Stop",
    "radio2": "NPO Radio 2",
    "npo3fm": "NPO 3FM",
}

# Stations whose history is scraped from relisten.nl (see relisten.py) instead
# of OnlineRadioBox: station id -> relisten slug. Relisten keeps years of
# history and has NPO 3FM, which OnlineRadioBox has no data for. Keep this
# BELOW STATIONS: add_station.py appends to the STATIONS block by name, and
# remove_station.py drops every line keyed on the station id.
RELISTEN_SLUGS = {
    "radio2": "radio2",
    "kinkdistortion": "kink-distortion",
    "npo3fm": "3fm",
}
