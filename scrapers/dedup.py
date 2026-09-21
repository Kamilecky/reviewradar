"""
Shared persistence layer for scraper output. Every parser's results flow
through save_reviews() so deduplication logic lives in exactly one place.
"""

from typing import Iterable

from reviews.models import Review, ReviewSource

from .base import FetchedReview


def save_reviews(
    product,
    source: ReviewSource,
    fetched_reviews: Iterable[FetchedReview],
) -> int:
    """
    Persist fetched reviews for a product/source, skipping ones already stored
    (matched by sha256 hash of source_url). Returns the count of newly created reviews.
    """
    created_count = 0
    for item in fetched_reviews:
        url_hash = Review.hash_url(item.source_url)
        if Review.objects.filter(source_url_hash=url_hash).exists():
            continue

        Review.objects.create(
            product=product,
            source=source,
            author=item.author,
            raw_text=item.raw_text,
            source_url=item.source_url,
            source_url_hash=url_hash,
            published_at=item.published_at,
        )
        created_count += 1

    return created_count
