import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analysis", "0001_initial"),
        ("products", "0004_product_ai_overview"),
    ]

    operations = [
        migrations.RenameModel(old_name="AIUsageLog", new_name="APIUsageLog"),
        migrations.RenameField(
            model_name="apiusagelog", old_name="task_name", new_name="action"
        ),
        migrations.AlterField(
            model_name="apiusagelog",
            name="input_tokens",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="apiusagelog",
            name="output_tokens",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="apiusagelog",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="api_usage_logs",
                to="products.product",
            ),
        ),
        migrations.AddField(
            model_name="apiusagelog",
            name="provider",
            # Every existing row predates multi-provider tracking and was an
            # Anthropic call -- backfill accordingly, then drop the default
            # (new rows always pass provider explicitly via analysis.usage).
            field=models.CharField(
                max_length=20,
                choices=[
                    ("anthropic", "Anthropic"),
                    ("youtube", "YouTube"),
                    ("google_places", "Google Places"),
                    ("crawlbase", "Crawlbase"),
                    ("reddit", "Reddit"),
                ],
                default="anthropic",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="apiusagelog",
            name="units",
            field=models.PositiveIntegerField(default=1),
        ),
    ]
