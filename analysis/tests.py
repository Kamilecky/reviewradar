from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from django.utils import timezone

from analysis.overview import ask_about_product
from analysis.retry import PermanentAPIError, call_with_retry
from analysis.sentiment import classify_sentiment
from analysis.summarizer import summarize_pros_cons


def _status_error(cls, status_code: int, headers: dict | None = None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=status_code, request=request, headers=headers or {})
    return cls(message="error", response=response, body=None)


def _connection_error():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=request)


def _tool_use_response(tool_name: str, input_data: dict, input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name=tool_name, input=input_data)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _text_response(text: str, input_tokens=10, output_tokens=5):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


@pytest.mark.django_db
class TestCallWithRetry:
    """Exercises analysis.retry.call_with_retry directly, independent of any
    particular Anthropic call site."""

    def test_succeeds_on_first_try_and_logs_usage(self, mocker):
        from analysis.models import APIUsageLog

        response = _text_response("ok")
        make_request = mocker.Mock(return_value=response)

        result = call_with_retry(make_request, task_name="unit_test", product_id=None)

        assert result is response
        make_request.assert_called_once()
        assert APIUsageLog.objects.count() == 1
        log = APIUsageLog.objects.get()
        assert log.provider == APIUsageLog.Provider.ANTHROPIC
        assert log.action == "unit_test"
        assert log.input_tokens == 10
        assert log.output_tokens == 5

    def test_retries_rate_limit_error_then_succeeds(self, mocker):
        mocker.patch("analysis.retry.time.sleep")
        response = _text_response("ok")
        make_request = mocker.Mock(
            side_effect=[_status_error(anthropic.RateLimitError, 429), response]
        )

        result = call_with_retry(make_request, task_name="unit_test")

        assert result is response
        assert make_request.call_count == 2

    def test_returns_none_after_exhausting_retries_on_rate_limit(self, mocker):
        mock_sleep = mocker.patch("analysis.retry.time.sleep")
        make_request = mocker.Mock(
            side_effect=[_status_error(anthropic.RateLimitError, 429) for _ in range(3)]
        )

        result = call_with_retry(make_request, task_name="unit_test")

        assert result is None
        assert make_request.call_count == 3
        assert mock_sleep.call_count == 2  # sleeps between attempts, not after the last one

    def test_retries_connection_error(self, mocker):
        mocker.patch("analysis.retry.time.sleep")
        response = _text_response("ok")
        make_request = mocker.Mock(side_effect=[_connection_error(), response])

        result = call_with_retry(make_request, task_name="unit_test")

        assert result is response
        assert make_request.call_count == 2

    def test_honors_retry_after_header_instead_of_backoff(self, mocker):
        mock_sleep = mocker.patch("analysis.retry.time.sleep")
        response = _text_response("ok")
        make_request = mocker.Mock(
            side_effect=[
                _status_error(anthropic.RateLimitError, 429, headers={"retry-after": "7"}),
                response,
            ]
        )

        call_with_retry(make_request, task_name="unit_test")

        mock_sleep.assert_called_once_with(7.0)

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 413, 422])
    def test_raises_permanent_error_without_retry(self, mocker, status_code):
        mock_sleep = mocker.patch("analysis.retry.time.sleep")
        make_request = mocker.Mock(
            side_effect=_status_error(anthropic.APIStatusError, status_code)
        )

        with pytest.raises(PermanentAPIError):
            call_with_retry(make_request, task_name="unit_test")

        make_request.assert_called_once()
        mock_sleep.assert_not_called()

    def test_bad_request_error_is_permanent(self, mocker):
        make_request = mocker.Mock(side_effect=_status_error(anthropic.BadRequestError, 400))

        with pytest.raises(PermanentAPIError):
            call_with_retry(make_request, task_name="unit_test")

        make_request.assert_called_once()

    def test_authentication_error_is_permanent(self, mocker):
        make_request = mocker.Mock(side_effect=_status_error(anthropic.AuthenticationError, 401))

        with pytest.raises(PermanentAPIError):
            call_with_retry(make_request, task_name="unit_test")

        make_request.assert_called_once()


