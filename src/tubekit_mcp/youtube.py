"""yt-dlp wrappers for listing playlist videos and fetching video transcripts."""

from __future__ import annotations

import json
import os
import re
import threading
from bisect import bisect_right
from contextlib import contextmanager
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
_PLAYLIST_ID_RE = re.compile(r"^(PL|UU|LL|FL|RD|OL)[A-Za-z0-9_-]{8,62}$")
_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

# Abuse limits. Inputs are always reduced to a YouTube ID and rebuilt as a
# canonical youtube.com URL, so tools can never be pointed at other hosts (SSRF).
MAX_PLAYLIST_POSITION = 5000  # yt-dlp pages through everything before `start`
MAX_FILTER_SCAN = 2000  # videos scanned for title_filter
MAX_CAPTION_BYTES = 5 * 1024 * 1024  # ~10 hours of captions
SOCKET_TIMEOUT = 20
# Concurrent YouTube fetches; beyond this, requests wait briefly, then fail.
_fetch_slots = threading.BoundedSemaphore(int(os.environ.get("TUBEKIT_MAX_CONCURRENCY", "4")))


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
        "socket_timeout": SOCKET_TIMEOUT,
        # Defense in depth against SSRF: never fall back to the generic extractor.
        "allowed_extractors": ["youtube", "youtube:tab", "youtube:playlist"],
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


@contextmanager
def _youtube_slot():
    if not _fetch_slots.acquire(timeout=30):
        raise YouTubeError("Server is busy; retry in a minute.")
    try:
        yield
    finally:
        _fetch_slots.release()


def _is_youtube_host(host: str | None) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in _YOUTUBE_HOSTS)


def _parse_youtube_url(value: str):
    parsed = urlparse(value if "//" in value else f"https://{value}")
    if parsed.scheme in ("http", "https") and _is_youtube_host(parsed.hostname):
        return parsed
    return None


def video_id(video: str) -> str | None:
    """Extract the 11-character ID from a video ID or a YouTube URL; None otherwise."""
    video = video.strip()
    if _VIDEO_ID_RE.match(video):
        return video
    if not (parsed := _parse_youtube_url(video)):
        return None
    if v := parse_qs(parsed.query).get("v"):
        return v[0] if _VIDEO_ID_RE.match(v[0]) else None
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.hostname.lower().endswith("youtu.be") and parts:
        candidate = parts[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
        candidate = parts[1]
    else:
        return None
    return candidate if _VIDEO_ID_RE.match(candidate) else None


def video_url(video: str) -> str:
    if not (vid := video_id(video)):
        raise YouTubeError("Not a YouTube video URL or 11-character video ID.")
    return f"https://www.youtube.com/watch?v={vid}"


def playlist_id(playlist: str) -> str | None:
    """Extract a playlist ID from an ID or a YouTube URL with a list= parameter."""
    playlist = playlist.strip()
    if _PLAYLIST_ID_RE.match(playlist):
        return playlist
    if (parsed := _parse_youtube_url(playlist)) and (lst := parse_qs(parsed.query).get("list")):
        return lst[0] if _PLAYLIST_ID_RE.match(lst[0]) else None
    return None


def playlist_url(playlist: str) -> str:
    if not (pid := playlist_id(playlist)):
        raise YouTubeError("Not a YouTube playlist URL or playlist ID.")
    return f"https://www.youtube.com/playlist?list={pid}"


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


def _fetch_playlist(playlist: str, items: str) -> dict[str, Any]:
    url = playlist_url(playlist)
    # Only the requested range is fetched from YouTube, not the whole list.
    opts = _base_opts() | {"extract_flat": "in_playlist", "playlist_items": items}
    try:
        with _youtube_slot(), yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as e:
        raise YouTubeError(str(e)) from e
    if not info or info.get("_type") != "playlist":
        raise YouTubeError("URL did not resolve to a playlist.")
    return info


def list_playlist(
    playlist: str, start: int = 1, end: int | None = None, title_filter: str | None = None
) -> dict[str, Any]:
    """Return playlist videos at positions start..end (1-based, inclusive).

    With title_filter, the first MAX_FILTER_SCAN videos are scanned and start/end
    page through the matches instead, so only matching titles reach the model.
    """
    if end is not None and end < start:
        raise YouTubeError(f"end ({end}) must be >= start ({start}).")
    if start > MAX_PLAYLIST_POSITION:
        raise YouTubeError(f"start can be at most {MAX_PLAYLIST_POSITION}.")
    end = min(end, MAX_PLAYLIST_POSITION) if end else MAX_PLAYLIST_POSITION

    scanned = None
    if title_filter:
        info = _fetch_playlist(playlist, f"1-{MAX_FILTER_SCAN}")
        scanned = len(info.get("entries") or [])
        words = title_filter.lower().split()
        entries = [
            (pos, e)
            for pos, e in enumerate(info.get("entries") or [], start=1)
            if e and all(w in (e.get("title") or "").lower() for w in words)
        ]
        matched = len(entries)
        entries = entries[start - 1 : end]
    else:
        info = _fetch_playlist(playlist, f"{start}-{end}")
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
        "scanned_videos": scanned,
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
    try:
        events = json.loads(raw).get("events", [])
    except (ValueError, AttributeError):
        raise YouTubeError("Could not parse the caption track.") from None
    segments = []
    for event in events:
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or [])
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        segments.append({"start": event.get("tStartMs", 0) / 1000, "text": text})
    return segments


