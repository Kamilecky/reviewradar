"""
Direct product knowledge query to Claude -- distinct from sentiment.py and
summarizer.py, which analyze *collected reviews*. This asks Claude what it
already knows about a product from its own training data, as a plain
natural-language answer (not the structured JSON contract the other two
modules use), for the "Zapytanie do Claude" tab on the product detail page.
"""

import logging

from django.conf import settings

from .prompts import category_label
from .retry import call_with_retry
from .sentiment import _client

logger = logging.getLogger(__name__)

_OVERVIEW_PROMPT = """Jestem zainteresowany produktem: {brand} {model_name} \
({category}). Co o nim wiesz? Opisz krótko, czym jest, oraz jakie są jego \
główne zalety i wady w Twojej ocenie. Odpowiedz po polsku, zwięźle \
(maksymalnie 150 słów), zwykłym tekstem, bez formatowania Markdown."""


def ask_about_product(
    brand: str, model_name: str, main_category: str, product_id: int | None = None
) -> str | None:
    """
    Return Claude's own free-text answer about the product, or None if a
    transient error exhausted all retries.

    Deliberately does NOT catch analysis.retry.PermanentAPIError -- it
    propagates to analysis.tasks.generate_product_ai_overview, the only
    caller, which needs to tell a permanent (config) error apart from a
    transient one that simply ran out of retries (see that function).
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set; skipping product overview.")
        return None

    prompt = _OVERVIEW_PROMPT.format(
        brand=brand, model_name=model_name, category=category_label(main_category)
    )

    def make_request():
        return _client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=400,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

    response = call_with_retry(make_request, task_name="ask_about_product", product_id=product_id)
    if response is None:
        return None

    try:
        return response.content[0].text.strip()
    except (IndexError, AttributeError) as exc:
        logger.warning("Product overview response was malformed: %s", exc)
        return None