@pytest.mark.django_db
class TestClassifySentimentCategoryContext:
    def test_prompt_reflects_cosmetics_category_and_uses_tool_choice(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.return_value = _tool_use_response(
            "classify_sentiment", {"sentiment": "positive"}
        )
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        result = classify_sentiment(
            "Nie podraznil skory, swietnie nawilza", main_category="cosmetics"
        )

        assert result == "positive"
        call_kwargs = fake_client.messages.create.call_args.kwargs
        assert "a cosmetics product" in call_kwargs["messages"][0]["content"]
        assert call_kwargs["tool_choice"] == {"type": "tool", "name": "classify_sentiment"}
        assert call_kwargs["tools"][0]["name"] == "classify_sentiment"

    def test_prompt_defaults_to_electronics(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.return_value = _tool_use_response(
            "classify_sentiment", {"sentiment": "negative"}
        )
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        result = classify_sentiment("Battery dies in two hours")

        assert result == "negative"
        prompt_sent = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "an electronics product" in prompt_sent

    def test_skips_when_api_key_missing(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        assert classify_sentiment("Anything", main_category="household") is None

    def test_returns_none_on_permanent_error(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.side_effect = _status_error(anthropic.AuthenticationError, 401)
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        assert classify_sentiment("Anything") is None

    def test_logs_usage_with_product_id(self, settings, mocker):
        from analysis.models import APIUsageLog
        from products.models import Category, Product

        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )

        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.return_value = _tool_use_response(
            "classify_sentiment", {"sentiment": "positive"}, input_tokens=42, output_tokens=7
        )
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        classify_sentiment("Anything", product_id=product.id)

        log = APIUsageLog.objects.get()
        assert log.product_id == product.id
        assert log.provider == APIUsageLog.Provider.ANTHROPIC
        assert log.action == "classify_sentiment"
        assert log.input_tokens == 42
        assert log.output_tokens == 7


@pytest.mark.django_db
class TestSummarizeProsConsCategoryContext:
    def test_prompt_includes_household_aspect_hints(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.return_value = _tool_use_response(
            "summarize_pros_cons", {"pros": "Solid build", "cons": "Heavy"}
        )
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        result = summarize_pros_cons(
            "Tefal Ingenio", ["Great pot", "Too heavy"], main_category="household"
        )

        assert result == {"pros": "Solid build", "cons": "Heavy"}
        prompt_sent = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "a household product" in prompt_sent
        assert "functionality" in prompt_sent

    def test_prompt_includes_cosmetics_aspect_hints(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.return_value = _tool_use_response(
            "summarize_pros_cons", {"pros": "Absorbs fast", "cons": "Strong scent"}
        )
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        result = summarize_pros_cons(
            "Nivea Creme", ["Absorbs fast", "Smells too strong"], main_category="cosmetics"
        )

        assert result == {"pros": "Absorbs fast", "cons": "Strong scent"}
        prompt_sent = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "a cosmetics product" in prompt_sent
        assert "allergies" in prompt_sent

    def test_skips_when_no_reviews(self, settings):
        settings.ANTHROPIC_API_KEY = "test-key"
        assert summarize_pros_cons("Any Product", [], main_category="electronics") is None

    def test_returns_none_on_permanent_error(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.side_effect = _status_error(anthropic.BadRequestError, 400)
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        assert summarize_pros_cons("Any Product", ["text"]) is None


@pytest.mark.django_db
class TestAnalyzeProductReviewsUsesProductCategory:
    def test_passes_product_main_category_to_sentiment_and_summarizer(self, mocker):
        from products.models import Category, Product
        from reviews.models import Review, ReviewSource

        from .tasks import analyze_product_reviews

        category = Category.objects.create(
            name="Pielegnacja", main_category=Category.MainCategory.COSMETICS
        )
        product = Product.objects.create(
            name="Krem", category=category, brand="Nivea", model_name="Creme"
        )
        source = ReviewSource.objects.create(
            name="Reddit",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit",
        )
        Review.objects.create(
            product=product,
            source=source,
            raw_text="Great cream",
            source_url="https://reddit.com/1",
            source_url_hash="hash1",
        )

        classify_mock = mocker.patch(
            "analysis.tasks.classify_sentiment", return_value="positive"
        )
        summarize_mock = mocker.patch(
            "analysis.tasks.summarize_pros_cons",
            return_value={"pros": "Nice", "cons": "None"},
        )

        analyze_product_reviews(product.id)

        classify_mock.assert_called_once_with(
            "Great cream", main_category="cosmetics", product_id=product.id
        )
        summarize_mock.assert_called_once_with(
            "Nivea Creme", ["Great cream"], main_category="cosmetics", product_id=product.id
        )

    def test_skips_review_still_within_cooldown(self, settings, mocker):
        from products.models import Category, Product
        from reviews.models import Review, ReviewSource

        from .tasks import analyze_product_reviews

        settings.SENTIMENT_RETRY_COOLDOWN_MINUTES = 15
        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        source = ReviewSource.objects.create(
            name="Reddit",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit",
        )
        review = Review.objects.create(
            product=product,
            source=source,
            raw_text="Great laptop",
            source_url="https://reddit.com/1",
            source_url_hash="hash1",
        )
        Review.objects.filter(pk=review.pk).update(
            sentiment_last_attempt_at=timezone.now() - timedelta(minutes=5)
        )

        classify_mock = mocker.patch("analysis.tasks.classify_sentiment")
        mocker.patch("analysis.tasks.summarize_pros_cons", return_value=None)

        analyze_product_reviews(product.id)

        classify_mock.assert_not_called()

    def test_retries_review_past_cooldown(self, settings, mocker):
        from products.models import Category, Product
        from reviews.models import Review, ReviewSource

        from .tasks import analyze_product_reviews

        settings.SENTIMENT_RETRY_COOLDOWN_MINUTES = 15
        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        source = ReviewSource.objects.create(
            name="Reddit",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit",
        )
        review = Review.objects.create(
            product=product,
            source=source,
            raw_text="Great laptop",
            source_url="https://reddit.com/1",
            source_url_hash="hash1",
        )
        Review.objects.filter(pk=review.pk).update(
            sentiment_last_attempt_at=timezone.now() - timedelta(minutes=20)
        )

        classify_mock = mocker.patch("analysis.tasks.classify_sentiment", return_value="positive")
        mocker.patch("analysis.tasks.summarize_pros_cons", return_value=None)

        analyze_product_reviews(product.id)

        classify_mock.assert_called_once()
        review.refresh_from_db()
        assert review.sentiment == "positive"

    def test_failed_attempt_sets_last_attempt_at_without_sentiment(self, mocker):
        from products.models import Category, Product
        from reviews.models import Review, ReviewSource

        from .tasks import analyze_product_reviews

        category = Category.objects.create(name="Laptopy")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        source = ReviewSource.objects.create(
            name="Reddit",
            source_type=ReviewSource.SourceType.REDDIT,
            base_url="https://www.reddit.com",
            parser_key="reddit",
        )
        review = Review.objects.create(
            product=product,
            source=source,
            raw_text="Great laptop",
            source_url="https://reddit.com/1",
            source_url_hash="hash1",
        )

        mocker.patch("analysis.tasks.classify_sentiment", return_value=None)
        mocker.patch("analysis.tasks.summarize_pros_cons", return_value=None)

        analyze_product_reviews(product.id)

        review.refresh_from_db()
        assert review.sentiment is None
        assert review.sentiment_last_attempt_at is not None


@pytest.mark.django_db
class TestAskAboutProduct:
    def test_returns_claude_text_response(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.return_value = _text_response(
            "  To solidny laptop biznesowy.  "
        )
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        result = ask_about_product("Dell", "XPS 13", "electronics")

        assert result == "To solidny laptop biznesowy."
        prompt_sent = fake_client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert "Dell XPS 13" in prompt_sent
        assert "an electronics product" in prompt_sent

    def test_returns_none_when_api_key_missing(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        assert ask_about_product("Dell", "XPS 13", "electronics") is None

    def test_returns_none_when_transient_retries_exhausted(self, settings, mocker):
        mocker.patch("analysis.retry.time.sleep")
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.side_effect = _connection_error()
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        assert ask_about_product("Dell", "XPS 13", "electronics") is None

    def test_raises_permanent_api_error(self, settings, mocker):
        settings.ANTHROPIC_API_KEY = "test-key"
        fake_client = mocker.MagicMock()
        fake_client.messages.create.side_effect = _status_error(anthropic.AuthenticationError, 401)
        mocker.patch("analysis.sentiment.anthropic.Anthropic", return_value=fake_client)

        with pytest.raises(PermanentAPIError):
            ask_about_product("Dell", "XPS 13", "electronics")


@pytest.mark.django_db
class TestGenerateProductAiOverview:
    def _product(self):
        from products.models import Category, Product

        category = Category.objects.create(
            name="Laptopy", main_category=Category.MainCategory.ELECTRONICS
        )
        return Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )

    def test_saves_successful_overview(self, mocker):
        from analysis.tasks import generate_product_ai_overview

        product = self._product()
        mocker.patch("analysis.tasks.ask_about_product", return_value="Świetny laptop.")

        generate_product_ai_overview(product.id)

        product.refresh_from_db()
        assert product.ai_overview == "Świetny laptop."

    def test_permanent_error_saves_fallback_message(self, mocker):
        from analysis.tasks import FALLBACK_OVERVIEW_MESSAGE, generate_product_ai_overview

        product = self._product()
        Product = product.__class__
        Product.objects.filter(pk=product.pk).update(ai_overview_requested_at=timezone.now())
        requested_at_before = Product.objects.get(pk=product.pk).ai_overview_requested_at

        mocker.patch("analysis.tasks.ask_about_product", side_effect=PermanentAPIError("bad key"))

        generate_product_ai_overview(product.id)

        product.refresh_from_db()
        assert product.ai_overview == FALLBACK_OVERVIEW_MESSAGE
        assert product.ai_overview_requested_at == requested_at_before

    def test_transient_error_clears_requested_at_for_retry(self, mocker):
        from analysis.tasks import generate_product_ai_overview

        product = self._product()
        Product = product.__class__
        Product.objects.filter(pk=product.pk).update(ai_overview_requested_at=timezone.now())

        mocker.patch("analysis.tasks.ask_about_product", return_value=None)

        generate_product_ai_overview(product.id)

        product.refresh_from_db()
        assert product.ai_overview == ""
        assert product.ai_overview_requested_at is None


@pytest.mark.django_db
class TestQueueAiOverview:
    def _product(self):
        from products.models import Category, Product

        category = Category.objects.create(
            name="Laptopy", main_category=Category.MainCategory.ELECTRONICS
        )
        return Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )

    def test_marks_requested_on_successful_enqueue(self, mocker):
        from analysis.tasks import generate_product_ai_overview, queue_ai_overview

        product = self._product()
        mocker.patch.object(generate_product_ai_overview, "delay")

        queue_ai_overview(product)

        product.refresh_from_db()
        assert product.ai_overview_requested_at is not None

    def test_does_not_mark_requested_when_broker_fails(self, mocker):
        from analysis.tasks import generate_product_ai_overview, queue_ai_overview

        product = self._product()
        mocker.patch.object(
            generate_product_ai_overview, "delay", side_effect=ConnectionError("broker down")
        )

        queue_ai_overview(product)  # must not raise

        product.refresh_from_db()
        assert product.ai_overview_requested_at is None


@pytest.mark.django_db
class TestLogApiUsage:
    def test_creates_row_with_provider_and_units(self):
        from analysis.models import APIUsageLog
        from analysis.usage import log_api_usage

        log_api_usage(APIUsageLog.Provider.YOUTUBE, "fetch_reviews", units=1)

        log = APIUsageLog.objects.get()
        assert log.provider == APIUsageLog.Provider.YOUTUBE
        assert log.action == "fetch_reviews"
        assert log.units == 1
        assert log.input_tokens is None

    def test_swallows_write_failure(self, mocker):
        from analysis.usage import log_api_usage

        mocker.patch(
            "analysis.usage.APIUsageLog.objects.create", side_effect=Exception("db down")
        )

        log_api_usage("youtube", "fetch_reviews")  # must not raise


@pytest.mark.django_db
class TestTrackApiUsage:
    def test_logs_on_success(self):
        from analysis.models import APIUsageLog
        from analysis.usage import track_api_usage

        with track_api_usage(APIUsageLog.Provider.REDDIT, "fetch_reviews"):
            pass

        assert APIUsageLog.objects.filter(provider=APIUsageLog.Provider.REDDIT).exists()

    def test_logs_even_when_block_raises(self):
        from analysis.models import APIUsageLog
        from analysis.usage import track_api_usage

        with pytest.raises(ValueError):
            with track_api_usage(APIUsageLog.Provider.CRAWLBASE, "fetch_reddit_post"):
                raise ValueError("boom")

        assert APIUsageLog.objects.filter(provider=APIUsageLog.Provider.CRAWLBASE).exists()


@pytest.mark.django_db
class TestCheckDailyBudget:
    def test_logs_critical_when_budget_exceeded(self, settings, caplog):
        from analysis.models import APIUsageLog
        from analysis.usage import log_api_usage

        settings.DAILY_PROVIDER_BUDGETS = {"youtube": 2}

        with caplog.at_level("CRITICAL"):
            log_api_usage(APIUsageLog.Provider.YOUTUBE, "fetch_reviews", units=1)
            log_api_usage(APIUsageLog.Provider.YOUTUBE, "fetch_reviews", units=1)
            log_api_usage(APIUsageLog.Provider.YOUTUBE, "fetch_reviews", units=1)

        assert any("Daily API budget exceeded" in message for message in caplog.messages)

    def test_no_alert_without_configured_budget(self, settings, caplog):
        from analysis.models import APIUsageLog
        from analysis.usage import log_api_usage

        settings.DAILY_PROVIDER_BUDGETS = {}

        with caplog.at_level("CRITICAL"):
            log_api_usage(APIUsageLog.Provider.YOUTUBE, "fetch_reviews", units=1000)

        assert not any("Daily API budget exceeded" in message for message in caplog.messages)

    def test_anthropic_budget_sums_input_and_output_tokens(self, settings, caplog):
        from analysis.models import APIUsageLog
        from analysis.usage import log_api_usage

        settings.DAILY_PROVIDER_BUDGETS = {"anthropic": 100}

        with caplog.at_level("CRITICAL"):
            log_api_usage(
                APIUsageLog.Provider.ANTHROPIC, "classify_sentiment",
                input_tokens=60, output_tokens=60,
            )

        assert any("Daily API budget exceeded" in message for message in caplog.messages)


@pytest.mark.django_db
class TestFlagSummary:
    def _product(self):
        from products.models import Category, Product

        category = Category.objects.create(name="Laptopy-flag")
        product = Product.objects.create(
            name="XPS 13", category=category, brand="Dell", model_name="XPS 13"
        )
        Product.objects.filter(pk=product.pk).update(
            pros_summary="Great screen", cons_summary="Pricey"
        )
        return Product.objects.get(pk=product.pk)

    def _login(self):
        from django.contrib.auth import get_user_model
        from django.test import Client

        user = get_user_model().objects.create_user(username="flagger", password="x")
        client = Client()
        client.force_login(user)
        return client, user

    def test_anonymous_is_redirected_to_login(self):
        from django.test import Client

        from .models import SummaryFlag

        product = self._product()
        client = Client()

        response = client.post(f"/products/{product.id}/flag-summary/", {"target": "pros"})

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert not SummaryFlag.objects.exists()

    def test_valid_flag_is_created(self):
        from .models import SummaryFlag

        product = self._product()
        client, user = self._login()

        response = client.post(f"/products/{product.id}/flag-summary/", {"target": "pros"})

        assert response.status_code == 302
        assert SummaryFlag.objects.filter(product=product, user=user, target="pros").exists()

    def test_repeated_flag_does_not_duplicate(self):
        from .models import SummaryFlag

        product = self._product()
        client, user = self._login()

        client.post(f"/products/{product.id}/flag-summary/", {"target": "cons"})
        client.post(f"/products/{product.id}/flag-summary/", {"target": "cons"})

        assert SummaryFlag.objects.filter(product=product, user=user, target="cons").count() == 1

    def test_invalid_target_is_rejected(self):
        from .models import SummaryFlag

        product = self._product()
        client, _ = self._login()

        response = client.post(f"/products/{product.id}/flag-summary/", {"target": "garbage"})

        assert response.status_code == 400
        assert not SummaryFlag.objects.exists()
