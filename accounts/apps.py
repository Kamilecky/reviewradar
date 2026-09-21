from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Django app config for `accounts` (user identity, watchlist, account lifecycle)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'
