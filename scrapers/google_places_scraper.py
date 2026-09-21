"""
Google Places API review source (official REST API, no HTML scraping).

Two-step lookup unless ReviewSource.parser_config already pins a place_id:
  1. Text Search (places/textsearch) for product_name -> take the top
     match's place_id.
  2. Place Details (places/details) with fields=name,reviews for that
     place_id.

Google's Place Details endpoint returns at most ~5 reviews (a limitation of
the API itself, not something this parser can work around) and does not
expose a stable per-review permalink -- source_url is synthesized from
place_id + review time + author so dedup.py's URL-hash dedup still works
per review.

Same fetch(product_name, base_url, parser_config=None) -> Iterable[FetchedReview]
contract as every other source parser (see scrapers/base.py) -- base_url is
accepted for interface consistency but unused: Google's API base URL is
fixed, not per-source.

Recognized parser_config keys:
    place_id: skip Text Search and use this place_id directly.

IMPORTANT: this is an official API, not scraping -- no robots.txt/ToS check
needed, but Google Places has request quotas and billing; confirm the
project's expected usage stays within budget before enabling in production,
and record that check on ReviewSource.tos_notes for consistency with the
scraping sources' audit trail.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from django.conf import settings

from .base import FetchedReview

logger = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


def _get_json(url: str, params: dict) -> dict | None:
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Google Places request failed for %s: %s", url, exc)
        return None


def _to_datetime(unix_time) -> datetime | None:
    if not unix_time:
        return None
    return datetime.fromtimestamp(unix_time, tz=timezone.utc)


def _resolve_place_id(product_name: str, api_key: str) -> str | None:
    data = _get_json(TEXT_SEARCH_URL, {"query": product_name, "key": api_key})
    if data is None:
        return None

    status = data.get("status")
    if status == "ZERO_RESULTS":
        logger.info("Google Places text search found no results for %r", product_name)
        return None
    if status != "OK":
        logger.warning(
            "Google Places text search returned status=%s for %r", status, product_name
        )
        return None

    results = data.get("results") or []
    return results[0]["place_id"] if results else None


def fetch(product_name: str, base_url: str = "", parser_config: dict | None = None):
    """
    Yield one FetchedReview per Google review attached to the place matching
    product_name (or parser_config["place_id"] if provided).
    """
    api_key = settings.GOOGLE_PLACES_API_KEY
    if not api_key:
        logger.warning("GOOGLE_PLACES_API_KEY not set; skipping Google Places fetch.")
        return

    parser_config = parser_config or {}
    place_id = parser_config.get("place_id") or _resolve_place_id(product_name, api_key)
    if not place_id:
        return

    data = _get_json(
        PLACE_DETAILS_URL, {"place_id": place_id, "fields": "name,reviews", "key": api_key}
    )
    if data is None:
        return

    status = data.get("status")
    if status != "OK":
        logger.warning(
            "Google Places details returned status=%s for place_id=%s", status, place_id
        )
        return

    reviews = (data.get("result") or {}).get("reviews") or []
    for review in reviews:
        text = review.get("text", "")
        if not text:
            continue

        author = review.get("author_name", "")
        review_time = review.get("time", "")
        yield FetchedReview(
            source_url=(
                f"https://www.google.com/maps/place/?q=place_id:{place_id}"
                f"&review_time={review_time}&review_author={quote(author)}"
            ),
            raw_text=text,
            author=author,
            published_at=_to_datetime(review_time),
        )
