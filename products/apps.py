from django.apps import AppConfig


class ProductsConfig(AppConfig):
    """Django app config for `products` (Category/Product models, search UI, REST API)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'products'
