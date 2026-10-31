"""Security regression tests: inputs must never make the server fetch non-YouTube URLs."""

import pytest

from tubekit_mcp import youtube
from tubekit_mcp.youtube import YouTubeError, playlist_id, video_id

HOSTILE_INPUTS = [
    "http://127.0.0.1:8080/",
    "http://169.254.169.254/metadata/instance",
    "http://10.0.0.5/admin?v=dQw4w9WgXcQ",
    "https://evil.example/watch?v=dQw4w9WgXcQ&list=PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH",
    "https://youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
    "https://evilyoutube.com/watch?v=dQw4w9WgXcQ",
    "file:///etc/passwd",
    "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
    "javascript:alert(1)",
    "",
]


@pytest.mark.parametrize("value", HOSTILE_INPUTS)
def test_non_youtube_inputs_are_rejected(value):
    assert video_id(value) is None
    assert playlist_id(value) is None


@pytest.mark.parametrize("value", HOSTILE_INPUTS)
def test_tools_refuse_hostile_inputs_before_any_network_call(value, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", no_network)
    with pytest.raises(YouTubeError):
        youtube.get_transcript(value)
    with pytest.raises(YouTubeError):
        youtube.search_transcript(value, ["x"])
    with pytest.raises(YouTubeError):
        youtube.list_playlist(value)


@pytest.mark.parametrize(
    "value",
    [
        "PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH",
        "https://www.youtube.com/playlist?list=PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH",
        "https://m.youtube.com/watch?v=17WoOqgXsRM&list=PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH",
    ],
)
def test_valid_playlist_forms(value):
    assert playlist_id(value) == "PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH"


def test_playlist_position_is_capped_before_fetching():
    with pytest.raises(YouTubeError, match="at most"):
        youtube.list_playlist("PLRqwX-V7Uu6ZiZxtDDRCi6uhfTH4FilpH", start=youtube.MAX_PLAYLIST_POSITION + 1)


def test_caption_urls_on_other_hosts_are_not_fetched(monkeypatch):
    info = {"id": "dQw4w9WgXcQ", "subtitles": {"en": [{"ext": "json3", "url": "http://10.0.0.5/captions"}]}}
    monkeypatch.setattr(youtube, "_extract_video_info", lambda url: info)
    with pytest.raises(YouTubeError, match="refusing"):
        youtube._load_transcript.__wrapped__("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "en", True)


def test_malformed_captions_raise_a_clean_error():
    with pytest.raises(YouTubeError):
        youtube._parse_json3("not json")
