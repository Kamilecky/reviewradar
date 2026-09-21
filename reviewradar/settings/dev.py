from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env

DEBUG = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Convenient in dev if REDDIT/ANTHROPIC keys are missing: tasks log instead of crashing.
DEV_MODE = True
