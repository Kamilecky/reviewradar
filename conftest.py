"""
Project-wide pytest fixtures.
"""

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _use_local_memory_cache(settings):
    """
    Force LocMemCache for the whole test session so the suite never depends
    on a live Redis instance to exercise products/rate_limit.py's counters
    or the throttle classes -- production uses Redis (see CACHES in
    settings/base.py) for cross-process correctness, but that's irrelevant
    to single-process test correctness.

    LocMemCache's backing dict is keyed by LOCATION and persists for the
    whole process, not per-test -- clear it before every test so fixed-window
    rate-limit counters (same test-client IP every time) don't leak between
    tests and cause order-dependent failures.
    """
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()
