from types import SimpleNamespace
from unittest.mock import MagicMock

import requests

from scrapers import (
    crawlbase_reddit_scraper,
    generic_forum_scraper,
    google_places_scraper,
    reddit_scraper,
    youtube_scraper,
)


def _fake_submission(permalink, title, selftext, author, comments):
    submission = SimpleNamespace(
        permalink=permalink,
        title=title,
        selftext=selftext,
        author=author,
        created_utc=1_700_000_000,
    )
    submission.comments = MagicMock()
    submission.comments.replace_more = MagicMock()
    submission.comments.list = MagicMock(return_value=comments)
    return submission


def _fake_comment(permalink, body, author):
    return SimpleNamespace(permalink=permalink, body=body, author=author, created_utc=1_700_000_100)


class TestRedditScraperFetch:
    """
    Exercises scrapers.reddit_scraper.fetch() against a mocked PRAW client so
    this test never makes a real network call to Reddit's API.
    """

    def test_fetch_yields_submission_and_comments(self):
        submission = _fake_submission(
            permalink="/r/headphones/comments/abc123/great_review/",
            title="Loving these",
            selftext="Sound quality is excellent",
            author="redditor1",
            comments=[_fake_comment("/r/headphones/comments/abc123/great_review/c1/", "Agreed!", "redditor2")],
        )

        fake_reddit = MagicMock()
        fake_reddit.subreddit.return_value.search.return_value = [submission]

        results = list(
            reddit_scraper.fetch(
                "WH-1000XM5",
                subreddits=["headphones"],
                limit=5,
                reddit_client=fake_reddit,
            )
        )

        assert len(results) == 2
        assert results[0].source_url.endswith("/r/headphones/comments/abc123/great_review/")
        assert "Sound quality is excellent" in results[0].raw_text
        assert results[0].author == "redditor1"

        assert results[1].raw_text == "Agreed!"
        assert results[1].author == "redditor2"

        fake_reddit.subreddit.assert_called_once_with("headphones")
        fake_reddit.subreddit.return_value.search.assert_called_once_with("WH-1000XM5", limit=5)

    def test_fetch_handles_search_failure_gracefully(self):
        import prawcore

        fake_reddit = MagicMock()
        fake_reddit.subreddit.return_value.search.side_effect = prawcore.exceptions.ResponseException(
            MagicMock(status_code=429)
        )

        results = list(
            reddit_scraper.fetch(
                "WH-1000XM5", subreddits=["headphones"], limit=5, reddit_client=fake_reddit
            )
        )

        assert results == []


class TestGenericForumScraperFetch:
    """
    Exercises scrapers.generic_forum_scraper.fetch() against mocked HTTP
    responses -- proves selectors come from parser_config (not hardcoded),
    and that dedup.save_reviews's contract (plain FetchedReview objects)
    works identically regardless of which parser produced them.
    """

    def test_fetch_uses_custom_selectors_from_parser_config(self, mocker):
        mocker.patch("scrapers.generic_forum_scraper.time.sleep")

        search_html = '<html><body><a class="result-link" href="/thread/1">Thread</a></body></html>'
        thread_html = """
            <html><body>
                <article class="entry">
                    <div class="entry-text">Great product, highly recommend</div>
                    <span class="entry-author">Jan</span>
                </article>
            </body></html>
        """
        mock_get = mocker.patch(
            "scrapers.generic_forum_scraper.requests.get",
            side_effect=[MagicMock(text=search_html), MagicMock(text=thread_html)],
        )

        parser_config = {
            "thread_link_selector": "a.result-link[href]",
            "post_selector": "article.entry",
            "body_selector": ".entry-text",
            "author_selector": ".entry-author",
        }

        results = list(
            generic_forum_scraper.fetch(
                "WH-1000XM5", base_url="https://forum.example.com", parser_config=parser_config
            )
        )

        assert len(results) == 1
        assert results[0].raw_text == "Great product, highly recommend"
        assert results[0].author == "Jan"
        assert results[0].source_url == "https://forum.example.com/thread/1"
        assert mock_get.call_count == 2

    def test_fetch_falls_back_to_default_selectors(self, mocker):
        mocker.patch("scrapers.generic_forum_scraper.time.sleep")

        search_html = '<html><body><a class="thread-link" href="/thread/1">Thread</a></body></html>'
        thread_html = """
            <html><body>
                <div class="post">
                    <div class="post-body">Battery life is disappointing</div>
                    <span class="post-author">Ola</span>
                </div>
            </body></html>
        """
        mocker.patch(
            "scrapers.generic_forum_scraper.requests.get",
            side_effect=[MagicMock(text=search_html), MagicMock(text=thread_html)],
        )

        results = list(
            generic_forum_scraper.fetch("XPS 13", base_url="https://forum.example.com")
        )

        assert len(results) == 1
        assert results[0].raw_text == "Battery life is disappointing"
        assert results[0].author == "Ola"


