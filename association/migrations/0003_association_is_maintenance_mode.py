from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("association", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="association",
            name="is_maintenance_mode",
            field=models.BooleanField(default=False),
        ),
    ]
