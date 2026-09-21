"""
Common interface every review source parser must implement.

A parser's only job is to fetch raw items from its source and yield them as
FetchedReview objects. It must NOT touch the database directly — persistence
(deduplication, Review.objects.create(...), ScrapeJob bookkeeping) is handled
uniformly by scrapers/tasks.py + scrapers/dedup.py. This split is what lets a
new source be added as "one parser file + one ReviewSource row" without
touching models, Celery wiring, or any other parser.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


@dataclass
class FetchedReview:
    source_url: str
    raw_text: str
    author: str = ""
    published_at: datetime | None = None


class ReviewParser(Protocol):
    def fetch(self, product_name: str, **kwargs) -> Iterable[FetchedReview]:
        """
        Yield FetchedReview items matching product_name from this source.

        Forum parsers additionally accept a `parser_config` kwarg (the
        ReviewSource.parser_config dict, e.g. CSS selectors) so a new forum on
        the same engine can be onboarded via config alone -- see
        scrapers/generic_forum_scraper.py.
        """
        ...
