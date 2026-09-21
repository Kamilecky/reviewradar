from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from products.models import Category, Product
from reviews.models import Review, ReviewSource, UserReview
from scrapers.base import FetchedReview
from scrapers.dedup import save_reviews
from scrapers.tasks import (
    fetch_crawlbase_reddit_post,
    fetch_reddit_reviews,
    fetch_registry_reviews,
    queue_fetch,
    queue_live_search_fetch,
)


@pytest.fixture
def product(db):
    category = Category.objects.create(name="Headphones")
    return Product.objects.create(
        name="WH-1000XM5", category=category, brand="Sony", model_name="WH-1000XM5"
    )


@pytest.fixture
def reddit_source(db):
    return ReviewSource.objects.create(
        name="Reddit",
        source_type=ReviewSource.SourceType.REDDIT,
        base_url="https://www.reddit.com",
        parser_key="reddit",
    )


@pytest.fixture
def crawlbase_source(db):
    return ReviewSource.objects.create(
        name="Crawlbase Reddit",
        source_type=ReviewSource.SourceType.REVIEW_SITE,
        base_url="https://api.crawlbase.com/",
        parser_key="crawlbase_reddit",
    )


@pytest.mark.django_db
class TestReviewDedup:
    def test_hash_is_deterministic(self):
        url = "https://www.reddit.com/r/headphones/comments/abc123/great_review/"
        assert Review.hash_url(url) == Review.hash_url(url)

    def test_save_reviews_creates_new_reviews(self, product, reddit_source):
        fetched = [
            FetchedReview(source_url="https://reddit.com/1", raw_text="Great sound"),
            FetchedReview(source_url="https://reddit.com/2", raw_text="Bad battery"),
        ]

        created = save_reviews(product, reddit_source, fetched)

        assert created == 2
        assert Review.objects.filter(product=product).count() == 2

    def test_save_reviews_skips_duplicates_by_url_hash(self, product, reddit_source):
        duplicate_url = "https://reddit.com/1"
        Review.objects.create(
            product=product,
            source=reddit_source,
            raw_text="Existing review",
            source_url=duplicate_url,
            source_url_hash=Review.hash_url(duplicate_url),
        )

        fetched = [
            FetchedReview(source_url=duplicate_url, raw_text="Same post, fetched again"),
            FetchedReview(source_url="https://reddit.com/2", raw_text="A genuinely new review"),
        ]

        created = save_reviews(product, reddit_source, fetched)

        assert created == 1
        assert Review.objects.filter(product=product).count() == 2


@pytest.mark.django_db
class TestReviewSourceAppliesTo:
    def test_empty_main_categories_matches_everything(self):
        source = ReviewSource.objects.create(
            name="Reddit - global",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit-global",
        )
        assert source.applies_to("electronics") is True
        assert source.applies_to("cosmetics") is True

    def test_non_matching_category_is_rejected(self):
        source = ReviewSource.objects.create(
            name="Reddit - beauty",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit-beauty",
            main_categories=["cosmetics"],
        )
        assert source.applies_to("electronics") is False

    def test_matching_category_is_accepted(self):
        source = ReviewSource.objects.create(
            name="Reddit - electronics",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit-electronics",
            main_categories=["electronics", "household"],
        )
        assert source.applies_to("electronics") is True


