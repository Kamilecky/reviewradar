from django.apps import AppConfig


class ScrapersConfig(AppConfig):
    """Django app config for `scrapers` (review-fetching parsers and Celery dispatch tasks)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scrapers'
