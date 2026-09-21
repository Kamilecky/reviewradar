from datetime import timedelta

from django.contrib import admin
from django.db.models import Sum
from django.utils import timezone

from .models import APIUsageLog, SummaryFlag


def _usage_summary(since):
    """
    Per-provider totals since `since`: units for non-Anthropic providers,
    input+output tokens for Anthropic. Returns a list of dicts ready for
    the change_list template.
    """
    rows = []
    qs = APIUsageLog.objects.filter(created_at__gte=since)
    for value, label in APIUsageLog.Provider.choices:
        provider_qs = qs.filter(provider=value)
        if value == APIUsageLog.Provider.ANTHROPIC:
            agg = provider_qs.aggregate(
                input_total=Sum("input_tokens"), output_total=Sum("output_tokens")
            )
            total = (agg["input_total"] or 0) + (agg["output_total"] or 0)
            unit_label = "tokens"
        else:
            total = provider_qs.aggregate(total=Sum("units"))["total"] or 0
            unit_label = "requests"
        rows.append({"provider": label, "total": total, "unit_label": unit_label})
    return rows


@admin.register(APIUsageLog)
class APIUsageLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "provider", "action", "product", "units", "input_tokens", "output_tokens")
    list_filter = ("provider", "action")
    search_fields = ("product__name", "product__brand", "product__model_name")
    readonly_fields = ("provider", "action", "product", "units", "input_tokens", "output_tokens", "created_at")
    date_hierarchy = "created_at"
    change_list_template = "admin/analysis/apiusagelog/change_list.html"

    def changelist_view(self, request, extra_context=None):
        now = timezone.now()
        extra_context = extra_context or {}
        extra_context["usage_summary_24h"] = _usage_summary(now - timedelta(days=1))
        extra_context["usage_summary_7d"] = _usage_summary(now - timedelta(days=7))
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(SummaryFlag)
class SummaryFlagAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "target", "created_at")
    list_filter = ("target",)
    search_fields = ("product__name", "user__username")