@pytest.mark.django_db
class TestQueueFetch:
    def test_reddit_source_type_dispatches_reddit_task(self, product, reddit_source, mocker):
        delay_mock = mocker.patch.object(fetch_reddit_reviews, "delay")
        registry_delay_mock = mocker.patch.object(fetch_registry_reviews, "delay")

        queue_fetch(reddit_source, product)

        delay_mock.assert_called_once_with(product.id, reddit_source.id)
        registry_delay_mock.assert_not_called()

    def test_forum_source_type_dispatches_registry_task(self, product, mocker):
        forum_source = ReviewSource.objects.create(
            name="Example forum",
            source_type=ReviewSource.SourceType.FORUM,
            base_url="https://forum.example.com",
            parser_key="forum_example",
        )
        delay_mock = mocker.patch.object(fetch_registry_reviews, "delay")
        reddit_delay_mock = mocker.patch.object(fetch_reddit_reviews, "delay")

        queue_fetch(forum_source, product)

        delay_mock.assert_called_once_with(product.id, forum_source.id)
        reddit_delay_mock.assert_not_called()

    def test_review_site_source_type_dispatches_registry_task(self, product, mocker):
        """REVIEW_SITE (e.g. Google Places, YouTube) shares the forum dispatch
        path -- both resolve their parser through scrapers.registry."""
        review_site_source = ReviewSource.objects.create(
            name="Google Places",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://maps.googleapis.com/maps/api/place",
            parser_key="google_places",
        )
        delay_mock = mocker.patch.object(fetch_registry_reviews, "delay")
        reddit_delay_mock = mocker.patch.object(fetch_reddit_reviews, "delay")

        queue_fetch(review_site_source, product)

        delay_mock.assert_called_once_with(product.id, review_site_source.id)
        reddit_delay_mock.assert_not_called()


@pytest.mark.django_db
class TestFetchCrawlbaseRedditPost:
    """
    fetch_crawlbase_reddit_post has its own upsert-with-freshness logic
    (not scrapers.dedup.save_reviews, which never updates), so it's tested
    separately from TestReviewDedup.
    """

    url = "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"

    def _fake_post(self, **overrides):
        post = {
            "id": "1w0p01k",
            "title": "Just got the S25 Ultra",
            "author": "u/someone",
            "score": 120,
            "createdAt": "2024-01-01T12:00:00Z",
            "url": self.url,
            "permalink": "/r/Smartphones/comments/1w0p01k/",
            "selfText": "Second-guessing my purchase",
        }
        post.update(overrides)
        return post

    def test_creates_review_when_none_exists(self, product, crawlbase_source, mocker):
        mocker.patch("scrapers.tasks.fetch_reddit_post", return_value=self._fake_post())
        mocker.patch("analysis.tasks.analyze_product_reviews.delay")

        fetch_crawlbase_reddit_post(product.id, crawlbase_source.id, self.url)

        review = Review.objects.get(source_url_hash=Review.hash_url(self.url))
        assert review.author == "u/someone"
        assert review.score == 120
        assert "Just got the S25 Ultra" in review.raw_text

    def test_skips_fetch_when_existing_review_is_fresh(self, product, crawlbase_source, mocker):
        Review.objects.create(
            product=product,
            source=crawlbase_source,
            raw_text="Old text",
            source_url=self.url,
            source_url_hash=Review.hash_url(self.url),
        )
        fetch_mock = mocker.patch("scrapers.tasks.fetch_reddit_post")

        fetch_crawlbase_reddit_post(product.id, crawlbase_source.id, self.url)

        fetch_mock.assert_not_called()
        assert Review.objects.get(source_url_hash=Review.hash_url(self.url)).raw_text == "Old text"

    def test_refetches_and_updates_stale_review(self, product, crawlbase_source, mocker):
        review = Review.objects.create(
            product=product,
            source=crawlbase_source,
            raw_text="Old text",
            source_url=self.url,
            source_url_hash=Review.hash_url(self.url),
            sentiment=Review.Sentiment.POSITIVE,
        )
        stale_time = timezone.now() - timedelta(hours=25)
        Review.objects.filter(pk=review.pk).update(fetched_at=stale_time)

        mocker.patch(
            "scrapers.tasks.fetch_reddit_post",
            return_value=self._fake_post(selfText="Updated opinion", score=200),
        )
        mocker.patch("analysis.tasks.analyze_product_reviews.delay")

        fetch_crawlbase_reddit_post(product.id, crawlbase_source.id, self.url)

        review.refresh_from_db()
        assert "Updated opinion" in review.raw_text
        assert review.score == 200
        assert review.sentiment is None  # reset for reclassification
        assert review.fetched_at > stale_time

    def test_no_review_created_when_post_fetch_fails(self, product, crawlbase_source, mocker):
        mocker.patch("scrapers.tasks.fetch_reddit_post", return_value=None)

        fetch_crawlbase_reddit_post(product.id, crawlbase_source.id, self.url)

        assert not Review.objects.filter(source_url_hash=Review.hash_url(self.url)).exists()


