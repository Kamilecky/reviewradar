from django.contrib import admin

from .models import Watchlist


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    """Read-only-ish browsing of who watches what; no custom logic."""

    list_display = ("user", "product", "created_at")
    list_filter = ("created_at",)
