"""
Category-specific context injected into the Claude prompts in sentiment.py
and summarizer.py. Both keep a single shared output format (sentiment.py:
{"sentiment": ...}; summarizer.py: {"pros": ..., "cons": ...}) regardless of
category -- only the prompt wording changes, matched to
products.models.Category.MainCategory. Unknown/blank main_category values
fall back to "electronics" (the original, pre-multi-category behavior).

Keyed by plain strings rather than Category.MainCategory itself to avoid
pulling the products app into analysis for what's just a label lookup.
"""

CATEGORY_LABELS = {
    "electronics": "an electronics product",
    "cosmetics": "a cosmetics product",
    "household": "a household product",
}

# What a review most often praises/criticizes for each category -- steers
# the aggregate pros/cons summary toward the aspects that actually matter,
# e.g. skin reaction/scent for cosmetics vs. durability/performance for
# electronics.
CATEGORY_ASPECT_HINTS = {
    "electronics": "durability, performance, and build quality",
    "cosmetics": "skin/hair reaction, scent, allergies, and overall effectiveness",
    "household": "durability, functionality, ease of use, and value for money",
}


def category_label(main_category: str) -> str:
    return CATEGORY_LABELS.get(main_category, CATEGORY_LABELS["electronics"])


def category_aspect_hints(main_category: str) -> str:
    return CATEGORY_ASPECT_HINTS.get(main_category, CATEGORY_ASPECT_HINTS["electronics"])