class TestGooglePlacesScraperFetch:
    """
    Exercises scrapers.google_places_scraper.fetch() against mocked HTTP
    responses -- official REST API, no real network calls.
    """

    def test_fetch_resolves_place_id_then_returns_reviews(self, settings, mocker):
        settings.GOOGLE_PLACES_API_KEY = "test-key"
        text_search_response = MagicMock()
        text_search_response.json.return_value = {
            "status": "OK",
            "results": [{"place_id": "abc123"}],
        }
        details_response = MagicMock()
        details_response.json.return_value = {
            "status": "OK",
            "result": {
                "reviews": [
                    {"author_name": "Jan", "text": "Swietny sprzet", "time": 1700000000},
                ]
            },
        }
        mocker.patch(
            "scrapers.google_places_scraper.requests.get",
            side_effect=[text_search_response, details_response],
        )

        results = list(google_places_scraper.fetch("XPS 13"))

        assert len(results) == 1
        assert results[0].raw_text == "Swietny sprzet"
        assert results[0].author == "Jan"
        assert "abc123" in results[0].source_url

    def test_fetch_uses_place_id_from_parser_config_without_text_search(self, settings, mocker):
        settings.GOOGLE_PLACES_API_KEY = "test-key"
        details_response = MagicMock()
        details_response.json.return_value = {"status": "OK", "result": {"reviews": []}}
        mock_get = mocker.patch(
            "scrapers.google_places_scraper.requests.get", return_value=details_response
        )

        results = list(
            google_places_scraper.fetch("XPS 13", parser_config={"place_id": "known123"})
        )

        assert results == []
        assert mock_get.call_count == 1  # no text search performed

    def test_fetch_returns_empty_on_zero_results(self, settings, mocker):
        settings.GOOGLE_PLACES_API_KEY = "test-key"
        text_search_response = MagicMock()
        text_search_response.json.return_value = {"status": "ZERO_RESULTS", "results": []}
        mocker.patch(
            "scrapers.google_places_scraper.requests.get", return_value=text_search_response
        )

        results = list(google_places_scraper.fetch("Nonexistent product"))

        assert results == []

    def test_fetch_returns_empty_when_api_key_missing(self, settings):
        settings.GOOGLE_PLACES_API_KEY = ""
        assert list(google_places_scraper.fetch("XPS 13")) == []

    def test_fetch_returns_empty_on_request_exception(self, settings, mocker):
        settings.GOOGLE_PLACES_API_KEY = "test-key"
        mocker.patch(
            "scrapers.google_places_scraper.requests.get",
            side_effect=requests.RequestException("boom"),
        )

        results = list(google_places_scraper.fetch("XPS 13"))

        assert results == []


