from django.apps import AppConfig


class SteConfig(AppConfig):
    name = "apps.ste"
    label = "ste"
    verbose_name = "Подбор КТРУ и автозаполнение СТЕ"
    default_auto_field = "django.db.models.BigAutoField"
