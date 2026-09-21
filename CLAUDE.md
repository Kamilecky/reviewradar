# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ReviewRadar aggregates products across three main categories (electronics, cosmetics, household) with opinions collected from Reddit (official API via PRAW), category-matched public forums (BeautifulSoup scrapers), and official REST APIs (Google Places, YouTube Data API, Crawlbase for one-off Reddit-post fetches — no scraping). Reviews are classified by sentiment, and an aggregate pros/cons summary is generated per product via the Anthropic (Claude) API, with prompts adapted to the product's category. Every external API call is cost-tracked (`analysis.models.APIUsageLog`) and the two cost-triggering DRF actions plus the search auto-create/live-fetch path are authenticated/throttled/rate-limited — see the Architecture section below. Registered users (separate from Django staff) can watch products, rate them 1-5, and flag inaccurate AI summaries (`accounts`/`reviews.UserReview`/`analysis.SummaryFlag`).

## Commands

```bash
# Setup
python -m venv venv
venv\Scripts\activate                 # Windows
pip install -r requirements.txt
copy .env.example .env                # then fill in secrets as needed

# Local services (Postgres + Redis, only needed for Celery / prod-like DB)
docker compose up -d

# DB
python manage.py migrate
python manage.py createsuperuser

# Run (four separate terminals)
python manage.py runserver
celery -A reviewradar worker -l info --pool=solo   # --pool=solo required on Windows
celery -A reviewradar beat -l info

# Tests
pytest                                # all tests
pytest reviews/tests.py               # single app
pytest reviews/tests.py::TestReviewDedup::test_hash_is_deterministic  # single test
```

`DJANGO_SETTINGS_MODULE` defaults to `reviewradar.settings.dev` (set in `manage.py`), which uses local SQLite — no Postgres needed to run the server. Switch to `reviewradar.settings.prod` + `DATABASE_URL` for a Postgres-backed run.

## Architecture

**Apps and their one job each:**

