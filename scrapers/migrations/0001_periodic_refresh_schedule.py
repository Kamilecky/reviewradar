from django.db import migrations


def create_periodic_task(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = IntervalSchedule.objects.get_or_create(every=1, period="days")
    PeriodicTask.objects.get_or_create(
        name="Refresh reviews for recently added products",
        defaults={
            "interval": schedule,
            "task": "scrapers.tasks.refresh_recent_products",
        },
    )


def remove_periodic_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(
        name="Refresh reviews for recently added products"
    ).delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_periodic_task, remove_periodic_task),
    ]
