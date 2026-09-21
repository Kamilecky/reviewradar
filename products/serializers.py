from rest_framework import serializers

from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "main_category", "spec_schema"]


class ProductListSerializer(serializers.ModelSerializer):
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Product
        fields = ["id", "name", "category", "brand", "model_name", "created_at"]


class ProductDetailSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "category",
            "brand",
            "model_name",
            "specification",
            "pros_summary",
            "cons_summary",
            "summary_updated_at",
            "created_at",
        ]
