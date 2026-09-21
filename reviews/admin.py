from django import forms
from django.contrib import admin

from products.models import Category

from .models import Review, ReviewSource, ScrapeJob, UserReview


class ReviewSourceAdminForm(forms.ModelForm):
    main_categories = forms.MultipleChoiceField(
        choices=Category.MainCategory.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Puste = źródło dotyczy wszystkich kategorii.",
    )

    class Meta:
        model = ReviewSource
        fields = "__all__"

    def clean_main_categories(self):
        return list(self.cleaned_data["main_categories"])


@admin.register(ReviewSource)
class ReviewSourceAdmin(admin.ModelAdmin):
    form = ReviewSourceAdminForm
    list_display = (
        "name",
        "source_type",
        "parser_key",
        "main_categories",
        "is_active",
        "base_url",
    )
    list_filter = ("source_type", "is_active")
    search_fields = ("name", "parser_key")
    fieldsets = (
        (None, {
            "fields": (
                "name",
                "source_type",
                "parser_key",
                "base_url",
                "is_active",
                "main_categories",
                "parser_config",
            ),
        }),
        ("Zgodność z ToS", {
            "fields": ("tos_checked_at", "tos_notes"),
        }),
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "source", "sentiment", "score", "author", "published_at", "fetched_at")
    list_filter = ("sentiment", "source", "product__category")
    search_fields = ("raw_text", "author", "product__name", "source_url")
    readonly_fields = ("source_url_hash", "fetched_at")
    autocomplete_fields = ("product", "source")


@admin.register(UserReview)
class UserReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("product__name", "user__username")


@admin.register(ScrapeJob)
class ScrapeJobAdmin(admin.ModelAdmin):
    list_display = ("product", "source", "status", "reviews_found", "started_at", "finished_at")
    list_filter = ("status", "source")
    search_fields = ("product__name",)
    readonly_fields = ("started_at",)
