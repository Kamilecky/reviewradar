import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import Client
from django.utils import timezone
from rest_framework.test import APIClient

from products.models import Category, Product
from reviews.models import ReviewSource


def _admin_client():
    client = APIClient()
    admin = get_user_model().objects.create_user(
        username="admin-test", password="x", is_staff=True
    )
    client.force_authenticate(user=admin)
    return client


@pytest.mark.django_db
class TestProductModel:
    def test_category_slug_auto_generated(self):
        category = Category.objects.create(name="Headphones")
        assert category.slug == "headphones"

    def test_str_representation(self):
        category = Category.objects.create(name="Laptops")
        product = Product.objects.create(
            name="ThinkPad X1", category=category, brand="Lenovo", model_name="X1 Carbon"
        )
        assert str(product) == "Lenovo X1 Carbon"

    def test_brand_model_unique_together(self):
        category = Category.objects.create(name="Tablets")
        Product.objects.create(
            name="iPad Air", category=category, brand="Apple", model_name="iPad Air 5"
        )
        with pytest.raises(IntegrityError):
            Product.objects.create(
                name="iPad Air (dup)", category=category, brand="Apple", model_name="iPad Air 5"
            )


@pytest.mark.django_db
class TestCategorySpecSchema:
    def test_default_schema_seeded_from_main_category(self):
        category = Category.objects.create(
            name="Laptopy", main_category=Category.MainCategory.COSMETICS
        )
        assert category.spec_schema["properties"]["product_type"]["enum"] == [
            "pielegnacja",
            "makijaz",
            "higiena",
        ]

    def test_explicit_schema_is_not_overwritten(self):
        custom_schema = {"type": "object", "properties": {"foo": {"type": "string"}}}
        category = Category.objects.create(
            name="Perfumy",
            main_category=Category.MainCategory.COSMETICS,
            spec_schema=custom_schema,
        )
        assert category.spec_schema == custom_schema


@pytest.mark.django_db
class TestProductSpecificationValidation:
    def test_valid_electronics_specification_passes(self):
        category = Category.objects.create(
            name="Laptopy", main_category=Category.MainCategory.ELECTRONICS
        )
        product = Product(
            name="XPS 13",
            category=category,
            brand="Dell",
            model_name="XPS 13",
            specification={"technical_parameters": {"RAM": "16GB"}, "warranty_months": 24},
        )
        product.full_clean()

    def test_empty_specification_passes_regression(self):
        """Existing electronics products with no/blank specification must keep validating."""
        category = Category.objects.create(
            name="Headphones", main_category=Category.MainCategory.ELECTRONICS
        )
        product = Product(
            name="WH-1000XM5", category=category, brand="Sony", model_name="WH-1000XM5"
        )
        product.full_clean()

    def test_valid_cosmetics_specification_passes(self):
        category = Category.objects.create(
            name="Pielegnacja", main_category=Category.MainCategory.COSMETICS
        )
        product = Product(
            name="Krem nawilzajacy",
            category=category,
            brand="Nivea",
            model_name="Creme",
            specification={"product_type": "pielegnacja", "capacity_ml": 100},
        )
        product.full_clean()

    def test_valid_household_specification_passes(self):
        category = Category.objects.create(
            name="Naczynia", main_category=Category.MainCategory.HOUSEHOLD
        )
        product = Product(
            name="Garnek",
            category=category,
            brand="Tefal",
            model_name="Ingenio",
            specification={"material": "stal nierdzewna", "room": "kuchnia"},
        )
        product.full_clean()

    def test_invalid_specification_type_is_rejected(self):
        category = Category.objects.create(
            name="Pielegnacja 2", main_category=Category.MainCategory.COSMETICS
        )
        product = Product(
            name="Zle dane",
            category=category,
            brand="Test",
            model_name="Bad",
            # capacity_ml must be a number, not a string
            specification={"capacity_ml": "duzo"},
        )
        with pytest.raises(ValidationError):
            product.full_clean()

    def test_invalid_enum_value_is_rejected(self):
        category = Category.objects.create(
            name="Pielegnacja 3", main_category=Category.MainCategory.COSMETICS
        )
        product = Product(
            name="Zle dane 2",
            category=category,
            brand="Test",
            model_name="Bad2",
            specification={"product_type": "nieznany_typ"},
        )
        with pytest.raises(ValidationError):
            product.full_clean()


