from django.db import migrations

# Copied literally (not imported from products/spec_schemas.py) so this
# migration stays correct even if the app's default schema template changes
# later -- migrations should not depend on app code that can drift over time.
ELECTRONICS_SCHEMA = {
    "type": "object",
    "properties": {
        "technical_parameters": {
            "type": "object",
            "description": "Free-form key/value technical specs, e.g. {'RAM': '16GB', 'CPU': '...'}",
        },
        "warranty_months": {"type": "integer", "minimum": 0},
        "compatibility": {"type": "string"},
    },
    "additionalProperties": True,
}


def backfill_electronics(apps, schema_editor):
    """
    Every Category that existed before multi-category support was added is,
    by definition, electronics -- this app only handled electronics until now.
    """
    Category = apps.get_model("products", "Category")
    Category.objects.filter(spec_schema={}).update(
        main_category="electronics", spec_schema=ELECTRONICS_SCHEMA
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0002_category_main_category_spec_schema"),
    ]

    operations = [
        migrations.RunPython(backfill_electronics, noop_reverse),
    ]
