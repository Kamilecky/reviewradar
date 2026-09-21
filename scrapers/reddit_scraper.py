"""
Reddit review parser (official API via PRAW).

PRAW/prawcore already throttle requests to Reddit's published rate limits and
raise prawcore.exceptions.RequestException / ResponseException on failures
(e.g. 429s) rather than silently retrying forever -- we catch those instead of
letting an unhandled exception kill the Celery task.
"""

import logging
from datetime import datetime, timezone

import praw
import prawcore
from django.conf import settings

from .base import FetchedReview

logger = logging.getLogger(__name__)


def _to_datetime(created_utc: float) -> datetime:
    return datetime.fromtimestamp(created_utc, tz=timezone.utc)


def get_reddit_client() -> praw.Reddit:
    return praw.Reddit(
        client_id=settings.REDDIT_CLIENT_ID,
        client_secret=settings.REDDIT_CLIENT_SECRET,
        user_agent=settings.REDDIT_USER_AGENT,
    )


def fetch(product_name: str, subreddits=None, limit=None, reddit_client=None):
    """
    Search the configured subreddits for submissions mentioning product_name
    and yield FetchedReview for the submission itself plus its top-level comments.
    """
    subreddits = subreddits or settings.REDDIT_SUBREDDITS
    limit = limit or settings.REDDIT_SEARCH_LIMIT
    reddit = reddit_client or get_reddit_client()

    subreddit_path = "+".join(subreddits)

    try:
        submissions = list(
            reddit.subreddit(subreddit_path).search(product_name, limit=limit)
        )
    except (prawcore.exceptions.RequestException, prawcore.exceptions.ResponseException) as exc:
        logger.warning("Reddit search failed for %r: %s", product_name, exc)
        return

    for submission in submissions:
        yield FetchedReview(
            source_url=f"https://www.reddit.com{submission.permalink}",
            raw_text=f"{submission.title}\n\n{submission.selftext}".strip(),
            author=str(submission.author) if submission.author else "",
            published_at=_to_datetime(submission.created_utc),
        )

        try:
            submission.comments.replace_more(limit=0)
            for comment in submission.comments.list():
                if not getattr(comment, "body", None):
                    continue
                yield FetchedReview(
                    source_url=f"https://www.reddit.com{comment.permalink}",
                    raw_text=comment.body,
                    author=str(comment.author) if comment.author else "",
                    published_at=_to_datetime(comment.created_utc),
                )
        except (prawcore.exceptions.RequestException, prawcore.exceptions.ResponseException) as exc:
            logger.warning("Failed to fetch comments for %s: %s", submission.id, exc)
            continue