- `accounts` — user identity only: registration (`RegistrationForm`, a `UserCreationForm` requiring a unique email, throttled per-IP via `products.rate_limit.increment_and_check`), the full password lifecycle (Django's own `LoginView`/`LogoutView`/`PasswordChangeView`/`PasswordResetView` family — no custom views, only `template_name`/`success_url`), email change (`change_email`/`EmailChangeForm` — the one piece Django has no built-in view for), a profile page, `Watchlist` (`user`+`product`, `unique_together`) and its list page. Uses the built-in `auth.User`, no custom user model.
- `products` — `Category` (`main_category` + `spec_schema`, a JSON Schema template per main category — see `spec_schemas.py`), `Product` (`specification` JSON validated against its category's schema in `Product.clean()`; `pros_summary`/`cons_summary` written by `analysis`, not user-editable)
- `reviews` — `ReviewSource` (`source_type` drives Celery dispatch; `main_categories` — which `Category.MainCategory` values it applies to, empty = all; `parser_config` — subreddits for reddit sources, CSS selectors for forum sources; `tos_checked_at`/`tos_notes` — ToS sign-off trail), `Review` (deduped by sha256 hash of source URL — `source_url_hash`), `UserReview` (a registered user's own 1-5 rating + comment, `unique_together=("product","user")` — editable, not scraped, no `ReviewSource`/sentiment), `ScrapeJob` (fetch run log)
- `scrapers` — fetching only, never persistence directly: `reddit_scraper.py` (PRAW, dedicated dispatch path), `generic_forum_scraper.py` (config-driven HTML scraping, selectors from `parser_config`), `forum_scraper_example.py` / `beauty_forum_scraper.py` (bespoke per-forum HTML scraping examples), `google_places_scraper.py` / `youtube_scraper.py` (official REST APIs, no scraping — same `fetch()` contract), `registry.py` (maps `ReviewSource.parser_key` → parser, `PARSER_REGISTRY`, for everything except Reddit), `dedup.py` (the *only* place that writes `Review` rows, source-agnostic), `tasks.py` (`queue_fetch()` dispatch + the fetch tasks)
- `analysis` — `sentiment.py` (per-review classification), `summarizer.py` (aggregate pros/cons), `prompts.py` (per-`main_category` label/aspect-hint injected into both prompts; output format `{"sentiment": ...}` / `{"pros": ..., "cons": ...}` never changes), Celery task in `tasks.py`, `SummaryFlag` (a user reporting `pros_summary`/`cons_summary` as inaccurate — `unique_together=("product","user","target")`, `get_or_create`'d so a repeat report is a no-op, surfaced only in `/admin/analysis/summaryflag/` — no moderation workflow yet)

**Category model (`products/spec_schemas.py` + `Category`):** `Product.specification` stays a single `JSONField` rather than per-category profile models — a new *specific* category (e.g. "Perfumy" under cosmetics) is just an admin row inheriting its main category's default schema, no migration. A new *main* category (a 4th group beyond electronics/cosmetics/household) is a deliberate code change: `Category.MainCategory`, `spec_schemas.SPEC_SCHEMAS`, and usually `analysis/prompts.py`.

**Parser → persistence split (`scrapers/base.py`):** every source parser (Reddit, each forum, each official-API source) only yields `FetchedReview` objects; it must never touch the database. All persistence and dedup logic lives solely in `scrapers/dedup.py::save_reviews`, identically regardless of source. This is what lets a new non-Reddit source be added as either a `parser_config` tweak (same engine, via `generic_forum_scraper`) or one parser file + one `PARSER_REGISTRY` entry, plus one `ReviewSource` row — with zero changes to models, Celery wiring, or other parsers.

**Category-scoped dispatch:** `ReviewSource.applies_to(main_category)` (empty `main_categories` = matches everything) gates which sources get queued. Both `scrapers/tasks.py::refresh_recent_products` (Celery Beat) and `products/views.py::ProductViewSet.refresh_reviews` (manual API trigger) filter through it before calling the shared `scrapers/tasks.py::queue_fetch(source, product)`, which picks `fetch_reddit_reviews` (source_type=REDDIT) vs `fetch_registry_reviews` (everything else — FORUM, REVIEW_SITE, OTHER, all resolved via `registry.get_parser(source.parser_key)`) based on `source.source_type` — don't reintroduce an if/else at the call sites.

**Fetch → analyze pipeline:** `fetch_reddit_reviews(product_id, source_id)` / `fetch_registry_reviews(product_id, source_id)` create a `ScrapeJob`, fetch (reddit reads subreddits from `source.parser_config`, falling back to `settings.REDDIT_SUBREDDITS`), save via `dedup.save_reviews`, and — only if new reviews were actually created — enqueue `analysis.tasks.analyze_product_reviews`, which classifies sentiment for unclassified reviews and regenerates the product's `pros_summary`/`cons_summary` using its category's prompt context.

**Anthropic calls go through `analysis/retry.py::call_with_retry`**, not the SDK's own retry (`_client()` is built with `max_retries=0`): rate limits/5xx/connection errors retry with backoff (honoring `Retry-After` if present) up to 3 attempts and return `None` on exhaustion; 400/401/403/404/413/422 raise `PermanentAPIError` immediately (retrying a bad key/model/request never helps). `classify_sentiment`/`summarize_pros_cons` catch `PermanentAPIError` and return `None` like any other failure — they don't need to tell it apart from a transient one. `overview.ask_about_product` deliberately does *not* catch it, letting it reach `analysis.tasks.generate_product_ai_overview`, the one caller that must distinguish "permanent → write `FALLBACK_OVERVIEW_MESSAGE`" from "transient → clear `ai_overview_requested_at` so the next page view retries" (writing the fallback on every failure was the bug this fixed — it made a rate limit look identical to a bad API key and permanently blocked retry). `classify_sentiment`/`summarize_pros_cons` force structured output via tool use (`tool_choice`) instead of parsing JSON out of free text. `analyze_product_reviews` only retries a review's failed classification after `settings.SENTIMENT_RETRY_COOLDOWN_MINUTES` (`Review.sentiment_last_attempt_at`), not on every task run.

**Every external API call logs `analysis.models.APIUsageLog`** (provider/action/units, or input/output tokens for Anthropic) through `analysis/usage.py::log_api_usage`/`track_api_usage` — the one place cost tracking lives, not reimplemented per call site. `call_with_retry` calls it directly for Anthropic; `scrapers/tasks.py` wraps `save_reviews(...)` (not `fetch()` itself — `fetch()` is a lazy generator, so real HTTP calls happen during iteration) in `track_api_usage(...)` for Reddit/YouTube/Google Places/Crawlbase, keyed off `PARSER_KEY_TO_PROVIDER`. Logging always fires a `_check_daily_budget` check that logs `CRITICAL` (with a `# TODO` for Slack/Sentry) if `settings.DAILY_PROVIDER_BUDGETS[provider]` is exceeded in the last 24h. `/admin/analysis/apiusagelog/` shows 24h/7d sums per provider above the list (`APIUsageLogAdmin.changelist_view` + `templates/admin/analysis/apiusagelog/change_list.html`).

**The two cost-triggering DRF actions require staff + are throttled:** `ProductViewSet.refresh_reviews`/`fetch_crawlbase_reddit_post` set `permission_classes=[IsAdminUser]` and `throttle_classes=[ScopedRateThrottle]` *per-action* via `@action(...)` kwargs (DRF applies these as instance attributes only for that action's route, not the whole ViewSet) — note `ProductViewSet` must declare `throttle_scope = None` as a class attribute first, since `APIView` doesn't define it and `ViewSet.as_view()`'s `hasattr()` check on `@action` kwargs would otherwise raise `TypeError`. `fetch_crawlbase_reddit_post` also whitelists the URL's domain to `reddit.com` (`_is_reddit_url`) so it can't be used as an open scraping proxy. `product_search` (the public search view) has its own, separate cost gate — see below.

**`product_search`'s live-fetch is behind `products/rate_limit.py::cost_path_allowed()`, not just the auto-created-stub path.** Any non-empty `?q=` triggers `queue_live_search_fetch` for matched products (existing *or* newly auto-created) — both cost real quota, so both are gated together by one per-request check: a hidden honeypot field (`website`, see `templates/base.html`), a per-IP hourly counter, and a global daily counter (both plain `cache.incr()` fixed-window counters, not a third-party rate-limit package — chosen because the daily cap already needed a cache-backed counter, so reusing the same primitive avoided a new dependency for the narrow per-IP case too). Plain search over the existing catalog is never gated. `_is_meaningful_query()` (min length + not a single repeated character) is a separate, narrower check that *only* guards stub-product creation, not live-fetch for existing matches. **`products/rate_limit.py::increment_and_check` (the underlying fixed-window counter) is a generic, cross-app utility** despite living in `products` — it's the app that needed it first, not the only one that gets to use it; `accounts.views.register` imports it directly for per-IP registration throttling rather than duplicating the counter. Add new rate limits here too instead of reimplementing.

**Two independent HTTP surfaces on the same models, no shared views:**
- REST API under `/api/` (DRF, `products/views.py` + `products/serializers.py`, `reviews/views.py`) — `ProductViewSet` has `refresh-reviews`/`fetch-crawlbase-reddit-post` actions (staff-only + throttled, see above).
- Server-rendered search UI at `/` (`products/web_views.py` + `products/web_urls.py`, namespace `products`, templates in `templates/`) — plain Django views hitting the ORM directly, unrelated to the DRF serializers.

**`products/web_views.py` is the orchestration layer for the product-detail page, not just CRUD on `Product`.** It already read `reviews.models.ScrapeJob` and called `analysis.tasks.queue_ai_overview` directly before user accounts existed; `toggle_watchlist`/`submit_user_review`/`flag_summary` (all `@login_required` + `@require_POST`, redirecting back to `products:detail`) extend the same pattern by writing to `accounts.models.Watchlist`/`reviews.models.UserReview`/`analysis.models.SummaryFlag` from here rather than adding views to those apps — keep new product-detail-page actions here too, even when the model they touch lives elsewhere.

**Adding a new source:** same forum engine as an existing one → new `ReviewSource` row with `parser_key="generic_forum"` and CSS selectors in `parser_config`, no new file. A genuinely different forum layout, or a new official API (like `google_places_scraper.py`/`youtube_scraper.py`) → write `scrapers/<name>_scraper.py` with `fetch(product_name, base_url, parser_config=None)` returning `FetchedReview`s, register it in `scrapers/registry.py::PARSER_REGISTRY`, then create a `ReviewSource` row (`source_type=forum` or `review_site`) whose `parser_key` matches and whose `main_categories` scopes it. Before enabling an HTML-scraping source on a real forum, check its `robots.txt`/ToS (not automated by the code); official-API sources (Google Places, YouTube) skip that but still have quotas/costs to check. Either way, record the outcome on `ReviewSource.tos_checked_at`/`tos_notes`.

## Windows notes

- Celery worker must run with `--pool=solo` on Windows.
