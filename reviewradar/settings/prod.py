from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

DATABASES = {
    "default": env.db("DATABASE_URL"),
}

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

DEV_MODE = False
