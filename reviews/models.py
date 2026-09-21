import hashlib

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from products.models import Product


class ReviewSource(models.Model):
    class SourceType(models.TextChoices):
        REDDIT = "reddit", "Reddit"
        FORUM = "forum", "Forum"
        REVIEW_SITE = "review_site", "Serwis z recenzjami"  # reserved, no parser yet
        OTHER = "other", "Inne"

    name = models.CharField(max_length=100, unique=True)
    # Drives Celery dispatch (scrapers.tasks.queue_fetch) -- reddit vs. forum,
    # not just a descriptive label.
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    base_url = models.URLField()
    # Maps this source to the scraper module/function that knows how to fetch it
    # (see scrapers.registry). Keeps Celery task dispatch free of if/elif chains.
    parser_key = models.SlugField(unique=True)
    is_active = models.BooleanField(default=True)
    # Category.MainCategory values this source is relevant for, e.g.
    # ["electronics"]. Empty list = applies to every category (backward
    # compatible default for a source that hasn't been scoped yet).
    main_categories = models.JSONField(default=list, blank=True)
    # Per-source parser parameters: {"subreddits": [...]} for reddit sources,
    # CSS selectors for forum sources (see scrapers/generic_forum_scraper.py).
    parser_config = models.JSONField(default=dict, blank=True)
    # Business (not technical) sign-off trail for robots.txt/ToS review --
    # the check itself is never automated, only recorded here.
    tos_checked_at = models.DateTimeField(null=True, blank=True)
    tos_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def applies_to(self, main_category: str) -> bool:
        return not self.main_categories or main_category in self.main_categories


class Review(models.Model):
    class Sentiment(models.TextChoices):
        POSITIVE = "positive", "Positive"
        NEUTRAL = "neutral", "Neutral"
        NEGATIVE = "negative", "Negative"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    source = models.ForeignKey(ReviewSource, on_delete=models.PROTECT, related_name="reviews")
    author = models.CharField(max_length=150, blank=True)
    raw_text = models.TextField()
    summary = models.TextField(blank=True)
    sentiment = models.CharField(
        max_length=10, choices=Sentiment.choices, null=True, blank=True
    )
    # Set on every classify_sentiment attempt (success or failure), so
    # analyze_product_reviews can skip re-attempting a review that just
    # failed instead of retrying it every single task run forever.
    sentiment_last_attempt_at = models.DateTimeField(null=True, blank=True)
    source_url = models.URLField(max_length=1000)
    source_url_hash = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    published_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    # Upvote-style score, where the source has one (e.g. Reddit); None for
    # sources without a comparable concept.
    score = models.IntegerField(null=True, blank=True)
    # Full raw payload from the source API, for debugging and for fields not
    # otherwise modeled -- optional, most sources leave this empty.
    raw_data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-fetched_at"]
        indexes = [
            models.Index(fields=["product", "sentiment"]),
        ]

    def __str__(self):
        return f"Review({self.product}, {self.source})"

    def save(self, *args, **kwargs):
        if not self.source_url_hash:
            self.source_url_hash = self.hash_url(self.source_url)
        super().save(*args, **kwargs)

    @staticmethod
    def hash_url(url: str) -> str:
        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


class UserReview(models.Model):
    """
    A rating + comment submitted directly by a registered user through the
    web UI -- distinct from Review (scraped from an external source): no
    ReviewSource, no sentiment classification, editable by its owner.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="user_reviews")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="product_reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("product", "user")
        ordering = ["-created_at"]

    def __str__(self):
        return f"UserReview({self.product}, {self.user}, {self.rating})"


class ScrapeJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    source = models.ForeignKey(ReviewSource, on_delete=models.CASCADE, related_name="jobs")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="scrape_jobs")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    reviews_found = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"ScrapeJob({self.product}, {self.source}, {self.status})"
