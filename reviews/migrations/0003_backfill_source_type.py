from django.db import migrations

# Old choices were "api" (always meant Reddit in this codebase) and
# "scraping" (always meant a forum parser). New choices split by parser kind
# instead of fetch mechanism -- see ReviewSource.SourceType.
SOURCE_TYPE_MAP = {
    "api": "reddit",
    "scraping": "forum",
}


def backfill_source_type(apps, schema_editor):
    ReviewSource = apps.get_model("reviews", "ReviewSource")
    for old_value, new_value in SOURCE_TYPE_MAP.items():
        ReviewSource.objects.filter(source_type=old_value).update(source_type=new_value)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0002_reviewsource_multi_source"),
    ]

    operations = [
        migrations.RunPython(backfill_source_type, noop_reverse),
    ]