@pytest.mark.django_db
class TestFetchCrawlbaseRedditPostEndpoint:
    def _product(self):
        category = Category.objects.create(name="Smartfony")
        return Product.objects.create(
            name="Galaxy S25 Ultra", category=category, brand="Samsung", model_name="S25 Ultra"
        )

    def test_anonymous_request_is_rejected(self):
        product = self._product()
        client = APIClient()

        response = client.post(
            f"/api/products/{product.id}/fetch-crawlbase-reddit-post/",
            {"url": "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"},
        )

        assert response.status_code in (401, 403)

    def test_missing_url_returns_400(self):
        product = self._product()
        client = _admin_client()

        response = client.post(f"/api/products/{product.id}/fetch-crawlbase-reddit-post/", {})

        assert response.status_code == 400

    def test_non_reddit_url_is_rejected(self, mocker):
        product = self._product()
        ReviewSource.objects.create(
            name="Crawlbase Reddit",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://api.crawlbase.com/",
            parser_key="crawlbase_reddit",
        )
        delay_mock = mocker.patch("products.views.fetch_crawlbase_reddit_post_task.delay")
        client = _admin_client()

        response = client.post(
            f"/api/products/{product.id}/fetch-crawlbase-reddit-post/",
            {"url": "https://evil.com/reddit.com/x"},
        )

        assert response.status_code == 400
        delay_mock.assert_not_called()

    def test_missing_review_source_returns_400(self):
        product = self._product()
        client = _admin_client()

        response = client.post(
            f"/api/products/{product.id}/fetch-crawlbase-reddit-post/",
            {"url": "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"},
        )

        assert response.status_code == 400

    def test_valid_request_queues_task(self, mocker):
        product = self._product()
        source = ReviewSource.objects.create(
            name="Crawlbase Reddit",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://api.crawlbase.com/",
            parser_key="crawlbase_reddit",
        )
        delay_mock = mocker.patch("products.views.fetch_crawlbase_reddit_post_task.delay")
        client = _admin_client()
        url = "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"

        response = client.post(
            f"/api/products/{product.id}/fetch-crawlbase-reddit-post/", {"url": url}
        )

        assert response.status_code == 202
        delay_mock.assert_called_once_with(product.id, source.id, url)

    def test_throttling_returns_429_after_scope_limit(self, mocker):
        # SimpleRateThrottle.THROTTLE_RATES is bound from api_settings at
        # import time and does not react to overriding settings.REST_FRAMEWORK
        # at test time (a well-known DRF testing gotcha) -- patch the class
        # attribute ScopedRateThrottle actually reads from directly instead.
        from rest_framework.throttling import ScopedRateThrottle

        mocker.patch.object(
            ScopedRateThrottle,
            "THROTTLE_RATES",
            {"refresh-reviews": "10/hour", "crawlbase-fetch": "1/hour"},
        )
        product = self._product()
        ReviewSource.objects.create(
            name="Crawlbase Reddit",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://api.crawlbase.com/",
            parser_key="crawlbase_reddit",
        )
        mocker.patch("products.views.fetch_crawlbase_reddit_post_task.delay")
        client = _admin_client()
        url = "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"

        first = client.post(f"/api/products/{product.id}/fetch-crawlbase-reddit-post/", {"url": url})
        second = client.post(f"/api/products/{product.id}/fetch-crawlbase-reddit-post/", {"url": url})

        assert first.status_code == 202
        assert second.status_code == 429


