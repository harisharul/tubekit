"""yt-dlp wrappers for listing playlist videos and fetching video transcripts."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qs, urlparse

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


def video_id(video: str) -> str | None:
    """Extract the 11-character ID from a video ID or any common YouTube URL form."""
    video = video.strip()
    if _VIDEO_ID_RE.match(video):
        return video
    parsed = urlparse(video if "//" in video else f"https://{video}")
    if v := parse_qs(parsed.query).get("v"):
        return v[0] if _VIDEO_ID_RE.match(v[0]) else None
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.netloc.endswith("youtu.be") and parts:
        candidate = parts[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        candidate = parts[1]
    else:
        return None
    return candidate if _VIDEO_ID_RE.match(candidate) else None


def normalize_playlist_url(playlist: str) -> str:
    playlist = playlist.strip()
    if _PLAYLIST_ID_RE.match(playlist):
        return f"https://www.youtube.com/playlist?list={playlist}"
    return playlist


def format_timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_time(value: float | str | None) -> float | None:
    """Accept seconds (90, "90") or clock strings ("1:30", "01:02:03")."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        total = 0.0
        for part in value.strip().split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        raise YouTubeError(f"Invalid time '{value}'. Use seconds or mm:ss / hh:mm:ss.") from None


# --------------------------------------------------------------------------- playlists


def _fetch_playlist(playlist: str, items: str | None) -> dict[str, Any]:
    opts = _base_opts() | {"extract_flat": "in_playlist"}
    if items:
        # Only the requested range is fetched from YouTube, not the whole list.
        opts["playlist_items"] = items
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(normalize_playlist_url(playlist), download=False)
    except DownloadError as e:
        raise YouTubeError(str(e)) from e
    if not info or info.get("_type") != "playlist":
        raise YouTubeError("URL did not resolve to a playlist.")
    return info


def list_playlist(
    playlist: str, start: int = 1, end: int | None = None, title_filter: str | None = None
) -> dict[str, Any]:
    """Return playlist videos at positions start..end (1-based, inclusive).

    With title_filter, the whole playlist is scanned and start/end page through
    the matches instead, so only matching titles are returned to the model.
    """
    if end is not None and end < start:
        raise YouTubeError(f"end ({end}) must be >= start ({start}).")

    if title_filter:
        info = _fetch_playlist(playlist, None)
        words = title_filter.lower().split()
        entries = [
            (pos, e)
            for pos, e in enumerate(info.get("entries") or [], start=1)
            if e and all(w in (e.get("title") or "").lower() for w in words)
        ]
        matched = len(entries)
        entries = entries[start - 1 : end]
    else:
        info = _fetch_playlist(playlist, f"{start}-{end}" if end else f"{start}-")
        entries = [(pos, e) for pos, e in enumerate(info.get("entries") or [], start=start) if e]
        matched = None

    total = info.get("playlist_count")
    videos = [
        {
            "position": pos,
            "id": e.get("id"),
            "title": e.get("title"),
            "duration_seconds": e.get("duration"),
        }
        for pos, e in entries
    ]
    return {
        "playlist_id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "total_videos": total,
        "matched_videos": matched,
        "videos": videos,
    }


# --------------------------------------------------------------------------- transcripts


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
        segments.append({"start": event.get("tStartMs", 0) / 1000, "text": text})
    return segments


@lru_cache(maxsize=32)
def _load_transcript(url: str, language: str, prefer_manual: bool) -> dict[str, Any]:
    """Fetch and parse a caption track once; paging through it reuses this result.

    Cached results are shared between calls, so callers must not mutate them.
    Failures raise and are therefore never cached.
    """
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

    return {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration_seconds": info.get("duration"),
        "language": code,
        "auto_generated": is_auto,
        "chapters": [
            {"start": c.get("start_time"), "title": c.get("title")}
            for c in info.get("chapters") or []
        ],
        "segments": _parse_json3(raw),
    }


def get_transcript(
    video: str,
    language: str = "en",
    prefer_manual: bool = True,
    include_timestamps: bool = False,
    start_time: float | str | None = None,
    end_time: float | str | None = None,
    offset: int = 0,
    max_chars: int = 20000,
) -> dict[str, Any]:
    """Return one page of a transcript, bounded by a time window and a character budget.

    `offset` is a segment index within the time window; the result's `next_offset`
    continues where this page stopped.
    """
    vid = video_id(video)
    url = f"https://www.youtube.com/watch?v={vid}" if vid else video.strip()
    data = _load_transcript(url, language, prefer_manual)

    t0, t1 = parse_time(start_time), parse_time(end_time)
    window = [
        s
        for s in data["segments"]
        if (t0 is None or s["start"] >= t0) and (t1 is None or s["start"] < t1)
    ]
    if offset < 0 or (window and offset >= len(window)):
        raise YouTubeError(f"offset {offset} is out of range (0-{max(len(window) - 1, 0)}).")

    lines, used = [], 0
    for seg in window[offset:]:
        line = f"[{format_timestamp(seg['start'])}] {seg['text']}" if include_timestamps else seg["text"]
        if lines and used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    page = window[offset : offset + len(lines)]
    next_offset = offset + len(lines) if offset + len(lines) < len(window) else None

    return {
        **{k: data[k] for k in ("video_id", "title", "channel", "duration_seconds", "language", "auto_generated")},
        # Chapters let the model jump to a section with start_time/end_time; send once.
        "chapters": data["chapters"] if offset == 0 and t0 is None else [],
        "page_start": page[0]["start"] if page else None,
        "page_end": page[-1]["start"] if page else None,
        "next_offset": next_offset,
        "transcript": ("\n" if include_timestamps else " ").join(lines),
    }
