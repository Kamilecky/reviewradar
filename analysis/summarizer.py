"""
Aggregate "most common pros / cons" summary for a product, generated from its
collected reviews via the Anthropic API.
"""

import logging

from django.conf import settings

from .prompts import category_aspect_hints, category_label
from .retry import PermanentAPIError, call_with_retry
from .sentiment import _client, _extract_tool_input

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """You are summarizing user reviews about {category}: {product_name}.

Below are excerpts from {count} reviews collected from online discussions. \
Identify the most frequently mentioned advantages and disadvantages, paying \
particular attention to {aspect_hints}.

Reviews:
\"\"\"
{reviews_text}
\"\"\"
"""

SUMMARY_TOOL = {
    "name": "summarize_pros_cons",
    "description": "Record the most common advantages and disadvantages mentioned across the reviews.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pros": {
                "type": "string",
                "description": "Short bullet-style summary of the most common advantages.",
            },
            "cons": {
                "type": "string",
                "description": "Short bullet-style summary of the most common disadvantages.",
            },
        },
        "required": ["pros", "cons"],
    },
}

MAX_REVIEWS_IN_PROMPT = 40
MAX_CHARS_PER_REVIEW = 500


def summarize_pros_cons(
    product_name: str,
    review_texts: list[str],
    main_category: str = "electronics",
    product_id: int | None = None,
) -> dict | None:
    """Return {"pros": str, "cons": str}, or None if summarization failed/skipped."""
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set; skipping pros/cons summary.")
        return None
    if not review_texts:
        return None

    sample = review_texts[:MAX_REVIEWS_IN_PROMPT]
    reviews_text = "\n---\n".join(text[:MAX_CHARS_PER_REVIEW] for text in sample)
    prompt = _SUMMARY_PROMPT.format(
        category=category_label(main_category),
        product_name=product_name,
        count=len(sample),
        aspect_hints=category_aspect_hints(main_category),
        reviews_text=reviews_text,
    )

    def make_request():
        return _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=500,
            temperature=0,
            tools=[SUMMARY_TOOL],
            tool_choice={"type": "tool", "name": SUMMARY_TOOL["name"]},
            messages=[{"role": "user", "content": prompt}],
        )

    try:
        response = call_with_retry(
            make_request, task_name="summarize_pros_cons", product_id=product_id
        )
    except PermanentAPIError:
        # No caller of summarize_pros_cons needs to distinguish this from a
        # transient failure -- report "no result" either way.
        return None

    if response is None:
        return None

    try:
        payload = _extract_tool_input(response, SUMMARY_TOOL["name"])
    except (AttributeError, IndexError) as exc:
        logger.warning("Pros/cons summarization response was malformed: %s", exc)
        return None

    if not payload or "pros" not in payload or "cons" not in payload:
        return None
    return {"pros": payload["pros"], "cons": payload["cons"]}