@pytest.mark.django_db
class TestRefreshReviewsEndpoint:
    def _product_with_source(self):
        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        ReviewSource.objects.create(
            name="YouTube",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://www.googleapis.com/youtube/v3",
            parser_key="youtube",
        )
        return product

    def test_anonymous_request_is_rejected(self):
        product = self._product_with_source()
        client = APIClient()

        response = client.post(f"/api/products/{product.id}/refresh-reviews/")

        assert response.status_code in (401, 403)

    def test_admin_request_queues_matching_sources(self, mocker):
        product = self._product_with_source()
        mock_queue = mocker.patch("products.views.queue_fetch")
        client = _admin_client()

        response = client.post(f"/api/products/{product.id}/refresh-reviews/")

        assert response.status_code == 202
        mock_queue.assert_called_once()


@pytest.mark.django_db
class TestProductSearchLiveFetch:
    def test_search_with_query_triggers_live_fetch_for_matches(self, mocker):
        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        client.get("/", {"q": "Dell"})

        mock_fetch.assert_called_once_with(product)

    def test_search_without_query_does_not_trigger_live_fetch(self, mocker):
        category = Category.objects.create(name="Laptopy")
        Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        client.get("/")

        mock_fetch.assert_not_called()

    def test_search_caps_live_fetch_to_max_results(self, mocker):
        category = Category.objects.create(name="Laptopy")
        for i in range(7):
            Product.objects.create(
                name=f"Model {i}", category=category, brand="Dell", model_name=f"Model{i}"
            )
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        client.get("/", {"q": "Dell"})

        assert mock_fetch.call_count == 5


