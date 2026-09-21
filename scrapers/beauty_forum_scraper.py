"""
Example bespoke forum parser for a cosmetics/beauty discussion forum.

Demonstrates that a topic-specific parser (as opposed to the config-driven
generic_forum_scraper.py) is just another module following the same
fetch(product_name, base_url, ...) -> Iterable[FetchedReview] contract.
Onboarding it required no changes to models, dedup, or Celery wiring -- only
this file, one PARSER_REGISTRY entry in scrapers/registry.py, and a
ReviewSource row (source_type="forum", main_categories=["cosmetics"]).

Selectors below are illustrative placeholders for a beauty-forum-style
review layout (one review block per post, rather than a general "thread of
posts") -- adapt them to whichever real forum is chosen.

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
        logger.warning("Beauty forum request failed for %s: %s", url, exc)
        return None


def fetch(product_name: str, base_url: str, parser_config: dict | None = None):
    """
    Search base_url for reviews mentioning product_name and yield one
    FetchedReview per review block. parser_config is accepted (and ignored)
    only to keep a uniform call signature across forum parsers -- this
    parser's selectors are hardcoded like forum_scraper_example.py's.
    """
    search_url = f"{base_url.rstrip('/')}/reviews/search?product={product_name}"

    response = _get(search_url)
    if response is None:
        return
    _polite_sleep()

    soup = BeautifulSoup(response.text, "html.parser")
    for review in soup.select("div.review-entry"):
        body = review.select_one(".review-text")
        author = review.select_one(".review-author")
        review_id = review.get("id", "")
        if not body:
            continue

        yield FetchedReview(
            source_url=f"{search_url}#{review_id}" if review_id else search_url,
            raw_text=body.get_text(strip=True),
            author=author.get_text(strip=True) if author else "",
            published_at=None,
        )
