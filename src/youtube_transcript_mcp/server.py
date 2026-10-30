"""MCP server exposing YouTube playlist listing and transcript fetching."""

from __future__ import annotations

from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import youtube

mcp = MCPServer(
    name="youtube-transcript",
    instructions=(
        "Use list_playlist_videos to see what videos a YouTube playlist contains, "
        "then get_video_transcript with a video ID or URL to read what is said in it."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@mcp.tool(annotations=READ_ONLY)
async def list_playlist_videos(
    playlist: Annotated[str, Field(description="Playlist URL or playlist ID (e.g. PLxxxx).")],
    limit: Annotated[
        int | None, Field(description="Maximum number of videos to return. Omit for all.", ge=1)
    ] = None,
) -> dict[str, Any]:
    """List the videos in a YouTube playlist (position, ID, title, URL, duration)."""
    try:
        return await anyio.to_thread.run_sync(youtube.list_playlist, playlist, limit)
    except youtube.YouTubeError as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=READ_ONLY)
async def get_video_transcript(
    video: Annotated[str, Field(description="Video URL or 11-character video ID.")],
    language: Annotated[
        str, Field(description="Caption language code, e.g. 'en', 'hi', 'ta'.")
    ] = "en",
    prefer_manual: Annotated[
        bool, Field(description="Prefer creator-uploaded captions over auto-generated ones.")
    ] = True,
    include_timestamps: Annotated[
        bool, Field(description="Prefix each caption line with its [mm:ss] start time.")
    ] = False,
) -> dict[str, Any]:
    """Fetch the transcript (captions) of a YouTube video."""
    try:
        return await anyio.to_thread.run_sync(
            youtube.get_transcript, video, language, prefer_manual, include_timestamps
        )
    except youtube.YouTubeError as e:
        raise ToolError(str(e)) from e


def main() -> None:
    mcp.run()