@pytest.mark.django_db
class TestProductDetailFetchingBanner:
    def test_shows_running_source_as_fetching(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        source = ReviewSource.objects.create(
            name="YouTube",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://www.googleapis.com/youtube/v3",
            parser_key="youtube",
        )
        from reviews.models import ScrapeJob

        ScrapeJob.objects.create(
            product=product, source=source, status=ScrapeJob.Status.RUNNING
        )
        client = Client()

        response = client.get(f"/products/{product.id}/")

        assert response.status_code == 200
        assert response.context["fetching_sources"] == ["YouTube"]
        assert b"Pobieranie najnowszych opinii w toku" in response.content

    def test_no_banner_when_no_active_jobs(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        client = Client()

        response = client.get(f"/products/{product.id}/")

        assert response.context["fetching_sources"] == []
        assert b"Pobieranie najnowszych opinii w toku" not in response.content


@pytest.mark.django_db
class TestProductDetailAiOverviewTab:
    def _product(self):
        category = Category.objects.create(name="Laptopy")
        return Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )

    def test_queues_overview_once_on_first_view(self, mocker):
        mock_queue = mocker.patch("products.web_views.queue_ai_overview")
        product = self._product()
        client = Client()

        response = client.get(f"/products/{product.id}/")

        mock_queue.assert_called_once_with(product)
        assert response.context["ai_overview_pending"] is True
        assert response.context["needs_refresh"] is True
        assert b"Claude generuje odpowied" in response.content

    def test_does_not_requeue_once_already_requested(self, mocker):
        mock_queue = mocker.patch("products.web_views.queue_ai_overview")
        product = self._product()
        Product.objects.filter(pk=product.pk).update(ai_overview_requested_at=timezone.now())
        client = Client()

        response = client.get(f"/products/{product.id}/")

        mock_queue.assert_not_called()
        assert response.context["ai_overview_pending"] is True

    def test_shows_completed_overview_without_pending_banner(self, mocker):
        mock_queue = mocker.patch("products.web_views.queue_ai_overview")
        product = self._product()
        Product.objects.filter(pk=product.pk).update(
            ai_overview="To świetny laptop biznesowy.", ai_overview_requested_at=timezone.now()
        )
        client = Client()

        response = client.get(f"/products/{product.id}/")

        mock_queue.assert_not_called()
        assert response.context["ai_overview_pending"] is False
        assert response.context["needs_refresh"] is False
        assert b"To \xc5\x9bwietny laptop biznesowy." in response.content

    def test_youtube_tab_only_includes_youtube_reviews(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        product = self._product()
        youtube_source = ReviewSource.objects.create(
            name="YouTube",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://www.googleapis.com/youtube/v3",
            parser_key="youtube",
        )
        other_source = ReviewSource.objects.create(
            name="Reddit",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit",
        )
        from reviews.models import Review

        Review.objects.create(
            product=product,
            source=youtube_source,
            raw_text="Great laptop",
            source_url="https://youtube.com/watch?v=1&lc=c1",
            source_url_hash="hash-yt",
        )
        Review.objects.create(
            product=product,
            source=other_source,
            raw_text="Reddit take",
            source_url="https://reddit.com/1",
            source_url_hash="hash-reddit",
        )
        client = Client()

        response = client.get(f"/products/{product.id}/")

        assert [r.raw_text for r in response.context["youtube_reviews"]] == ["Great laptop"]

    def test_google_places_tab_only_includes_google_places_reviews(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        product = self._product()
        google_places_source = ReviewSource.objects.create(
            name="Google Places",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://maps.googleapis.com/maps/api/place",
            parser_key="google_places",
        )
        other_source = ReviewSource.objects.create(
            name="Reddit",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit",
        )
        from reviews.models import Review

        Review.objects.create(
            product=product,
            source=google_places_source,
            raw_text="Świetna obsługa",
            source_url="https://maps.google.com/place/1?review=1",
            source_url_hash="hash-gp",
        )
        Review.objects.create(
            product=product,
            source=other_source,
            raw_text="Reddit take",
            source_url="https://reddit.com/2",
            source_url_hash="hash-reddit-2",
        )
        client = Client()

        response = client.get(f"/products/{product.id}/")

        assert [r.raw_text for r in response.context["google_places_reviews"]] == ["Świetna obsługa"]


@pytest.mark.django_db
class TestProductSearchAutoCreatesStubProduct:
    def test_creates_stub_under_main_category_and_triggers_fetch(self, mocker):
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get("/", {"q": "Samsung Galaxy S25 Ultra", "main_category": "electronics"})

        product = Product.objects.get(brand="Samsung", model_name="Galaxy S25 Ultra")
        assert product.category.main_category == "electronics"
        assert product.category.name == "Inne (Elektronika)"
        mock_fetch.assert_called_once_with(product)
        assert response.context["auto_created_product"] == product
        assert b"dodano nowy wpis" in response.content

    def test_creates_stub_under_specific_category(self, mocker):
        category = Category.objects.create(
            name="Laptopy", main_category=Category.MainCategory.ELECTRONICS
        )
        mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        client.get("/", {"q": "Lenovo ThinkPad X1", "category": category.slug})

        product = Product.objects.get(brand="Lenovo", model_name="ThinkPad X1")
        assert product.category_id == category.id

    def test_does_not_auto_create_without_category_context(self, mocker):
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get("/", {"q": "Nieznany produkt bez kategorii"})

        assert not Product.objects.filter(brand="Nieznany").exists()
        assert response.context["auto_created_product"] is None
        mock_fetch.assert_not_called()
        assert b"Wybierz kategori" in response.content

    def test_reuses_existing_match_instead_of_creating_duplicate(self, mocker):
        mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()
        client.get("/", {"q": "Samsung Galaxy S25 Ultra", "main_category": "electronics"})
        assert Product.objects.filter(brand="Samsung", model_name="Galaxy S25 Ultra").count() == 1

        client.get("/", {"q": "Samsung Galaxy S25 Ultra", "main_category": "electronics"})

        assert Product.objects.filter(brand="Samsung", model_name="Galaxy S25 Ultra").count() == 1


@pytest.mark.django_db
class TestProductSearchCostPathGating:
    def test_honeypot_field_blocks_creation_and_live_fetch(self, mocker):
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get(
            "/",
            {"q": "Sony WH-1000XM6", "main_category": "electronics", "website": "spam"},
        )

        assert response.status_code == 200
        assert not Product.objects.filter(brand="Sony").exists()
        mock_fetch.assert_not_called()

    def test_too_short_query_does_not_auto_create(self, mocker):
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get("/", {"q": "ab", "main_category": "electronics"})

        assert response.context["auto_created_product"] is None
        assert Product.objects.count() == 0
        mock_fetch.assert_not_called()

    def test_repeated_character_query_does_not_auto_create(self, mocker):
        mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get("/", {"q": "aaaaaaa", "main_category": "electronics"})

        assert response.context["auto_created_product"] is None
        assert Product.objects.count() == 0

    def test_daily_cap_exceeded_blocks_creation_but_search_still_works(self, settings, mocker):
        settings.DAILY_NEW_PRODUCT_LIMIT = 0
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get("/", {"q": "Brand New Gadget", "main_category": "electronics"})

        assert response.status_code == 200
        assert response.context["cost_path_limited"] is True
        assert not Product.objects.filter(brand="Brand").exists()
        mock_fetch.assert_not_called()

    def test_per_ip_hourly_limit_blocks_creation_after_threshold(self, settings, mocker):
        settings.SEARCH_COST_PATH_IP_RATE_PER_HOUR = 1
        mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        first = client.get("/", {"q": "First Gadget", "main_category": "electronics"})
        second = client.get("/", {"q": "Second Gadget", "main_category": "electronics"})

        assert first.context["cost_path_limited"] is False
        assert second.context["cost_path_limited"] is True
        assert Product.objects.filter(brand="First").exists()
        assert not Product.objects.filter(brand="Second").exists()

    def test_existing_matches_are_not_live_fetched_when_daily_cap_exceeded(self, settings, mocker):
        category = Category.objects.create(name="Laptopy")
        Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        settings.DAILY_NEW_PRODUCT_LIMIT = 0
        mock_fetch = mocker.patch("products.web_views.queue_live_search_fetch")
        client = Client()

        response = client.get("/", {"q": "Dell"})

        assert response.status_code == 200
        assert response.context["result_count"] == 1
        mock_fetch.assert_not_called()


@pytest.mark.django_db
class TestProductDetailAccountsContext:
    def _product(self):
        category = Category.objects.create(name="Laptopy-accounts")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        Product.objects.filter(pk=product.pk).update(ai_overview_requested_at=timezone.now())
        return Product.objects.get(pk=product.pk)

    def test_is_watched_false_for_anonymous(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        product = self._product()
        client = Client()

        response = client.get(f"/products/{product.id}/")

        assert response.context["is_watched"] is False

    def test_is_watched_true_after_toggling_watchlist(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        from accounts.models import Watchlist

        product = self._product()
        user = get_user_model().objects.create_user(username="watcher", password="x")
        Watchlist.objects.create(user=user, product=product)
        client = Client()
        client.force_login(user)

        response = client.get(f"/products/{product.id}/")

        assert response.context["is_watched"] is True

    def test_rating_aggregates_computed_from_user_reviews(self, mocker):
        mocker.patch("products.web_views.queue_ai_overview")
        from reviews.models import UserReview

        product = self._product()
        user_a = get_user_model().objects.create_user(username="rater-a", password="x")
        user_b = get_user_model().objects.create_user(username="rater-b", password="x")
        UserReview.objects.create(product=product, user=user_a, rating=4)
        UserReview.objects.create(product=product, user=user_b, rating=2)
        client = Client()

        response = client.get(f"/products/{product.id}/")

        assert response.context["review_count"] == 2
        assert response.context["avg_rating"] == 3
