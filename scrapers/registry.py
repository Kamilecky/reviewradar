"""
Maps ReviewSource.parser_key -> a parser module's
fetch(product_name, base_url, parser_config=None) -> Iterable[FetchedReview].

Covers every source except Reddit, which is dispatched separately
(scrapers.tasks.queue_fetch, keyed off ReviewSource.source_type == REDDIT)
since it's a single API client rather than a per-source parser. Everything
else -- HTML-scraped forums (source_type=FORUM) and official-API review
sites (source_type=REVIEW_SITE, e.g. Google Places, YouTube) alike -- shares
one dispatch path (scrapers.tasks.fetch_registry_reviews) and this one
registry, because the fetch() contract is identical regardless of whether a
parser scrapes HTML or calls a JSON API.

Three ways to add a source:
  - same HTML engine as an existing forum: create a new ReviewSource row
    with parser_key="generic_forum" and CSS selectors in parser_config --
    no new Python file (see generic_forum_scraper.py).
  - a new official API (like google_places_scraper.py / youtube_scraper.py)
    or a forum with a genuinely different layout: write
    scrapers/<new>_scraper.py with fetch(product_name, base_url,
    parser_config=None), add it below, then create a ReviewSource row with
    matching parser_key.
Either way: no changes to models, Celery tasks, or other parsers required.
"""

from . import (
    beauty_forum_scraper,
    crawlbase_reddit_scraper,
    forum_scraper_example,
    generic_forum_scraper,
    google_places_scraper,
    youtube_scraper,
)

PARSER_REGISTRY = {
    "forum_example": forum_scraper_example.fetch,
    "generic_forum": generic_forum_scraper.fetch,
    "beauty_forum_example": beauty_forum_scraper.fetch,
    "google_places": google_places_scraper.fetch,
    "youtube": youtube_scraper.fetch,
    # Note: the URL-driven flow (scrapers.tasks.fetch_crawlbase_reddit_post)
    # calls crawlbase_reddit_scraper.fetch_reddit_post() directly rather
    # than resolving through this registry -- registered here mainly so
    # parser_key="crawlbase_reddit" is a recognized, discoverable choice on
    # ReviewSource, and so fetch_registry_reviews could also drive it for a
    # ReviewSource with a fixed url in parser_config.
    "crawlbase_reddit": crawlbase_reddit_scraper.fetch,
}


def get_parser(parser_key: str):
    try:
        return PARSER_REGISTRY[parser_key]
    except KeyError:
        raise ValueError(f"No parser registered for parser_key={parser_key!r}")
