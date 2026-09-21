from rest_framework import serializers

from .models import Review, ReviewSource


class ReviewSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewSource
        fields = ["id", "name", "source_type", "base_url"]


class ReviewSerializer(serializers.ModelSerializer):
    source = ReviewSourceSerializer(read_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "product",
            "source",
            "author",
            "raw_text",
            "summary",
            "sentiment",
            "source_url",
            "published_at",
            "fetched_at",
        ]
