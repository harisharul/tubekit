"""yt-dlp wrappers for listing playlist videos and fetching video transcripts."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError

# Player clients tried in order when the previous one returns no caption tracks.
# YouTube often blocks the default clients ("Sign in to confirm you're not a bot")
# from datacenter IPs while still serving captions to the embedded web player.
CAPTION_CLIENT_FALLBACKS: list[list[str] | None] = [None, ["web_embedded"], ["tv_simply"], ["mweb"]]

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_ID_RE = re.compile(r"^(PL|UU|LL|FL|RD|OL)[A-Za-z0-9_-]+$")


class YouTubeError(Exception):
    """Raised for user-facing failures (bad input, no captions, blocked requests)."""


def _base_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noprogress": True,
        # stdout is the MCP stdio channel; never let yt-dlp write to it.
        "logtostderr": True,
    }
    if cookie_file := os.environ.get("YT_COOKIES_FILE"):
        opts["cookiefile"] = cookie_file
    if browser := os.environ.get("YT_COOKIES_FROM_BROWSER"):
        # e.g. "chrome", "firefox", "edge:Profile 1"
        name, _, profile = browser.partition(":")
        opts["cookiesfrombrowser"] = (name, profile or None, None, None)
    if proxy := os.environ.get("YT_PROXY"):
        opts["proxy"] = proxy
    return opts


def normalize_video_url(video: str) -> str:
    video = video.strip()
    if _VIDEO_ID_RE.match(video):
        return f"https://www.youtube.com/watch?v={video}"
    return video


def normalize_playlist_url(playlist: str) -> str:
    playlist = playlist.strip()
    if _PLAYLIST_ID_RE.match(playlist):
        return f"https://www.youtube.com/playlist?list={playlist}"
    return playlist


def list_playlist(playlist: str, limit: int | None = None) -> dict[str, Any]:
    opts = _base_opts() | {"extract_flat": "in_playlist"}
    if limit:
        opts["playlistend"] = limit
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(normalize_playlist_url(playlist), download=False)
    except DownloadError as e:
        raise YouTubeError(str(e)) from e

    if not info or info.get("_type") != "playlist":
        raise YouTubeError("URL did not resolve to a playlist.")

    videos = []
    for i, entry in enumerate(info.get("entries") or [], start=1):
        if not entry:
            continue
        videos.append(
            {
                "position": i,
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id')}",
                "duration_seconds": entry.get("duration"),
                "channel": entry.get("channel") or entry.get("uploader"),
            }
        )

    return {
        "playlist_id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "total_videos": info.get("playlist_count") or len(videos),
        "returned_videos": len(videos),
        "videos": videos,
    }


def _extract_video_info(url: str) -> dict[str, Any]:
    """Extract metadata, retrying with alternate player clients until captions appear."""
    last_error: Exception | None = None
    info: dict[str, Any] | None = None
    for clients in CAPTION_CLIENT_FALLBACKS:
        opts = _base_opts() | {"ignore_no_formats_error": True}
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as e:
            last_error = e
            continue
        if info and (info.get("subtitles") or info.get("automatic_captions")):
            return info
    if info:
        return info
    raise YouTubeError(str(last_error) if last_error else "Could not extract video info.")


def _pick_track(
    info: dict[str, Any], language: str, prefer_manual: bool
) -> tuple[str, bool, list[dict[str, Any]]]:
    """Return (language_code, is_auto_generated, formats) for the best caption track."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    # Live chat replays show up as a "subtitle" but aren't a transcript.
    manual = {k: v for k, v in manual.items() if k != "live_chat"}
    sources = [(manual, False), (auto, True)]
    if not prefer_manual:
        sources.reverse()

    def matches(code: str) -> bool:
        return code == language or code.split("-")[0] == language.split("-")[0]

    for tracks, is_auto in sources:
        if language in tracks:
            return language, is_auto, tracks[language]
        # Auto captions include machine translations like "en-ta"; prefer the
        # "-orig" track, then any regional variant of the language.
        for code in sorted(tracks, key=lambda c: (not c.endswith("-orig"), len(c))):
            if matches(code.removesuffix("-orig")):
                return code, is_auto, tracks[code]

    available = sorted(set(manual) | {c for c in auto if "-" not in c or c.endswith("-orig")})
    raise YouTubeError(
        f"No '{language}' captions for this video. Available languages: "
        + (", ".join(available) if available else "none")
    )


def _parse_json3(raw: str) -> list[dict[str, Any]]:
    segments = []
    for event in json.loads(raw).get("events", []):
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or [])
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(event.get("tStartMs", 0) / 1000, 2),
                "duration": round(event.get("dDurationMs", 0) / 1000, 2),
                "text": text,
            }
        )
    return segments


def _format_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def get_transcript(
    video: str,
    language: str = "en",
    prefer_manual: bool = True,
    include_timestamps: bool = False,
) -> dict[str, Any]:
    url = normalize_video_url(video)
    info = _extract_video_info(url)
    code, is_auto, formats = _pick_track(info, language, prefer_manual)

    fmt = next((f for f in formats if f.get("ext") == "json3"), None)
    if fmt is None:
        raise YouTubeError(f"No json3 caption format available for '{code}'.")

    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            raw = ydl.urlopen(fmt["url"]).read().decode("utf-8")
    except Exception as e:  # network / HTTP errors from yt-dlp's request layer
        msg = f"Failed to download captions: {e}"
        if "429" in str(e):
            msg += (
                ". YouTube is rate-limiting caption requests (common for auto-translated"
                " tracks); retry later, try the original language, or set YT_COOKIES_FILE."
            )
        raise YouTubeError(msg) from e

    segments = _parse_json3(raw)
    if include_timestamps:
        transcript = "\n".join(f"[{_format_timestamp(s['start'])}] {s['text']}" for s in segments)
    else:
        transcript = " ".join(s["text"] for s in segments)

    return {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration_seconds": info.get("duration"),
        "language": code,
        "auto_generated": is_auto,
        "transcript": transcript,
    }
