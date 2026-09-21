from django.conf import settings
from django.db import models

from products.models import Product


class Watchlist(models.Model):
    """A user marking one product as "watched" (one row per user+product pair)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist"
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="watched_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """unique_together prevents duplicate watch entries for the same user+product."""

        unique_together = ("user", "product")
        ordering = ["-created_at"]

    def __str__(self):
        """Readable label for the Django admin list/autocomplete widgets."""
        return f"Watchlist({self.user}, {self.product})"
