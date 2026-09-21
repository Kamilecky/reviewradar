from django.conf import settings
from django.db import models


class APIUsageLog(models.Model):
    """
    Usage record for one call to an external API, written by
    analysis.usage.log_api_usage()/track_api_usage() -- the single place
    every provider's call sites (analysis.retry, scrapers.tasks) log
    through, so cost/quota monitoring isn't reimplemented per call site.

    units is the generic "how much did this cost" figure for providers
    without a token concept (1 per request, typically); input_tokens/
    output_tokens are Anthropic-specific and left null for everything else.
    """

    class Provider(models.TextChoices):
        ANTHROPIC = "anthropic", "Anthropic"
        YOUTUBE = "youtube", "YouTube"
        GOOGLE_PLACES = "google_places", "Google Places"
        CRAWLBASE = "crawlbase", "Crawlbase"
        REDDIT = "reddit", "Reddit"

    provider = models.CharField(max_length=20, choices=Provider.choices)
    action = models.CharField(max_length=50)
    product = models.ForeignKey(
        "products.Product", on_delete=models.CASCADE, null=True, blank=True,
        related_name="api_usage_logs",
    )
    units = models.PositiveIntegerField(default=1)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"APIUsageLog({self.provider}, {self.action}, product_id={self.product_id})"


class SummaryFlag(models.Model):
    """
    A user's report that Product.pros_summary/cons_summary looks wrong.
    get_or_create'd from the view (see products.web_views.flag_summary) --
    unique_together makes a repeat report from the same user a no-op rather
    than a duplicate row, with no moderation workflow beyond the admin list
    at this stage (see APIUsageLog for the same "just log it" precedent).
    """

    class Target(models.TextChoices):
        PROS = "pros", "Zalety"
        CONS = "cons", "Wady"

    product = models.ForeignKey(
        "products.Product", on_delete=models.CASCADE, related_name="summary_flags"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="summary_flags"
    )
    target = models.CharField(max_length=4, choices=Target.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("product", "user", "target")
        ordering = ["-created_at"]

    def __str__(self):
        return f"SummaryFlag({self.product}, {self.user}, {self.target})"
