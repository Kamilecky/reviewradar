import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from analysis.models import APIUsageLog
from analysis.usage import track_api_usage
from products.models import Product
from reviews.models import Review, ReviewSource, ScrapeJob

from . import reddit_scraper
from .crawlbase_reddit_scraper import fetch_reddit_post
from .dedup import save_reviews
from .registry import get_parser

CRAWLBASE_FRESHNESS_WINDOW = timedelta(hours=24)

# Maps parser_key -> APIUsageLog provider for sources with real quotas/costs.
# Forum scrapers (forum_example, generic_forum, beauty_forum_example) are
# HTML scraping, not a metered API, so they're deliberately absent here --
# no usage row is logged for them.
PARSER_KEY_TO_PROVIDER = {
    "youtube": APIUsageLog.Provider.YOUTUBE,
    "google_places": APIUsageLog.Provider.GOOGLE_PLACES,
    "crawlbase_reddit": APIUsageLog.Provider.CRAWLBASE,
}

# parser_keys eligible to be triggered live from the web search UI (see
# queue_live_search_fetch). Deliberately excludes reddit/forum sources (kept
# behind the explicit refresh-reviews action, to avoid firing on every
# search) and crawlbase_reddit (needs one specific post URL, not a
# product-name search -- see ProductViewSet.fetch_crawlbase_reddit_post).
LIVE_SEARCH_PARSER_KEYS = {"google_places", "youtube"}

logger = logging.getLogger(__name__)


def queue_fetch(source: ReviewSource, product: Product) -> None:
    """
    Dispatch the right Celery task for a source, keyed off source_type.
    Reddit has its own dedicated client/task; every other source_type
    (FORUM, REVIEW_SITE, OTHER) shares fetch_registry_reviews since they all
    resolve their parser through the same registry.
    """
    if source.source_type == ReviewSource.SourceType.REDDIT:
        fetch_reddit_reviews.delay(product.id, source.id)
    else:
        fetch_registry_reviews.delay(product.id, source.id)


def queue_live_search_fetch(product: Product) -> None:
    """
    Triggered from the web search UI (products/web_views.py::product_search)
    so YouTube/Google Places results feel current the moment someone
    searches for a product, instead of only refreshing via the explicit
    refresh-reviews action. Only queues sources whose parser_key is in
    LIVE_SEARCH_PARSER_KEYS and that apply to the product's category.

    Failures to enqueue (e.g. the Celery broker is unreachable) are logged
    and swallowed rather than raised: unlike refresh-reviews (an explicit
    user action where a failure should be visible), this fires passively on
    every plain search, so a broker hiccup must never break the search page.
    """
    sources = ReviewSource.objects.filter(
        parser_key__in=LIVE_SEARCH_PARSER_KEYS, is_active=True
    )
    for source in sources:
        if not source.applies_to(product.category.main_category):
            continue
        try:
            queue_fetch(source, product)
        except Exception:  # noqa: BLE001 - best-effort background trigger, must not break search
            logger.exception(
                "queue_live_search_fetch failed to enqueue source_id=%s for product_id=%s",
                source.id,
                product.id,
            )


@shared_task
def fetch_reddit_reviews(product_id: int, source_id: int):
    product = Product.objects.get(id=product_id)
    source = ReviewSource.objects.get(id=source_id, is_active=True)
    subreddits = source.parser_config.get("subreddits") or None  # None -> reddit_scraper's own default

    job = ScrapeJob.objects.create(product=product, source=source, status=ScrapeJob.Status.RUNNING)
    try:
        fetched = reddit_scraper.fetch(f"{product.brand} {product.model_name}", subreddits=subreddits)
        # Wraps save_reviews (which consumes the fetch() generator), not
        # fetch() itself -- fetch() is lazy, so the real HTTP calls happen
        # during iteration, not at generator-creation time.
        with track_api_usage(APIUsageLog.Provider.REDDIT, "fetch_reviews", product_id=product.id):
            created_count = save_reviews(product, source, fetched)
    except Exception as exc:  # noqa: BLE001 - task must record failure, not just crash
        logger.exception("fetch_reddit_reviews failed for product_id=%s", product_id)
        job.status = ScrapeJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        return

    job.status = ScrapeJob.Status.SUCCESS
    job.reviews_found = created_count
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "reviews_found", "finished_at"])

    if created_count:
        from analysis.tasks import analyze_product_reviews

        analyze_product_reviews.delay(product_id)


