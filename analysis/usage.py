"""
Shared usage-tracking helper for every external API call in the project
(Anthropic, YouTube, Google Places, Crawlbase, Reddit), so cost/quota
monitoring isn't reimplemented per call site. See APIUsageLog for the
schema and analysis/admin.py for the aggregated dashboard.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import APIUsageLog

logger = logging.getLogger(__name__)


def log_api_usage(
    provider: str,
    action: str,
    *,
    product_id: int | None = None,
    units: int = 1,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """
    Record one APIUsageLog row. Best-effort: a logging failure (e.g. a bad
    product_id) is caught and logged, never propagated -- cost tracking must
    not break the actual API call it's tracking.
    """
    try:
        APIUsageLog.objects.create(
            provider=provider,
            action=action,
            product_id=product_id,
            units=units,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Failed to record APIUsageLog for provider=%s action=%s", provider, action
        )
        return

    _check_daily_budget(provider)


@contextmanager
def track_api_usage(provider: str, action: str, *, product_id: int | None = None, units: int = 1):
    """
    Context manager for providers without a token concept (YouTube, Google
    Places, Crawlbase, Reddit): logs one APIUsageLog row when the wrapped
    block finishes, whether it raised or not -- a request against the
    provider's quota happened either way.
    """
    try:
        yield
    finally:
        log_api_usage(provider, action, product_id=product_id, units=units)


def _check_daily_budget(provider: str) -> None:
    budgets = getattr(settings, "DAILY_PROVIDER_BUDGETS", {})
    budget = budgets.get(provider)
    if not budget:
        return

    since = timezone.now() - timedelta(days=1)
    qs = APIUsageLog.objects.filter(provider=provider, created_at__gte=since)

    if provider == APIUsageLog.Provider.ANTHROPIC:
        agg = qs.aggregate(input_total=Sum("input_tokens"), output_total=Sum("output_tokens"))
        total = (agg["input_total"] or 0) + (agg["output_total"] or 0)
    else:
        total = qs.aggregate(total=Sum("units"))["total"] or 0

    if total > budget:
        # TODO: hook up Slack/Sentry notification here once available.
        logger.critical(
            "Daily API budget exceeded for provider=%s: %s > %s (last 24h)",
            provider, total, budget,
        )