class TestYoutubeScraperFetch:
    """
    Exercises scrapers.youtube_scraper.fetch() against mocked HTTP
    responses -- official REST API, no real network calls.
    """

    def test_fetch_returns_comments_for_matching_videos(self, settings, mocker):
        settings.YOUTUBE_API_KEY = "test-key"
        search_response = MagicMock()
        search_response.json.return_value = {"items": [{"id": {"videoId": "vid1"}}]}
        comments_response = MagicMock()
        comments_response.json.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "comment1",
                            "snippet": {
                                "textDisplay": "Great review, very helpful",
                                "authorDisplayName": "Ola",
                                "publishedAt": "2024-01-01T12:00:00Z",
                            },
                        }
                    }
                }
            ]
        }
        mocker.patch(
            "scrapers.youtube_scraper.requests.get",
            side_effect=[search_response, comments_response],
        )

        results = list(youtube_scraper.fetch("XPS 13 review"))

        assert len(results) == 1
        assert results[0].raw_text == "Great review, very helpful"
        assert results[0].author == "Ola"
        assert results[0].source_url == "https://www.youtube.com/watch?v=vid1&lc=comment1"

    def test_fetch_respects_max_videos_and_max_comments_config(self, settings, mocker):
        settings.YOUTUBE_API_KEY = "test-key"
        search_response = MagicMock()
        search_response.json.return_value = {"items": [{"id": {"videoId": "vid1"}}]}
        comments_response = MagicMock()
        comments_response.json.return_value = {"items": []}
        mock_get = mocker.patch(
            "scrapers.youtube_scraper.requests.get",
            side_effect=[search_response, comments_response],
        )

        list(
            youtube_scraper.fetch(
                "XPS 13", parser_config={"max_videos": 1, "max_comments_per_video": 5}
            )
        )

        search_call = mock_get.call_args_list[0]
        assert search_call.kwargs["params"]["maxResults"] == 1
        comments_call = mock_get.call_args_list[1]
        assert comments_call.kwargs["params"]["maxResults"] == 5

    def test_fetch_skips_video_with_comments_disabled(self, settings, mocker):
        settings.YOUTUBE_API_KEY = "test-key"
        search_response = MagicMock()
        search_response.json.return_value = {
            "items": [{"id": {"videoId": "vid1"}}, {"id": {"videoId": "vid2"}}]
        }
        forbidden_response = MagicMock()
        forbidden_response.raise_for_status.side_effect = requests.HTTPError(
            "403 comments disabled"
        )
        ok_response = MagicMock()
        ok_response.json.return_value = {
            "items": [
                {
                    "snippet": {
                        "topLevelComment": {
                            "id": "c1",
                            "snippet": {
                                "textDisplay": "Works fine",
                                "authorDisplayName": "Kasia",
                                "publishedAt": "2024-01-01T12:00:00Z",
                            },
                        }
                    }
                }
            ]
        }
        mocker.patch(
            "scrapers.youtube_scraper.requests.get",
            side_effect=[search_response, forbidden_response, ok_response],
        )

        results = list(youtube_scraper.fetch("XPS 13"))

        assert len(results) == 1
        assert results[0].raw_text == "Works fine"

    def test_fetch_returns_empty_when_api_key_missing(self, settings):
        settings.YOUTUBE_API_KEY = ""
        assert list(youtube_scraper.fetch("XPS 13")) == []


