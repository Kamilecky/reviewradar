"""
Crawlbase "reddit-post" scraper (official third-party scraping API, called
via requests -- this module does no HTML parsing of its own).

Unlike every other parser in this package, this one does not search a
source by product name: it fetches one specific, already-known Reddit post
URL, handed to it via parser_config["url"] at call time (set by the caller,
e.g. scrapers/tasks.py::fetch_crawlbase_reddit_post -- not stored on
ReviewSource.parser_config, since the URL differs per request rather than
per source). See that task for the "don't re-fetch a URL fetched within the
last 24h" cache logic, which lives at the task level rather than here.

Comment extraction is deliberately NOT implemented: Crawlbase's reddit-post
response includes a `comments` array, but every real response seen so far
had it empty, so the actual comment object shape is unconfirmed. Add it once
that's verified -- don't guess field names.

Same fetch(product_name, base_url, parser_config=None) -> Iterable[FetchedReview]
contract as every other source parser (see scrapers/base.py), for registry
consistency -- product_name and base_url are both unused here.
"""

import logging
from datetime import datetime

import requests
from django.conf import settings

from .base import FetchedReview

logger = logging.getLogger(__name__)

CRAWLBASE_URL = "https://api.crawlbase.com/"


def _to_datetime(created_at) -> datetime | None:
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def fetch_reddit_post(url: str) -> dict | None:
    """
    Fetch a single Reddit post via Crawlbase's reddit-post scraper and
    return its `body.post` dict, or None if the request failed, the
    response wasn't usable, or required fields were missing.
    """
    token = settings.CRAWLBASE_TOKEN
    if not token:
        logger.warning("CRAWLBASE_TOKEN not set; skipping Crawlbase fetch.")
        return None

    try:
        response = requests.get(
            CRAWLBASE_URL,
            params={"token": token, "url": url, "scraper": "reddit-post"},
            timeout=30,  # Crawlbase scrapes the page live server-side; slower than a plain API call
        )
    except requests.Timeout:
        logger.warning("Crawlbase request timed out for %s", url)
        return None
    except requests.RequestException as exc:
        logger.warning("Crawlbase request failed for %s: %s", url, exc)
        return None

    if response.status_code != 200:
        logger.warning(
            "Crawlbase returned status=%s for %s", response.status_code, url
        )
        return None

    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("Crawlbase response was not valid JSON for %s: %s", url, exc)
        return None

    post = (data.get("body") or {}).get("post") or {}
    if not post.get("id") or not (post.get("permalink") or post.get("url")):
        logger.warning("Crawlbase response for %s is missing required post fields", url)
        return None

    return post


def fetch(product_name: str, base_url: str = "", parser_config: dict | None = None):
    """Yield one FetchedReview for the Reddit post at parser_config["url"]."""
    parser_config = parser_config or {}
    url = parser_config.get("url")
    if not url:
        logger.warning("crawlbase_reddit_scraper.fetch called without parser_config['url']")
        return

    post = fetch_reddit_post(url)
    if post is None:
        return

    raw_text = f"{post.get('title', '')}\n\n{post.get('selfText', '')}".strip()
    yield FetchedReview(
        source_url=post.get("url") or url,
        raw_text=raw_text,
        author=post.get("author", ""),
        published_at=_to_datetime(post.get("createdAt")),
    )
