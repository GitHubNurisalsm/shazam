from bot.services.formatter import format_track, track_buttons
from bot.services.recognition.base import TrackResult


def test_full_card() -> None:
    track = TrackResult(
        title="Blinding Lights", artist="The Weeknd", album="After Hours", year="2020", genre="Pop"
    )
    assert format_track(track) == (
        "🎵 <b>Blinding Lights</b>\n👤 The Weeknd\n💿 After Hours · 2020\n🏷 Pop"
    )


def test_minimal_card_has_no_empty_lines() -> None:
    assert format_track(TrackResult(title="Song", artist="Artist")) == "🎵 <b>Song</b>\n👤 Artist"


def test_year_without_album() -> None:
    assert format_track(TrackResult(title="S", artist="A", year="1999")).endswith("💿 1999")


def test_html_in_metadata_is_escaped() -> None:
    track = TrackResult(title="<b>Rock & Roll</b>", artist="AC<DC>", album="<i>x</i>")
    text = format_track(track)
    assert "&lt;b&gt;Rock &amp; Roll&lt;/b&gt;" in text
    assert "AC&lt;DC&gt;" in text
    assert "<i>" not in text


def test_very_long_fields_fit_into_photo_caption() -> None:
    long = "x" * 5000
    track = TrackResult(title=long, artist=long, album=long, year="2020", genre=long)
    assert len(format_track(track)) < 1024


def test_buttons_with_all_links() -> None:
    track = TrackResult(
        title="Blinding Lights",
        artist="The Weeknd",
        links={"shazam": "https://shazam/x", "apple_music": "https://music.apple.com/x"},
    )
    assert track_buttons(track) == [
        ("Shazam", "https://shazam/x"),
        ("Apple Music", "https://music.apple.com/x"),
        ("Spotify", "https://open.spotify.com/search/The%20Weeknd%20Blinding%20Lights"),
        (
            "YouTube",
            "https://www.youtube.com/results?search_query=The%20Weeknd%20Blinding%20Lights",
        ),
        (
            "Текст песни",
            "https://www.google.com/search?q=The%20Weeknd%20Blinding%20Lights%20"
            "%D1%82%D0%B5%D0%BA%D1%81%D1%82",
        ),
    ]


def test_buttons_without_links_are_hidden() -> None:
    labels = [label for label, _ in track_buttons(TrackResult(title="T", artist="A"))]
    assert labels == ["Spotify", "YouTube", "Текст песни"]


def test_search_query_special_characters_are_encoded() -> None:
    track = TrackResult(title="Rock & Roll?", artist="AC/DC")
    youtube = dict(track_buttons(track))["YouTube"]
    assert youtube.endswith("AC%2FDC%20Rock%20%26%20Roll%3F")


def test_direct_spotify_link_preferred_over_search() -> None:
    track = TrackResult(
        title="T", artist="A", links={"spotify": "https://open.spotify.com/track/1"}
    )
    assert dict(track_buttons(track))["Spotify"] == "https://open.spotify.com/track/1"