class TestCrawlbaseRedditScraperFetch:
    """
    Exercises scrapers.crawlbase_reddit_scraper against mocked HTTP
    responses -- official third-party scraping API, no real network calls.
    """

    def _response(self, status_code=200, payload=None):
        response = MagicMock(status_code=status_code)
        response.json.return_value = payload or {}
        return response

    def test_fetch_reddit_post_returns_post_dict_on_success(self, settings, mocker):
        settings.CRAWLBASE_TOKEN = "test-token"
        payload = {
            "body": {
                "post": {
                    "id": "1w0p01k",
                    "title": "Just got the S25 Ultra",
                    "author": "u/someone",
                    "score": 120,
                    "createdAt": "2024-01-01T12:00:00Z",
                    "url": "https://www.reddit.com/r/Smartphones/comments/1w0p01k/",
                    "permalink": "/r/Smartphones/comments/1w0p01k/",
                    "selfText": "Second-guessing my purchase",
                }
            }
        }
        mocker.patch(
            "scrapers.crawlbase_reddit_scraper.requests.get",
            return_value=self._response(200, payload),
        )

        post = crawlbase_reddit_scraper.fetch_reddit_post(
            "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"
        )

        assert post is not None
        assert post["title"] == "Just got the S25 Ultra"
        assert post["score"] == 120

    def test_fetch_reddit_post_returns_none_when_required_fields_blank(self, settings, mocker):
        """Mirrors the exact (redacted) example payload shape from Crawlbase's docs."""
        settings.CRAWLBASE_TOKEN = "test-token"
        payload = {
            "original_status": 200,
            "pc_status": 200,
            "cb_status": 200,
            "url": "https://www.reddit.com/r/Smartphones/comments/1w0p01k/just_got_the_s25_ultra_and_im_secondguessing/",
            "domain_complexity": "standard",
            "body": {
                "post": {
                    "id": "",
                    "title": "",
                    "author": "",
                    "subreddit": "",
                    "score": None,
                    "upvoteRatio": None,
                    "commentsCount": None,
                    "createdAt": "",
                    "url": "",
                    "domain": "",
                    "permalink": "",
                    "flair": "",
                    "isNsfw": False,
                    "selfText": "",
                },
                "commentCount": 0,
                "comments": [],
            },
        }
        mocker.patch(
            "scrapers.crawlbase_reddit_scraper.requests.get",
            return_value=self._response(200, payload),
        )

        post = crawlbase_reddit_scraper.fetch_reddit_post(payload["url"])

        assert post is None

    def test_fetch_reddit_post_returns_none_on_non_200_status(self, settings, mocker):
        settings.CRAWLBASE_TOKEN = "test-token"
        mocker.patch(
            "scrapers.crawlbase_reddit_scraper.requests.get",
            return_value=self._response(status_code=503),
        )

        assert crawlbase_reddit_scraper.fetch_reddit_post("https://reddit.com/x") is None

    def test_fetch_reddit_post_returns_none_on_timeout(self, settings, mocker):
        settings.CRAWLBASE_TOKEN = "test-token"
        mocker.patch(
            "scrapers.crawlbase_reddit_scraper.requests.get",
            side_effect=requests.Timeout("timed out"),
        )

        assert crawlbase_reddit_scraper.fetch_reddit_post("https://reddit.com/x") is None

    def test_fetch_reddit_post_returns_none_when_token_missing(self, settings):
        settings.CRAWLBASE_TOKEN = ""
        assert crawlbase_reddit_scraper.fetch_reddit_post("https://reddit.com/x") is None

    def test_fetch_yields_fetched_review_from_post(self, settings, mocker):
        settings.CRAWLBASE_TOKEN = "test-token"
        payload = {
            "body": {
                "post": {
                    "id": "1w0p01k",
                    "title": "Just got the S25 Ultra",
                    "author": "u/someone",
                    "score": 120,
                    "createdAt": "2024-01-01T12:00:00Z",
                    "url": "https://www.reddit.com/r/Smartphones/comments/1w0p01k/",
                    "permalink": "/r/Smartphones/comments/1w0p01k/",
                    "selfText": "Second-guessing my purchase",
                }
            }
        }
        mocker.patch(
            "scrapers.crawlbase_reddit_scraper.requests.get",
            return_value=self._response(200, payload),
        )

        results = list(
            crawlbase_reddit_scraper.fetch(
                "Samsung S25 Ultra",
                parser_config={"url": "https://www.reddit.com/r/Smartphones/comments/1w0p01k/"},
            )
        )

        assert len(results) == 1
        assert "Just got the S25 Ultra" in results[0].raw_text
        assert "Second-guessing my purchase" in results[0].raw_text
        assert results[0].author == "u/someone"

    def test_fetch_returns_empty_without_url_in_parser_config(self, settings):
        settings.CRAWLBASE_TOKEN = "test-token"
        assert list(crawlbase_reddit_scraper.fetch("Samsung S25 Ultra")) == []
