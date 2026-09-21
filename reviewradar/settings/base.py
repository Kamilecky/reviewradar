"""
Shared settings for reviewradar project.
Environment-specific settings live in dev.py / prod.py.
"""

from pathlib import Path

import environ

# reviewradar/settings/base.py -> reviewradar/reviewradar/settings -> reviewradar/reviewradar -> reviewradar/ (BASE_DIR)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-change-me-in-prod")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "django_filters",
    "django_celery_beat",
    # local apps
    "accounts",
    "products",
    "reviews",
    "scrapers",
    "analysis",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "reviewradar.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "reviewradar.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "products:search"

# --- Email (password reset, future price-drop notifications) ---
# Console backend prints emails to stdout -- a safe, zero-config default for
# local dev; production sets EMAIL_BACKEND to the SMTP backend via .env.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="ReviewRadar <noreply@reviewradar.local>")

# --- Registration throttling (accounts/views.py::register) ---
REGISTER_IP_RATE_PER_HOUR = env.int("REGISTER_IP_RATE_PER_HOUR", default=5)

# Redis-backed cache (the `redis` package is already a dependency via Celery)
# -- used for the search cost-path rate limiters (products/rate_limit.py) and
# would need to be shared across processes to work correctly in production,
# unlike the default per-process LocMemCache. A separate DB index from the
# Celery broker (0) keeps cache keys from mixing with queue data.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("DJANGO_CACHE_URL", default="redis://localhost:6379/1"),
    }
}

# --- REST Framework ---
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    # Scoped per-action via ProductViewSet's throttle_scope (see products/views.py)
    "DEFAULT_THROTTLE_RATES": {
        "refresh-reviews": "10/hour",
        "crawlbase-fetch": "20/hour",
    },
}

# --- Search cost-path limits (products/web_views.py::product_search) ---
# Gate on auto-creating a new Product from an unmatched search + the live
# YouTube/Google Places fetch that comes with it -- both cost real API quota.
# Plain search over the existing catalog is never gated by these.
SEARCH_COST_PATH_IP_RATE_PER_HOUR = env.int("SEARCH_COST_PATH_IP_RATE_PER_HOUR", default=5)
DAILY_NEW_PRODUCT_LIMIT = env.int("DAILY_NEW_PRODUCT_LIMIT", default=200)

# --- Daily API usage budgets (analysis/usage.py) ---
# A provider missing here (or set to 0) gets no budget alert. Units are
# tokens for anthropic, request count for everything else.
DAILY_PROVIDER_BUDGETS = {
    "anthropic": env.int("DAILY_ANTHROPIC_TOKEN_BUDGET", default=0) or None,
    "youtube": env.int("DAILY_YOUTUBE_REQUEST_BUDGET", default=0) or None,
    "google_places": env.int("DAILY_GOOGLE_PLACES_REQUEST_BUDGET", default=0) or None,
    "crawlbase": env.int("DAILY_CRAWLBASE_REQUEST_BUDGET", default=0) or None,
    "reddit": env.int("DAILY_REDDIT_REQUEST_BUDGET", default=0) or None,
}

# --- Celery ---
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# How far back a product is considered "recently added" for the periodic refresh task.
REVIEW_REFRESH_WINDOW_DAYS = env.int("REVIEW_REFRESH_WINDOW_DAYS", default=30)

# Minimum time between classify_sentiment retry attempts for the same Review
# after a failed attempt (see analyze_product_reviews) -- avoids hammering
# the Anthropic API for a review that just failed, every single task run.
SENTIMENT_RETRY_COOLDOWN_MINUTES = env.int("SENTIMENT_RETRY_COOLDOWN_MINUTES", default=15)

# --- Reddit API (PRAW) ---
REDDIT_CLIENT_ID = env("REDDIT_CLIENT_ID", default="")
REDDIT_CLIENT_SECRET = env("REDDIT_CLIENT_SECRET", default="")
REDDIT_USER_AGENT = env("REDDIT_USER_AGENT", default="reviewradar/0.1 by u/changeme")
REDDIT_SUBREDDITS = env.list(
    "REDDIT_SUBREDDITS",
    default=["headphones", "laptops", "tablets", "smartphones", "buildapc"],
)
REDDIT_SEARCH_LIMIT = env.int("REDDIT_SEARCH_LIMIT", default=25)

# --- Forum scraping ---
SCRAPER_USER_AGENT = env(
    "SCRAPER_USER_AGENT",
    default="Mozilla/5.0 (compatible; ReviewRadarBot/0.1; +https://example.com/bot)",
)
SCRAPER_MIN_DELAY_SECONDS = env.float("SCRAPER_MIN_DELAY_SECONDS", default=2.0)
SCRAPER_MAX_DELAY_SECONDS = env.float("SCRAPER_MAX_DELAY_SECONDS", default=5.0)

# --- Anthropic (Claude) ---
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL = env("ANTHROPIC_MODEL", default="claude-sonnet-5")
# Only needed for identity-linked (SSO/workspace-scoped) API keys, which the
# Anthropic API rejects without an anthropic-workspace-id header. Leave
# blank for a regular API key.
ANTHROPIC_WORKSPACE_ID = env("ANTHROPIC_WORKSPACE_ID", default="")

# --- Google Places API (official REST API, no scraping) ---
GOOGLE_PLACES_API_KEY = env("GOOGLE_PLACES_API_KEY", default="")

# --- YouTube Data API v3 (official REST API, no scraping) ---
YOUTUBE_API_KEY = env("YOUTUBE_API_KEY", default="")

# --- Crawlbase (reddit-post scraper, fetches one known URL at a time) ---
CRAWLBASE_TOKEN = env("CRAWLBASE_TOKEN", default="")
