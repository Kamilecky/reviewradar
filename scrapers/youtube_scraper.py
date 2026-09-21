"""
YouTube Data API v3 review source (official REST API, no HTML scraping).

Two-step per product: search.list for the top matching videos, then
commentThreads.list per video for top-level comments. Each comment becomes
one FetchedReview; source_url is the real "jump to comment" YouTube link
(video URL + ?lc=<comment_id>), which is naturally unique per comment, so
different comments under the same video never collide in dedup.py's
URL-hash dedup.

Same fetch(product_name, base_url, parser_config=None) -> Iterable[FetchedReview]
contract as every other source parser (see scrapers/base.py) -- base_url is
accepted for interface consistency but unused: the API base URL is fixed.

Recognized parser_config keys:
    max_videos: how many search results to pull comments from (default 3)
    max_comments_per_video: top-level comments per video (default 50)

A video with comments disabled (or any other per-video API error) is
skipped -- it does not abort fetching comments from the remaining videos.

IMPORTANT: this is an official API, not scraping -- no robots.txt/ToS check
needed, but the YouTube Data API has a daily quota; confirm expected usage
stays within it before enabling in production, and record that check on
ReviewSource.tos_notes for consistency with the scraping sources' audit
trail.
"""

import logging
from datetime import datetime

import requests
from django.conf import settings

from .base import FetchedReview

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
COMMENT_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"

DEFAULT_MAX_VIDEOS = 3
DEFAULT_MAX_COMMENTS_PER_VIDEO = 50


def _get_json(url: str, params: dict) -> dict | None:
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("YouTube API request failed for %s: %s", url, exc)
        return None


def _to_datetime(published_at) -> datetime | None:
    try:
        return datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _search_video_ids(product_name: str, api_key: str, max_videos: int) -> list[str]:
    data = _get_json(
        SEARCH_URL,
        {
            "part": "snippet",
            "q": product_name,
            "type": "video",
            "maxResults": max_videos,
            "key": api_key,
        },
    )
    if data is None:
        return []
    return [
        item["id"]["videoId"]
        for item in data.get("items", [])
        if item.get("id", {}).get("videoId")
    ]


def _fetch_comments(video_id: str, api_key: str, max_comments: int):
    data = _get_json(
        COMMENT_THREADS_URL,
        {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": max_comments,
            "textFormat": "plainText",
            "key": api_key,
        },
    )
    if data is None:
        return

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    for item in data.get("items", []):
        try:
            top_comment = item["snippet"]["topLevelComment"]
            snippet = top_comment["snippet"]
            comment_id = top_comment["id"]
        except (KeyError, TypeError):
            continue

        text = snippet.get("textDisplay", "")
        if not text:
            continue

        yield FetchedReview(
            source_url=f"{video_url}&lc={comment_id}",
            raw_text=text,
            author=snippet.get("authorDisplayName", ""),
            published_at=_to_datetime(snippet.get("publishedAt")),
        )


def fetch(product_name: str, base_url: str = "", parser_config: dict | None = None):
    """Yield one FetchedReview per top-level comment on videos matching product_name."""
    api_key = settings.YOUTUBE_API_KEY
    if not api_key:
        logger.warning("YOUTUBE_API_KEY not set; skipping YouTube fetch.")
        return

    parser_config = parser_config or {}
    max_videos = parser_config.get("max_videos", DEFAULT_MAX_VIDEOS)
    max_comments = parser_config.get("max_comments_per_video", DEFAULT_MAX_COMMENTS_PER_VIDEO)

    video_ids = _search_video_ids(product_name, api_key, max_videos)
    for video_id in video_ids:
        yield from _fetch_comments(video_id, api_key, max_comments)
