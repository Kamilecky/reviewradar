"""
Generic, configuration-driven forum parser (BeautifulSoup).

Unlike forum_scraper_example.py (hardcoded selectors for one illustrative
forum), this parser reads its CSS selectors from ReviewSource.parser_config.
That means a new forum running the same engine (e.g. another phpBB/vBulletin/
Discourse installation) can be onboarded as one ReviewSource row with the
right parser_config -- no new Python file, no changes to models, dedup, or
Celery wiring.

Recognized parser_config keys (all optional -- defaults below match
forum_scraper_example.py's layout):
    thread_link_selector: CSS selector for search-result thread links
    post_selector:        CSS selector for one post within a thread
    body_selector:         CSS selector for a post's text body
    author_selector:       CSS selector for a post's author
    date_selector:         CSS selector for a post's date (stored as-is;
                            format varies too much per forum to parse reliably)

IMPORTANT (business, not technical, decision): as with every scraper in this
project, confirm robots.txt/ToS for the actual target forum before enabling
it, and record the check on ReviewSource.tos_checked_at / tos_notes.
"""

import logging
import random
import time

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from .base import FetchedReview

logger = logging.getLogger(__name__)

DEFAULT_SELECTORS = {
    "thread_link_selector": "a.thread-link[href]",
    "post_selector": "div.post",
    "body_selector": ".post-body",
    "author_selector": ".post-author",
    "date_selector": ".post-date",
}


def _polite_sleep():
    """Rate-limit requests to the forum: fixed delay + random jitter."""
    delay = random.uniform(
        settings.SCRAPER_MIN_DELAY_SECONDS, settings.SCRAPER_MAX_DELAY_SECONDS
    )
    time.sleep(delay)


def _get(url: str) -> requests.Response | None:
    headers = {"User-Agent": settings.SCRAPER_USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        logger.warning("Forum request failed for %s: %s", url, exc)
        return None


def fetch(product_name: str, base_url: str, parser_config: dict | None = None):
    """
    Search base_url for threads mentioning product_name and yield one
    FetchedReview per thread post, using CSS selectors from parser_config
    (falling back to DEFAULT_SELECTORS for any key not provided).
    """
    selectors = {**DEFAULT_SELECTORS, **(parser_config or {})}

    search_url = f"{base_url.rstrip('/')}/search?q={product_name}"
    response = _get(search_url)
    if response is None:
        return
    _polite_sleep()

    soup = BeautifulSoup(response.text, "html.parser")
    thread_links = [a["href"] for a in soup.select(selectors["thread_link_selector"])]

    for thread_url in thread_links:
        if not thread_url.startswith("http"):
            thread_url = f"{base_url.rstrip('/')}/{thread_url.lstrip('/')}"

        thread_response = _get(thread_url)
        _polite_sleep()
        if thread_response is None:
            continue

        thread_soup = BeautifulSoup(thread_response.text, "html.parser")
        for post in thread_soup.select(selectors["post_selector"]):
            body = post.select_one(selectors["body_selector"])
            author = post.select_one(selectors["author_selector"])
            post_id = post.get("id", "")
            if not body:
                continue

            yield FetchedReview(
                source_url=f"{thread_url}#{post_id}" if post_id else thread_url,
                raw_text=body.get_text(strip=True),
                author=author.get_text(strip=True) if author else "",
                published_at=None,
            )
