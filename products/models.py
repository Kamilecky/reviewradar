from django.db import models
from django.utils.text import slugify

from .validation import validate_specification


class Category(models.Model):
    """A product category (specific, e.g. "Słuchawki") under one of three main groups."""

    class MainCategory(models.TextChoices):
        """The three top-level product groups the whole app is organized around."""

        ELECTRONICS = "electronics", "Elektronika"
        COSMETICS = "cosmetics", "Kosmetyki"
        HOUSEHOLD = "household", "Produkty użytkowe / dom"

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    main_category = models.CharField(
        max_length=20, choices=MainCategory.choices, default=MainCategory.ELECTRONICS
    )
    # JSON Schema (draft-07) describing the expected shape of Product.specification
    # for products in this category. See products/spec_schemas.py for the
    # architectural decision behind this (JSONField + schema vs. per-category
    # profile models) and the default templates seeded below.
    spec_schema = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        """Readable label for admin dropdowns/autocomplete."""
        return self.name

    def save(self, *args, **kwargs):
        """Auto-fill `slug` from `name` and `spec_schema` from the main category's default."""
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.spec_schema:
            from .spec_schemas import SPEC_SCHEMAS

            self.spec_schema = SPEC_SCHEMAS.get(self.main_category, {})
        super().save(*args, **kwargs)


class Product(models.Model):
    """One tracked product: identity fields plus AI-generated summaries/overview."""

    name = models.CharField(max_length=255)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    specification = models.JSONField(default=dict, blank=True)

    # Aggregate output of analysis.summarizer, refreshed whenever reviews are re-analyzed.
    pros_summary = models.TextField(blank=True)
    cons_summary = models.TextField(blank=True)
    summary_updated_at = models.DateTimeField(null=True, blank=True)

    # Claude's own free-text answer to "what do you know about this product"
    # (analysis.overview) -- distinct from pros_summary/cons_summary, which
    # are generated *from collected reviews*, not from Claude's own knowledge.
    ai_overview = models.TextField(blank=True)
    # Set as soon as generation is queued (before ai_overview is filled in),
    # so product_detail() only queues it once instead of on every page view.
    ai_overview_requested_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("brand", "model_name")

    def __str__(self):
        """Readable label for admin dropdowns/autocomplete."""
        return f"{self.brand} {self.model_name}"

    def clean(self):
        """Validate `specification` against the category's JSON Schema (admin forms only)."""
        super().clean()
        validate_specification(self.category, self.specification)
