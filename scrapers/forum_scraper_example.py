"""
Example forum parser (BeautifulSoup) for a public tech forum without an API.

IMPORTANT (business, not technical, decision): before pointing this at a real
forum, confirm scraping is allowed by that forum's robots.txt and Terms of
Service. This module is a skeleton showing the expected shape of a
"source-specific parser" -- do not enable it against a live site until that
check has been done by whoever owns the ReviewSource record.

Every new no-API source should follow this same pattern: a small module with
a fetch(product_name, ...) generator, kept separate from persistence (see
scrapers/dedup.py) and from Celery wiring (see scrapers/tasks.py).
"""

import logging
import random
import time

import requests
from bs4 import BeautifulSoup
from django.conf import settings

from .base import FetchedReview

logger = logging.getLogger(__name__)


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
    FetchedReview per thread post. This is a skeleton: selectors below are
    illustrative placeholders -- adapt them to the target forum's actual HTML
    once a specific, ToS-compliant forum has been chosen.

    parser_config is accepted (and ignored) only to keep a uniform call
    signature across forum parsers -- see scrapers/tasks.py::fetch_registry_reviews.
    If you need configurable selectors instead of hardcoding them like this
    example does, use scrapers/generic_forum_scraper.py.
    """
    search_url = f"{base_url.rstrip('/')}/search?q={product_name}"

    response = _get(search_url)
    if response is None:
        return
    _polite_sleep()

    soup = BeautifulSoup(response.text, "html.parser")
    thread_links = [a["href"] for a in soup.select("a.thread-link[href]")]

    for thread_url in thread_links:
        if not thread_url.startswith("http"):
            thread_url = f"{base_url.rstrip('/')}/{thread_url.lstrip('/')}"

        thread_response = _get(thread_url)
        _polite_sleep()
        if thread_response is None:
            continue

        thread_soup = BeautifulSoup(thread_response.text, "html.parser")
        for post in thread_soup.select("div.post"):
            body = post.select_one(".post-body")
            author = post.select_one(".post-author")
            post_id = post.get("id", "")
            if not body:
                continue

            yield FetchedReview(
                source_url=f"{thread_url}#{post_id}" if post_id else thread_url,
                raw_text=body.get_text(strip=True),
                author=author.get_text(strip=True) if author else "",
                published_at=None,
            )
