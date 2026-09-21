import json

from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    """Standard admin for Category; auto-fills `slug` from `name` in the form."""

    list_display = ("name", "main_category", "slug")
    list_filter = ("main_category",)
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Admin for Product; injects each category's JSON Schema for the live spec-field hint JS."""

    list_display = ("name", "brand", "model_name", "category", "summary_updated_at", "created_at")
    list_filter = ("category", "brand")
    search_fields = ("name", "brand", "model_name")
    readonly_fields = ("summary_updated_at", "created_at")
    change_form_template = "admin/products/product/change_form.html"

    class Media:
        """Loads the client-side script that renders the spec_schema hint on category change."""

        js = ("products/admin_spec_schema.js",)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Embed every category's spec_schema as JSON so the admin JS can hint expected fields."""
        extra_context = extra_context or {}
        schema_map = {
            category.id: category.spec_schema for category in Category.objects.all()
        }
        extra_context["category_schema_map_json"] = json.dumps(schema_map)
        return super().changeform_view(request, object_id, form_url, extra_context)
