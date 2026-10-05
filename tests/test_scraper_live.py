import scraper


class Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def page(*rows):
    cells = "".join(f'<tr><td class="tablelist-schedule__time">{t}</td>'
                    f'<td class="track_history_item">{track}</td></tr>' for t, track in rows)
    return f'<table class="tablelist-schedule">{cells}</table>'


def test_the_live_label_in_any_language_is_the_current_track(monkeypatch):
    html = page(("En direct", "Arno - Les yeux de ma mère"), ("Live", "Doe Maar - Smoorverliefd"),
                ("14:05", "dEUS - Suds & Soda"))
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: Resp(html))
    plays = scraper._parse_day("be/studiobrussel", 0)
    assert [p.artist for p in plays] == ["Arno", "Doe Maar", "dEUS"]
    assert plays[2].played_at.hour == 14 and plays[2].played_at.minute == 5


def test_a_label_instead_of_a_time_on_an_earlier_day_is_skipped(monkeypatch):
    html = page(("En direct", "Arno - Les yeux de ma mère"), ("09:30", "dEUS - Suds & Soda"))
    monkeypatch.setattr(scraper.requests, "get", lambda *a, **k: Resp(html))
    assert [p.artist for p in scraper._parse_day("be/studiobrussel", 2)] == ["dEUS"]