@lru_cache(maxsize=16)
def _load_transcript(url: str, language: str, prefer_manual: bool) -> dict[str, Any]:
    """Fetch and parse a caption track once; paging through it reuses this result.

    Cached results are shared between calls, so callers must not mutate them.
    Failures raise and are therefore never cached.
    """
    with _youtube_slot():
        info = _extract_video_info(url)
        code, is_auto, formats = _pick_track(info, language, prefer_manual)

        fmt = next((f for f in formats if f.get("ext") == "json3"), None)
        if fmt is None:
            raise YouTubeError(f"No json3 caption format available for '{code}'.")
        caption = urlparse(fmt["url"])
        if caption.scheme != "https" or not _is_youtube_host(caption.hostname):
            raise YouTubeError("Unexpected caption URL host; refusing to fetch it.")

        try:
            with yt_dlp.YoutubeDL(_base_opts()) as ydl:
                body = ydl.urlopen(fmt["url"]).read(MAX_CAPTION_BYTES + 1)
        except Exception as e:  # network / HTTP errors from yt-dlp's request layer
            msg = f"Failed to download captions: {e}"
            if "429" in str(e):
                msg += (
                    ". YouTube is rate-limiting caption requests (common for auto-translated"
                    " tracks); retry later or try the original language."
                )
            raise YouTubeError(msg) from e

    if len(body) > MAX_CAPTION_BYTES:
        raise YouTubeError("Caption track is too large to process.")
    raw = body.decode("utf-8", errors="replace")

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


def _load_for(video: str, language: str, prefer_manual: bool) -> dict[str, Any]:
    return _load_transcript(video_url(video), language, prefer_manual)


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
    data = _load_for(video, language, prefer_manual)

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


# --------------------------------------------------------------------------- search