@pytest.mark.django_db
class TestQueueLiveSearchFetch:
    """queue_live_search_fetch is triggered from the web search UI -- only
    google_places/youtube should fire, never reddit/forum/crawlbase."""

    def test_queues_google_places_and_youtube_for_matching_category(self, product, mocker):
        google_places = ReviewSource.objects.create(
            name="Google Places",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://maps.googleapis.com/maps/api/place",
            parser_key="google_places",
        )
        youtube = ReviewSource.objects.create(
            name="YouTube",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://www.googleapis.com/youtube/v3",
            parser_key="youtube",
        )
        delay_mock = mocker.patch.object(fetch_registry_reviews, "delay")

        queue_live_search_fetch(product)

        assert delay_mock.call_count == 2
        called_args = {call.args for call in delay_mock.call_args_list}
        assert (product.id, google_places.id) in called_args
        assert (product.id, youtube.id) in called_args

    def test_does_not_queue_reddit_forum_or_crawlbase(
        self, product, reddit_source, crawlbase_source, mocker
    ):
        ReviewSource.objects.create(
            name="Example forum",
            source_type=ReviewSource.SourceType.FORUM,
            base_url="https://forum.example.com",
            parser_key="forum_example",
        )
        reddit_delay_mock = mocker.patch.object(fetch_reddit_reviews, "delay")
        registry_delay_mock = mocker.patch.object(fetch_registry_reviews, "delay")

        queue_live_search_fetch(product)

        reddit_delay_mock.assert_not_called()
        registry_delay_mock.assert_not_called()

    def test_excludes_source_not_matching_category(self, product, mocker):
        ReviewSource.objects.create(
            name="Google Places - cosmetics only",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://maps.googleapis.com/maps/api/place",
            parser_key="google_places",
            main_categories=["cosmetics"],
        )
        delay_mock = mocker.patch.object(fetch_registry_reviews, "delay")

        queue_live_search_fetch(product)  # product's category is electronics-default (Headphones)

        delay_mock.assert_not_called()

    def test_broker_failure_is_swallowed_not_raised(self, product, mocker):
        """A plain search must never 500 just because Celery/the broker is down."""
        ReviewSource.objects.create(
            name="Google Places",
            source_type=ReviewSource.SourceType.REVIEW_SITE,
            base_url="https://maps.googleapis.com/maps/api/place",
            parser_key="google_places",
        )
        mocker.patch.object(
            fetch_registry_reviews, "delay", side_effect=ConnectionError("broker unreachable")
        )


@pytest.mark.django_db
class TestSubmitUserReview:
    def _login(self, username="reviewer"):
        user = get_user_model().objects.create_user(username=username, password="x")
        client = Client()
        client.force_login(user)
        return client, user

    def test_anonymous_is_redirected_to_login_and_creates_nothing(self, product):
        client = Client()

        response = client.post(f"/products/{product.id}/review/", {"rating": 5, "comment": "Great"})

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert not UserReview.objects.exists()

    def test_valid_post_creates_review(self, product):
        client, user = self._login()

        response = client.post(f"/products/{product.id}/review/", {"rating": 4, "comment": "Solid"})

        assert response.status_code == 302
        review = UserReview.objects.get(product=product, user=user)
        assert review.rating == 4
        assert review.comment == "Solid"

    def test_second_post_updates_instead_of_duplicating(self, product):
        client, user = self._login()
        client.post(f"/products/{product.id}/review/", {"rating": 3, "comment": "Ok"})

        client.post(f"/products/{product.id}/review/", {"rating": 5, "comment": "Actually great"})

        assert UserReview.objects.filter(product=product, user=user).count() == 1
        review = UserReview.objects.get(product=product, user=user)
        assert review.rating == 5
        assert review.comment == "Actually great"

    @pytest.mark.parametrize("bad_rating", [0, 6])
    def test_rating_out_of_range_rejected(self, product, bad_rating):
        client, user = self._login()

        client.post(f"/products/{product.id}/review/", {"rating": bad_rating, "comment": "x"})

        assert not UserReview.objects.filter(product=product, user=user).exists()

        queue_live_search_fetch(product)  # must not raise
