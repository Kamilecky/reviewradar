"""
Rate limiting helpers built on plain Django cache primitives (fixed-window
counters) rather than a third-party package like django-ratelimit -- the
search cost-path's daily global cap already needed a cache/Redis-backed
counter (see settings.DAILY_NEW_PRODUCT_LIMIT), so the same primitive covers
every other narrow rate-limiting need in the project too (e.g.
accounts.views.register) without a new dependency.

Fixed-window counters are simpler than a sliding window and good enough
here: this is abuse protection, not a precise SLA.

Lives in `products` because that's the app that first needed it
(product_search's cost-path gate below) -- it's a generic utility, not
products-specific, so other apps import it directly rather than duplicating
the counter logic.
"""

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone


def increment_and_check(cache_key: str, limit: int, window_seconds: int) -> bool:
    """Increment the counter and return whether it's still within `limit`."""
    try:
        count = cache.incr(cache_key)
    except ValueError:
        # Key doesn't exist yet (either truly new, or the previous window expired).
        cache.set(cache_key, 1, timeout=window_seconds)
        count = 1
    return count <= limit


def cost_path_allowed(request) -> bool:
    """
    Whether product_search may auto-create a new Product and/or trigger
    queue_live_search_fetch for this request -- both cost real external-API
    quota. Gated on a per-IP hourly rate and a global daily cap; plain
    search over the existing catalog is never gated by this.
    """
    ip = request.META.get("REMOTE_ADDR", "unknown")
    now = timezone.now()
    hour_key = f"search_cost_path:ip:{ip}:{now:%Y%m%d%H}"
    day_key = f"search_cost_path:global:{now:%Y%m%d}"

    ip_ok = increment_and_check(hour_key, settings.SEARCH_COST_PATH_IP_RATE_PER_HOUR, 3600)
    day_ok = increment_and_check(day_key, settings.DAILY_NEW_PRODUCT_LIMIT, 86400)
    return ip_ok and day_ok