@shared_task
def fetch_registry_reviews(product_id: int, source_id: int):
    """Fetch task for any source resolved through scrapers.registry (forums and official-API review sites alike)."""
    product = Product.objects.get(id=product_id)
    source = ReviewSource.objects.get(id=source_id, is_active=True)

    job = ScrapeJob.objects.create(product=product, source=source, status=ScrapeJob.Status.RUNNING)
    try:
        parser_fetch = get_parser(source.parser_key)
        fetched = parser_fetch(
            f"{product.brand} {product.model_name}",
            base_url=source.base_url,
            parser_config=source.parser_config,
        )
        provider = PARSER_KEY_TO_PROVIDER.get(source.parser_key)
        if provider:
            # Wraps save_reviews (which consumes the fetch() generator), not
            # fetch() itself -- see fetch_reddit_reviews for why.
            with track_api_usage(provider, "fetch_reviews", product_id=product.id):
                created_count = save_reviews(product, source, fetched)
        else:
            created_count = save_reviews(product, source, fetched)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "fetch_registry_reviews failed for product_id=%s source_id=%s", product_id, source_id
        )
        job.status = ScrapeJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        return

    job.status = ScrapeJob.Status.SUCCESS
    job.reviews_found = created_count
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "reviews_found", "finished_at"])

    if created_count:
        from analysis.tasks import analyze_product_reviews

        analyze_product_reviews.delay(product_id)


@shared_task
def refresh_recent_products():
    """
    Celery Beat entry point: re-fetch reviews for every product added within
    settings.REVIEW_REFRESH_WINDOW_DAYS, across every active review source
    that applies to that product's category.
    """
    cutoff = timezone.now() - timedelta(days=settings.REVIEW_REFRESH_WINDOW_DAYS)
    recent_products = Product.objects.select_related("category").filter(created_at__gte=cutoff)
    active_sources = list(ReviewSource.objects.filter(is_active=True))

    for product in recent_products:
        for source in active_sources:
            if source.applies_to(product.category.main_category):
                queue_fetch(source, product)


@shared_task
def fetch_crawlbase_reddit_post(product_id: int, source_id: int, url: str):
    """
    Fetch one specific, already-known Reddit post URL via Crawlbase and
    upsert it as a Review. Unlike every other fetch task, this isn't part of
    queue_fetch()'s category-scoped fan-out -- it's triggered directly (see
    products/views.py::ProductViewSet.fetch_crawlbase_reddit_post) with an
    explicit URL, and it deliberately bypasses dedup.save_reviews: that
    helper only ever inserts-if-new, whereas this task needs to *refresh* a
    URL it already has once it's stale (see CRAWLBASE_FRESHNESS_WINDOW).

    Calls crawlbase_reddit_scraper.fetch_reddit_post() directly rather than
    going through scrapers.registry.get_parser() -- the registry's fetch()
    wrapper only returns a FetchedReview (raw_text/author/date), not the
    score/raw post payload this task also needs, and calling both would
    mean two Crawlbase requests for one job.
    """
    product = Product.objects.get(id=product_id)
    source = ReviewSource.objects.get(id=source_id, is_active=True)
    url_hash = Review.hash_url(url)

    existing = Review.objects.filter(source_url_hash=url_hash).first()
    if existing and timezone.now() - existing.fetched_at < CRAWLBASE_FRESHNESS_WINDOW:
        ScrapeJob.objects.create(
            product=product,
            source=source,
            status=ScrapeJob.Status.SUCCESS,
            reviews_found=0,
            finished_at=timezone.now(),
            error_message="Skipped: cached Review is still fresh (< 24h old).",
        )
        return

    job = ScrapeJob.objects.create(product=product, source=source, status=ScrapeJob.Status.RUNNING)
    try:
        with track_api_usage(APIUsageLog.Provider.CRAWLBASE, "fetch_reddit_post", product_id=product.id):
            post = fetch_reddit_post(url)
    except Exception as exc:  # noqa: BLE001 - task must record failure, not just crash
        logger.exception("fetch_crawlbase_reddit_post failed for url=%s", url)
        job.status = ScrapeJob.Status.FAILED
        job.error_message = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        return

    if post is None:
        job.status = ScrapeJob.Status.SUCCESS
        job.reviews_found = 0
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "reviews_found", "finished_at"])
        return

    raw_text = f"{post.get('title', '')}\n\n{post.get('selfText', '')}".strip()
    Review.objects.update_or_create(
        source_url_hash=url_hash,
        defaults={
            "product": product,
            "source": source,
            "author": post.get("author", ""),
            "raw_text": raw_text,
            "source_url": post.get("url") or url,
            "score": post.get("score"),
            "raw_data": post,
            # Reset so analyze_product_reviews (which only classifies
            # sentiment__isnull=True) reclassifies refreshed content instead
            # of keeping a sentiment computed from the pre-refresh text.
            "sentiment": None,
            # auto_now_add only fires on INSERT, so on an update (refreshing
            # a stale Review) this must be set explicitly or fetched_at
            # would never advance and every future run would see it as
            # stale again.
            "fetched_at": timezone.now(),
        },
    )

    job.status = ScrapeJob.Status.SUCCESS
    job.reviews_found = 1
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "reviews_found", "finished_at"])

    from analysis.tasks import analyze_product_reviews

    analyze_product_reviews.delay(product_id)
