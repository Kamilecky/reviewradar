import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from products.models import Product
from reviews.models import Review

from .overview import ask_about_product
from .retry import PermanentAPIError
from .sentiment import classify_sentiment
from .summarizer import summarize_pros_cons

logger = logging.getLogger(__name__)

FALLBACK_OVERVIEW_MESSAGE = (
    "Nie udało się wygenerować odpowiedzi Claude (błąd API lub brak środków "
    "na koncie Anthropic). Spróbuj ponownie później."
)


@shared_task
def analyze_product_reviews(product_id: int):
    """
    Classify sentiment for any not-yet-classified reviews of this product,
    then regenerate the product's aggregate pros/cons summary.

    A review whose most recent classification attempt failed is not retried
    on every single run of this task -- only once SENTIMENT_RETRY_COOLDOWN_MINUTES
    has passed since sentiment_last_attempt_at (set on every attempt,
    success or failure), so a temporary API outage doesn't turn into
    hammering the Anthropic API once per fetch.
    """
    product = Product.objects.select_related("category").get(id=product_id)
    main_category = product.category.main_category

    cooldown_cutoff = timezone.now() - timedelta(
        minutes=settings.SENTIMENT_RETRY_COOLDOWN_MINUTES
    )
    unclassified = Review.objects.filter(product=product, sentiment__isnull=True).filter(
        Q(sentiment_last_attempt_at__isnull=True) | Q(sentiment_last_attempt_at__lt=cooldown_cutoff)
    )
    for review in unclassified:
        sentiment = classify_sentiment(
            review.raw_text, main_category=main_category, product_id=product.id
        )
        review.sentiment_last_attempt_at = timezone.now()
        if sentiment:
            review.sentiment = sentiment
            review.save(update_fields=["sentiment", "sentiment_last_attempt_at"])
        else:
            review.save(update_fields=["sentiment_last_attempt_at"])

    review_texts = list(
        Review.objects.filter(product=product).values_list("raw_text", flat=True)
    )
    summary = summarize_pros_cons(
        f"{product.brand} {product.model_name}",
        review_texts,
        main_category=main_category,
        product_id=product.id,
    )
    if summary is None:
        return

    product.pros_summary = summary["pros"]
    product.cons_summary = summary["cons"]
    product.summary_updated_at = timezone.now()
    product.save(update_fields=["pros_summary", "cons_summary", "summary_updated_at"])


@shared_task
def generate_product_ai_overview(product_id: int):
    """
    Ask Claude directly what it knows about this product (its own training-
    data knowledge, not an analysis of collected reviews) and cache the
    answer on Product.ai_overview.

    Distinguishes *why* ask_about_product didn't return an answer:
    - PermanentAPIError (bad key/model/request -- retrying won't help):
      write FALLBACK_OVERVIEW_MESSAGE so the "generating..." state on the
      detail page resolves instead of looping forever on the auto-refresh.
    - Plain None (transient error, retries exhausted): leave ai_overview
      blank and clear ai_overview_requested_at, so the next visit to the
      product detail page re-queues generation (see
      products/web_views.py::product_detail + queue_ai_overview) instead of
      being permanently stuck "already requested".
    """
    product = Product.objects.select_related("category").get(id=product_id)
    try:
        overview = ask_about_product(
            product.brand, product.model_name, product.category.main_category,
            product_id=product.id,
        )
    except PermanentAPIError:
        product.ai_overview = FALLBACK_OVERVIEW_MESSAGE
        product.save(update_fields=["ai_overview"])
        return

    if overview is None:
        Product.objects.filter(pk=product.pk).update(ai_overview_requested_at=None)
        return

    product.ai_overview = overview
    product.save(update_fields=["ai_overview"])


def queue_ai_overview(product: Product) -> None:
    """
    Enqueue generate_product_ai_overview and mark it as requested -- called
    passively from product_detail() on page view, not an explicit user
    action, so a broker failure must not break the page (mirrors
    scrapers.tasks.queue_live_search_fetch). ai_overview_requested_at is
    only set on a successful enqueue, so a broker hiccup gets retried on
    the next page view instead of getting stuck "pending" forever.
    """
    try:
        generate_product_ai_overview.delay(product.id)
    except Exception:  # noqa: BLE001 - best-effort background trigger, must not break the page
        logger.exception("Failed to enqueue AI overview for product_id=%s", product.id)
        return
    Product.objects.filter(pk=product.pk).update(ai_overview_requested_at=timezone.now())
