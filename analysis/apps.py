from django.apps import AppConfig


class AnalysisConfig(AppConfig):
    """Django app config for `analysis` (Anthropic-backed sentiment/summary/overview + cost tracking)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analysis'