# \w misses Unicode combining marks, which Indic scripts (Devanagari, Tamil, ...)
# use for vowel signs; count them as word characters so matches stay whole-word.
_WORD = r"[\ẁ-ͯऀ-෿]"
_SEP = r"[^\ẁ-ͯऀ-෿]+"
# Keywords containing other symbols (C++, C#, .NET) are matched literally.
_SYMBOLS_RE = re.compile(r"[^\w\s̀-ͯऀ-෿'\-]")


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Case-insensitive pattern matching a keyword at word starts.

    Each word may take a suffix ("function" matches "functions") and the words
    of a phrase may be separated by any spaces or punctuation ("cross entropy"
    matches "cross-entropy"). Words must start at a word boundary, so "loss"
    does not match "glossary".
    """
    keyword = keyword.strip()
    if _SYMBOLS_RE.search(keyword):
        body = re.escape(keyword)
    else:
        words = re.findall(f"{_WORD}+", keyword)
        if not words:
            raise YouTubeError(f"Keyword '{keyword}' has no searchable characters.")
        body = _SEP.join(re.escape(w) + f"{_WORD}*" for w in words)
    return re.compile(f"(?<!{_WORD}){body}", re.IGNORECASE)


def _snippet(text: str, start: int, end: int, before: int = 50, after: int = 80) -> str:
    lo, hi = max(0, start - before), min(len(text), end + after)
    # Trim to whole words at both edges.
    if lo > 0 and (cut := text.find(" ", lo, start)) != -1:
        lo = cut + 1
    if hi < len(text) and (cut := text.rfind(" ", end, hi)) != -1:
        hi = cut
    return ("..." if lo > 0 else "") + text[lo:hi].strip() + ("..." if hi < len(text) else "")


def search_segments(
    segments: list[dict[str, Any]],
    keywords: list[str],
    chapters: list[dict[str, Any]] | None = None,
    duration: float | None = None,
    max_results: int = 10,
    merge_gap: float = 30.0,
) -> dict[str, Any]:
    """Find where keywords are spoken and group nearby hits into time ranges.

    Segments are joined into one string before matching so a phrase split
    across two caption lines is still found. Ranges are ranked by how many
    different keywords they contain, then by total hits; the best
    `max_results` are returned in time order.
    """
    # Keywords that compile to the same pattern ("speech to text" and
    # "speech-to-text") would double-count every hit; keep the first spelling.
    patterns: dict[str, re.Pattern[str]] = {}
    for k in (k.strip() for k in keywords):
        if k and (p := keyword_pattern(k)).pattern.lower() not in {q.pattern.lower() for q in patterns.values()}:
            patterns[k] = p
    if not patterns:
        raise YouTubeError("Provide at least one non-empty keyword.")
    keywords = list(patterns)

    offsets, pieces, pos = [], [], 0
    for seg in segments:
        offsets.append(pos)
        pieces.append(seg["text"])
        pos += len(seg["text"]) + 1
    text = " ".join(pieces)

    def seg_end(i: int) -> float:
        if i + 1 < len(segments):
            return segments[i + 1]["start"]
        return max(segments[i]["start"] + 5, duration or 0)

    hits = []  # (segment index, keyword, match start, match end)
    for kw, pattern in patterns.items():
        for m in pattern.finditer(text):
            hits.append((bisect_right(offsets, m.start()) - 1, kw, m.start(), m.end()))
    hits.sort(key=lambda h: (h[0], h[2]))

    ranges: list[dict[str, Any]] = []
    for idx, kw, m_start, m_end in hits:
        t = segments[idx]["start"]
        current = ranges[-1] if ranges else None
        if current and t - current["last_hit"] <= merge_gap:
            current["end"] = max(current["end"], seg_end(idx))
            current["last_hit"] = t
        else:
            current = {"start": t, "end": seg_end(idx), "last_hit": t, "counts": {}, "span": (m_start, m_end)}
            ranges.append(current)
        current["counts"][kw] = current["counts"].get(kw, 0) + 1

    for r in ranges:
        r["snippet"] = _snippet(text, *r.pop("span"))
        del r["last_hit"]

    ranked = sorted(ranges, key=lambda r: (len(r["counts"]), sum(r["counts"].values())), reverse=True)
    best = sorted(ranked[:max_results], key=lambda r: r["start"])

    keyword_counts = {k: 0 for k in keywords}
    for _, kw, _, _ in hits:
        keyword_counts[kw] += 1

    return {
        "keyword_counts": keyword_counts,
        "chapter_matches": [
            c for c in chapters or [] if any(p.search(c.get("title") or "") for p in patterns.values())
        ],
        "total_ranges": len(ranges),
        "top_range": ranked[0] if ranked else None,
        "ranges": best,
    }


def search_transcript(
    video: str,
    keywords: list[str],
    language: str = "en",
    prefer_manual: bool = True,
    max_results: int = 10,
) -> dict[str, Any]:
    data = _load_for(video, language, prefer_manual)
    result = search_segments(
        data["segments"], keywords, data["chapters"], data["duration_seconds"], max_results
    )
    meta_keys = ("video_id", "title", "channel", "duration_seconds", "language", "auto_generated")
    return {k: data[k] for k in meta_keys} | result
