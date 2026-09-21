from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    """Django app config for `reviews` (ReviewSource/Review/UserReview/ScrapeJob models)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reviews'
