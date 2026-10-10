import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="SteSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("data", models.JSONField(default=dict, verbose_name="состояние")),
                ("created", models.DateTimeField(auto_now_add=True, verbose_name="создана")),
                (
                    "updated",
                    models.DateTimeField(auto_now=True, db_index=True, verbose_name="обновлена"),
                ),
            ],
            options={
                "verbose_name": "сессия подбора КТРУ",
                "verbose_name_plural": "сессии подбора КТРУ",
            },
        ),
    ]
