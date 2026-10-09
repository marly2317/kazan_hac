from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"
    verbose_name = "Каталог"
    default_auto_field = "django.db.models.BigAutoField"
