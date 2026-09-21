"""
Per-review sentiment classification via the Anthropic API.
"""

import logging

import anthropic
from django.conf import settings

from .prompts import category_label
from .retry import PermanentAPIError, call_with_retry

logger = logging.getLogger(__name__)

VALID_SENTIMENTS = {"positive", "neutral", "negative"}

_SENTIMENT_PROMPT = """You are classifying a single user review/comment about {category}.

Review text:
\"\"\"
{text}
\"\"\"
"""

SENTIMENT_TOOL = {
    "name": "classify_sentiment",
    "description": "Record the sentiment of a single review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment": {
                "type": "string",
                "enum": ["positive", "neutral", "negative"],
            },
        },
        "required": ["sentiment"],
    },
}


def _client() -> anthropic.Anthropic:
    # Identity-linked API keys (Anthropic Console SSO/workspace-scoped keys)
    # reject requests without an anthropic-workspace-id header; regular
    # keys ignore it, so only attach it when configured.
    headers = {"anthropic-workspace-id": settings.ANTHROPIC_WORKSPACE_ID} if settings.ANTHROPIC_WORKSPACE_ID else None
    # max_retries=0: retry/backoff is handled entirely by analysis.retry.call_with_retry,
    # not the SDK's own opaque retry logic -- we need per-attempt logging and
    # different handling per error category (see call_with_retry).
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY, default_headers=headers, timeout=30.0, max_retries=0
    )


def _extract_tool_input(response, tool_name: str) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


def classify_sentiment(
    text: str, main_category: str = "electronics", product_id: int | None = None
) -> str | None:
    """Return "positive"/"neutral"/"negative", or None if classification failed."""
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set; skipping sentiment classification.")
        return None

    prompt = _SENTIMENT_PROMPT.format(category=category_label(main_category), text=text[:4000])

    def make_request():
        return _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=50,
            temperature=0,
            tools=[SENTIMENT_TOOL],
            tool_choice={"type": "tool", "name": SENTIMENT_TOOL["name"]},
            messages=[{"role": "user", "content": prompt}],
        )

    try:
        response = call_with_retry(
            make_request, task_name="classify_sentiment", product_id=product_id
        )
    except PermanentAPIError:
        # classify_sentiment's callers don't need to distinguish this from a
        # transient failure (see analyze_product_reviews's cooldown, which
        # treats every failure the same) -- just report "no result" either way.
        return None

    if response is None:
        return None

    try:
        tool_input = _extract_tool_input(response, SENTIMENT_TOOL["name"])
        sentiment = tool_input.get("sentiment") if tool_input else None
    except (AttributeError, IndexError) as exc:
        logger.warning("Sentiment classification response was malformed: %s", exc)
        return None

    return sentiment if sentiment in VALID_SENTIMENTS else None
